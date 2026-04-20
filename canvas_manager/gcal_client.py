"""Google Calendar API client — fetches upcoming events automatically."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta, date

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


class GCalClient:
    def __init__(self, creds: Credentials) -> None:
        self.service = build("calendar", "v3", credentials=creds)

    def get_upcoming_events(
        self,
        days_ahead: int = 30,
        calendar_id: str = "primary",
    ) -> list[dict]:
        """Return upcoming events as normalized deadline dicts."""
        now = datetime.now(tz=timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        result = self.service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()

        deadlines: list[dict] = []
        for event in result.get("items", []):
            summary = event.get("summary", "").strip()
            if not summary:
                continue

            start_dt = _parse_gcal_time(event.get("start"))
            due_dt = _parse_gcal_time(event.get("end") or event.get("start"))
            if due_dt is None or due_dt <= now:
                continue

            description = event.get("description", "") or ""
            deadlines.append({
                "name": summary,
                "due_at": due_dt,
                "start_at": start_dt,
                "course": _extract_course(summary, description),
                "url": event.get("htmlLink", ""),
                "source": "gcal",
                "submitted": False,
                "recurrence": bool(event.get("recurringEventId")),
            })

        deadlines.sort(key=lambda d: d["due_at"])
        return deadlines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_gcal_time(time_obj: dict | None) -> datetime | None:
    if not time_obj:
        return None
    if "dateTime" in time_obj:
        dt = datetime.fromisoformat(time_obj["dateTime"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if "date" in time_obj:
        # All-day event — treat end-of-day as the deadline
        d = date.fromisoformat(time_obj["date"])
        return datetime(d.year, d.month, d.day, 23, 59, tzinfo=timezone.utc)
    return None


def _extract_course(summary: str, description: str) -> str:
    patterns = [
        r"\[([A-Z0-9]{2}-?[0-9]{3,4})\]",
        r"\(([A-Z0-9]{2}-?[0-9]{3,4})\)",
        r"\b([A-Z]{2,4}[\s-]?\d{3,4})\b",
    ]
    for text in (summary, description):
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
    return ""
