"""
Unified notifier — sends both email (HTML) and SMS (via email-to-SMS gateway).
Both use the Gmail API; no Twilio needed.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Resolve paths relative to the project root (parent of this file's package dir)
_PKG_DIR = Path(__file__).parent
_PROJECT_DIR = _PKG_DIR.parent

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]
CREDS_FILE = str(_PROJECT_DIR / "credentials.json")
TOKEN_FILE = str(_PROJECT_DIR / "token.json")


def get_credentials() -> Credentials:
    """Load (or refresh/create) OAuth credentials covering Gmail + Calendar scopes."""
    import sys
    creds: Optional[Credentials] = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        # If the saved token is missing the calendar scope, force re-auth
        if creds.scopes and not set(SCOPES).issubset(set(creds.scopes)):
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_FILE):
                print(
                    f"Error: credentials.json not found in {_PROJECT_DIR}.\n"
                    "  The file may be missing or named incorrectly.\n"
                    "  Rename your Google OAuth credentials file to exactly: credentials.json"
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_gmail_service():
    return build("gmail", "v1", credentials=get_credentials())


class Notifier:
    def __init__(self) -> None:
        self.service = get_gmail_service()

    # ------------------------------------------------------------------
    # Email (rich HTML)
    # ------------------------------------------------------------------

    def send_email(self, to_address: str, deadlines: list[dict], lookahead_days: int) -> str:
        """Send a formatted HTML email reminder. Returns message ID."""
        subject, html_body, plain_body = _build_email(deadlines, lookahead_days)
        return self._send(to_address, subject, plain_body, html_body)

    # ------------------------------------------------------------------
    # SMS via email-to-SMS gateway (plain text only, kept short)
    # ------------------------------------------------------------------

    def send_sms(self, sms_email: str, deadlines: list[dict], lookahead_days: int) -> str:
        """
        Send an SMS by emailing the carrier's SMS gateway address.
        e.g. sms_email = '1234567890@tmomail.net'
        Returns message ID.
        """
        body = _build_sms(deadlines, lookahead_days)
        # SMS gateway ignores subject, but set a short one just in case
        return self._send(sms_email, subject="Canvas", plain=body, html=None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, to: str, subject: str, plain: str, html: Optional[str]) -> str:
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(plain, "plain"))
            msg.attach(MIMEText(html, "html"))
        else:
            msg = MIMEText(plain, "plain")

        msg["Subject"] = subject
        msg["To"] = to

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        # Retry up to 3 times on temporary failures (4xx/5xx)
        for attempt in range(3):
            try:
                result = self.service.users().messages().send(
                    userId="me", body={"raw": raw}
                ).execute()
                return result["id"]
            except HttpError as e:
                if attempt < 2 and e.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                raise


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _build_email(deadlines: list[dict], lookahead_days: int) -> tuple[str, str, str]:
    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(days=lookahead_days)
    today_str = now.astimezone().strftime("%A, %B %d %Y")
    upcoming = [d for d in deadlines if now <= d["due_at"] <= cutoff]

    if not upcoming:
        subject = f"Canvas Reminder — No deadlines in the next {lookahead_days} days"
        plain = "No assignments due. You're all caught up!"
        html = f"<p>{plain}</p>"
        return subject, html, plain

    due_today = [d for d in upcoming if (d["due_at"].astimezone().date() - now.astimezone().date()).days == 0]
    subject = (
        f"⚠️ Canvas — {len(due_today)} due TODAY ({today_str})"
        if due_today else
        f"📚 Canvas — {len(upcoming)} upcoming deadline(s) ({today_str})"
    )

    # Plain text
    plain_lines = [f"Canvas Reminder — {today_str}", "=" * 40, ""]
    for item in upcoming:
        local_due = item["due_at"].astimezone()
        days_left = (local_due.date() - now.astimezone().date()).days
        due_str = local_due.strftime("%a %b %d at %I:%M %p")
        urgency = "DUE TODAY" if days_left == 0 else ("Due tomorrow" if days_left == 1 else f"Due in {days_left}d")
        submitted_str = " ✓ submitted" if item.get("submitted") else ""
        plain_lines += [f"[{urgency}]{submitted_str} {item.get('course', '')} — {item['name']}", f"  Due: {due_str}", ""]
    plain = "\n".join(plain_lines)

    # HTML
    rows = ""
    for item in upcoming:
        local_due = item["due_at"].astimezone()
        days_left = (local_due.date() - now.astimezone().date()).days
        due_str = local_due.strftime("%a %b %d at %I:%M %p")

        if days_left == 0:
            urgency_html = '<span style="color:#cc0000;font-weight:bold;">DUE TODAY</span>'
            row_bg = "#fff3f3"
        elif days_left == 1:
            urgency_html = '<span style="color:#e07000;font-weight:bold;">Due tomorrow</span>'
            row_bg = "#fff8f0"
        else:
            urgency_html = f'<span style="color:#555;">In {days_left}d</span>'
            row_bg = "#ffffff"

        url = item.get("url", "")
        name = item["name"]
        name_html = f'<a href="{url}" style="color:#1a73e8;">{name}</a>' if url else name
        submitted_html = ' <span style="color:#2e7d32;font-weight:bold;">✓ submitted</span>' if item.get("submitted") else ""

        rows += f"""
        <tr style="background:{row_bg};">
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">{urgency_html}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#555;">{item.get('course','')}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">{name_html}{submitted_html}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#555;">{due_str}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;color:#333;">
  <h2 style="color:#1a73e8;">📚 Canvas Reminder</h2>
  <p style="color:#555;">{today_str}</p>
  <table style="width:100%;border-collapse:collapse;margin-top:16px;">
    <thead><tr style="background:#f1f3f4;">
      <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;">Status</th>
      <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;">Course</th>
      <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;">Assignment</th>
      <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #ddd;">Due</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="margin-top:24px;font-size:12px;color:#aaa;">Sent by canvas-manager</p>
</body></html>"""

    return subject, html, plain


def _build_sms(deadlines: list[dict], lookahead_days: int) -> str:
    """Build a short plain-text SMS body (target: under 320 chars)."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(days=lookahead_days)
    upcoming = [d for d in deadlines if now <= d["due_at"] <= cutoff]

    if not upcoming:
        return f"Canvas: No deadlines in {lookahead_days}d. All caught up!"

    today_str = now.astimezone().strftime("%m/%d")
    lines = [f"Canvas ({today_str}):"]
    for item in upcoming:
        local_due = item["due_at"].astimezone()
        days_left = (local_due.date() - now.astimezone().date()).days
        urgency = "TODAY" if days_left == 0 else ("tmrw" if days_left == 1 else f"{days_left}d")
        course = item.get("course", "")[:8]
        name = item["name"][:22] + ("..." if len(item["name"]) > 22 else "")
        due_str = local_due.strftime("%m/%d %I%p").lower()
        lines.append(f"[{urgency}] {course}: {name} ({due_str})")

    body = "\n".join(lines)
    return body[:320] + "..." if len(body) > 320 else body
