"""CLI entry point for canvas-manager."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

from .config import get_canvas_config, get_email_config, get_sms_config, get_reminder_config, get_gcal_config, get_gemini_config
from .gemini_client import classify_events, estimate_durations, improve_schedule as gemini_improve, parse_schedule_command
from .canvas_client import CanvasClient, parse_due_date
from .gcal_client import GCalClient
from .ical_parser import parse_ical, merge_with_canvas
from .notifier import Notifier, get_credentials
from . import schedule as _sched
from .scheduler import generate_plan

console = Console()
# Store cache in the project root so it's always found regardless of cwd
DEADLINES_CACHE = Path(__file__).parent.parent / ".canvas_manager_deadlines.json"
HABITS_FILE = Path.home() / ".canvas_manager" / "habits.json"


@click.group()
@click.version_option(package_name="canvas-manager")
def cli() -> None:
    """Canvas Manager — Canvas + Google Calendar → email & SMS reminders."""
    pass


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@cli.command()
def setup() -> None:
    """Interactive setup: configure .env with Canvas, email, SMS, and GCal credentials."""
    from .config import CARRIER_GATEWAYS

    env_path = Path(__file__).parent.parent / ".env"
    console.rule("[bold cyan]Canvas Manager — Setup[/bold cyan]")

    # Load existing values as defaults
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    def ask(prompt: str, default: str = "", password: bool = False) -> str:
        """Prompt and re-prompt until a non-empty value is entered."""
        while True:
            value = Prompt.ask(prompt, default=default, password=password).strip()
            if value:
                return value
            console.print("[red]  This field cannot be empty. Please try again.[/red]")

    def ask_validated(prompt: str, validate, default: str = "", password: bool = False) -> str:
        """Prompt and re-prompt until validate(value) returns True."""
        while True:
            value = Prompt.ask(prompt, default=default, password=password).strip()
            error = validate(value)
            if error is None:
                return value
            console.print(f"[red]  {error}[/red]")

    # --- Validators ---
    def valid_url(v: str):
        if not v.startswith("http://") and not v.startswith("https://"):
            return "Must be a valid URL starting with https://"
        return None

    def valid_token(v: str):
        if not v:
            return "Canvas API token cannot be empty."
        return None

    def valid_email(v: str):
        if "@" not in v or "." not in v.split("@")[-1]:
            return "Enter a valid email address (e.g. you@example.com)."
        return None

    def valid_phone(v: str):
        digits = "".join(c for c in v if c.isdigit())
        if digits.startswith("1") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) != 10:
            return "Enter a valid 10-digit US phone number (e.g. +11234567890)."
        return None

    def valid_carrier(v: str):
        if v.lower().strip() not in CARRIER_GATEWAYS:
            return f"Unknown carrier. Choose from: {', '.join(CARRIER_GATEWAYS.keys())}."
        return None

    def valid_time(v: str):
        try:
            h, m = map(int, v.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            return "Enter a valid time in HH:MM format (e.g. 08:00)."
        return None

    def valid_days(v: str):
        try:
            if int(v) < 1:
                raise ValueError
        except ValueError:
            return "Enter a positive whole number (e.g. 3)."
        return None

    # --- Prompts ---
    console.print("\n[bold]Canvas[/bold]")
    while True:
        canvas_url = ask_validated(
            "  Canvas base URL",
            valid_url,
            default=existing.get("CANVAS_BASE_URL", "https://canvas.cmu.edu"),
        ).rstrip("/")
        canvas_token = ask_validated(
            "  Canvas API token",
            valid_token,
            default=existing.get("CANVAS_API_TOKEN", ""),
            password=True,
        )
        # Live validation: test the URL + token against Canvas API
        console.print("  [dim]Verifying Canvas credentials...[/dim]", end=" ")
        try:
            import requests as _requests
            resp = _requests.get(
                f"{canvas_url}/api/v1/courses",
                headers={"Authorization": f"Bearer {canvas_token}"},
                params={"per_page": 1},
                timeout=8,
            )
            if resp.status_code == 401:
                console.print("[red]failed[/red]")
                console.print("[red]  Invalid token. Please re-enter your Canvas API token.[/red]")
                existing["CANVAS_API_TOKEN"] = ""
                continue
            if resp.status_code == 404:
                console.print("[red]failed[/red]")
                console.print("[red]  Canvas URL not found. Please check the URL and try again.[/red]")
                existing["CANVAS_BASE_URL"] = canvas_url
                continue
            resp.raise_for_status()
            console.print("[green]OK[/green]")
            break
        except _requests.exceptions.ConnectionError:
            console.print("[red]failed[/red]")
            console.print("[red]  Could not reach that URL. Please check the Canvas base URL.[/red]")
            existing["CANVAS_BASE_URL"] = canvas_url

    console.print("\n[bold]Email[/bold]")
    to_email = ask_validated(
        "  Send reminders to (email)",
        valid_email,
        default=existing.get("TO_EMAIL_ADDRESS", ""),
    )

    console.print("\n[bold]SMS[/bold]")
    phone = ask_validated(
        "  Your phone number (e.g. +11234567890)",
        valid_phone,
        default=existing.get("TO_PHONE_NUMBER", ""),
    )
    carrier = ask_validated(
        f"  Your carrier ({', '.join(CARRIER_GATEWAYS.keys())})",
        valid_carrier,
        default=existing.get("PHONE_CARRIER", "tmobile"),
    ).lower().strip()

    console.print("\n[bold]Google Calendar[/bold]")
    while True:
        gcal_id = ask(
            "  Google Calendar ID",
            default=existing.get("GCAL_CALENDAR_ID", "primary"),
        )
        # Live validation: check calendar ID using existing credentials if available
        from .notifier import TOKEN_FILE, CREDS_FILE
        if os.path.exists(TOKEN_FILE) and os.path.exists(CREDS_FILE):
            console.print("  [dim]Verifying Google Calendar ID...[/dim]", end=" ")
            try:
                creds = get_credentials()
                gcal = GCalClient(creds)
                gcal.service.calendarList().get(calendarId=gcal_id).execute()
                console.print("[green]OK[/green]")
                break
            except Exception as e:
                console.print("[red]failed[/red]")
                console.print(f"[red]  Calendar ID not found or not accessible. Please check and try again.[/red]")
                existing["GCAL_CALENDAR_ID"] = gcal_id
        else:
            console.print("  [dim](Google credentials not set up yet — calendar ID will be verified on first sync)[/dim]")
            break

    console.print("\n[bold]Reminder settings[/bold]")
    reminder_time = ask_validated(
        "  Daily reminder time (HH:MM, 24h)",
        valid_time,
        default=existing.get("REMINDER_TIME", "08:00"),
    )
    lookahead = ask_validated(
        "  Days ahead to include in reminders",
        valid_days,
        default=existing.get("REMINDER_LOOKAHEAD_DAYS", "3"),
    )

    console.print("\n[bold]Gemini AI[/bold] [dim](optional — powers smart classification and schedule improvement)[/dim]")
    gemini_key = Prompt.ask(
        "  Gemini API key (leave blank to skip)",
        default=existing.get("GEMINI_API_KEY", ""),
        password=True,
    ).strip()

    # Write .env
    env_content = f"""CANVAS_BASE_URL={canvas_url}
CANVAS_API_TOKEN={canvas_token}

TO_EMAIL_ADDRESS={to_email}
FROM_NAME=Canvas Manager

TO_PHONE_NUMBER={phone}
PHONE_CARRIER={carrier}

REMINDER_LOOKAHEAD_DAYS={lookahead}
REMINDER_TIME={reminder_time}

GCAL_CALENDAR_ID={gcal_id}
GCAL_DAYS_AHEAD=30

GEMINI_API_KEY={gemini_key}
GEMINI_MODEL=gemini-2.0-flash-lite
"""
    env_path.write_text(env_content)
    console.print(f"\n[green]✓[/green] Saved config to [dim]{env_path}[/dim]")

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Run [cyan]canvas-manager habits[/cyan] to set your schedule preferences")
    console.print("  2. Run [cyan]canvas-manager sync[/cyan] to fetch your first deadlines")
    console.print("  3. Run [cyan]canvas-manager plan[/cyan] to generate today's schedule")
    console.print("  4. Run [cyan]canvas-manager setup-cron[/cyan] to install daily automated reminders")
    console.print("  5. Run [cyan]canvas-manager todo[/cyan] to preview a reminder summary\n")


# ---------------------------------------------------------------------------
# import-ical
# ---------------------------------------------------------------------------

@cli.command("import-ical")
@click.argument("ical_file", type=click.Path(dir_okay=False), required=False)
def import_ical(ical_file: str | None) -> None:
    """
    Read an iCal (.ics) file, merge with Canvas, and save locally.
    Drag your .ics file into the terminal when prompted.

    \b
    Example:
      canvas-manager import-ical ~/Downloads/calendar.ics
      canvas-manager import-ical
    """
    if not ical_file:
        ical_file = Prompt.ask("Path to your .ics file").strip().strip("'\"")
        ical_file = ical_file.replace("\\ ", " ").replace("\\(", "(").replace("\\)", ")")
        ical_file = os.path.expanduser(ical_file)

    if not os.path.exists(ical_file):
        console.print(f"[red]Error: File not found: {ical_file}[/red]")
        sys.exit(1)

    console.print(f"[bold]Parsing iCal file:[/bold] {ical_file}")
    ical_deadlines = parse_ical(ical_file)
    console.print(f"  Found [cyan]{len(ical_deadlines)}[/cyan] upcoming event(s) in iCal.")

    canvas_cfg = get_canvas_config()
    console.print("[bold]Fetching Canvas assignments...[/bold]", end=" ")
    canvas = CanvasClient(canvas_cfg["base_url"], canvas_cfg["token"])
    canvas_assignments = canvas.get_all_upcoming_assignments()
    console.print(f"[green]done ({len(canvas_assignments)} found)[/green]")

    merged = merge_with_canvas(ical_deadlines, canvas_assignments)

    # Preserve gcal items from existing cache — only replace canvas/ical data
    existing = _load_cache()
    from .ical_parser import _similar
    tolerance = timedelta(hours=2)
    for g in existing:
        if g.get("source") == "gcal":
            is_dup = any(
                _similar(g["name"], m["name"]) and abs(g["due_at"] - m["due_at"]) <= tolerance
                for m in merged
            )
            if not is_dup:
                merged.append(g)

    merged.sort(key=lambda d: d["due_at"])
    console.print(f"  Merged total: [bold]{len(merged)}[/bold] unique deadline(s).\n")

    for d in merged:
        d["type"] = _classify(d)
        d.pop("recurrence", None)
    _print_table(merged, title="Merged Deadlines (iCal + Canvas)")
    _save_cache(merged)
    console.print(f"\n[dim]Saved. Run [bold]canvas-manager list[/bold] to view or [bold]canvas-manager todo[/bold] to see a summary.[/dim]")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--no-gcal", "skip_gcal", is_flag=True, default=False, help="Skip Google Calendar fetch.")
def sync(skip_gcal: bool) -> None:
    """Fetch upcoming deadlines from Canvas + Google Calendar and save locally."""
    deadlines: list[dict] = []

    # --- Canvas ---
    canvas_cfg = get_canvas_config()
    console.print("[bold]Fetching Canvas assignments...[/bold]", end=" ")
    canvas = CanvasClient(canvas_cfg["base_url"], canvas_cfg["token"])
    assignments = canvas.get_all_upcoming_assignments()
    console.print(f"[green]done ({len(assignments)} found)[/green]")
    for a in assignments:
        due = parse_due_date(a["due_at"])
        deadlines.append({
            "name": a["name"],
            "due_at": due,
            "course": a.get("_course_name", "Unknown"),
            "url": a.get("html_url", ""),
            "source": "canvas",
            "submitted": a.get("_submitted", False),
            "recurrence": False,
        })

    # --- Google Calendar ---
    if not skip_gcal:
        gcal_cfg = get_gcal_config()
        console.print("[bold]Fetching Google Calendar events...[/bold]", end=" ")
        try:
            creds = get_credentials()
            gcal = GCalClient(creds)
            gcal_events = gcal.get_upcoming_events(
                days_ahead=gcal_cfg["days_ahead"],
                calendar_id=gcal_cfg["calendar_id"],
            )
            console.print(f"[green]done ({len(gcal_events)} found)[/green]")

            # Deduplicate: skip GCal events already covered by Canvas
            from .ical_parser import _similar
            from datetime import timedelta
            tolerance = timedelta(hours=2)
            for event in gcal_events:
                is_dup = any(
                    _similar(event["name"], d["name"])
                    and abs(event["due_at"] - d["due_at"]) <= tolerance
                    for d in deadlines
                )
                if not is_dup:
                    deadlines.append(event)
        except Exception as e:
            console.print(f"[yellow]skipped (error: {e})[/yellow]")

    # Preserve ical/manual items — only replace canvas/gcal data
    existing = _load_cache()
    preserved = [d for d in existing if d.get("source") not in ("canvas", "gcal")]
    deadlines.extend(preserved)
    deadlines.sort(key=lambda d: d["due_at"])
    for d in deadlines:
        d["type"] = _classify(d)
        d.pop("recurrence", None)

    gemini_cfg = get_gemini_config()
    console.print("[dim]Running Gemini classification...[/dim]", end=" ")
    deadlines = classify_events(deadlines, gemini_cfg["api_key"], gemini_cfg["model"])
    if gemini_cfg["api_key"]:
        console.print("[green]done[/green]")

    _save_cache(deadlines)
    console.print(f"[green]✓[/green] Saved {len(deadlines)} deadline(s). Run [cyan]canvas-manager list[/cyan] to view.")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--days", default=14, show_default=True, help="How many days ahead to show.")
def list_deadlines(days: int) -> None:
    """List upcoming deadlines from the local cache."""
    deadlines = _load_cache()
    if not deadlines:
        console.print("[yellow]No cache found. Run sync or import-ical first.[/yellow]")
        return
    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(days=days)
    upcoming = sorted(
        [d for d in deadlines if now <= d["due_at"] <= cutoff],
        key=lambda d: d["due_at"],
    )
    assignments = [d for d in upcoming if d.get("type") in ("assignment", "study")]
    classes     = [d for d in upcoming if d.get("type") == "class"]
    personal    = [d for d in upcoming if d.get("type") in ("personal", "other")]

    if assignments:
        _print_table(assignments, title=f"Assignments & Study — next {days} days")
    else:
        console.print(f"[dim]No assignments in the next {days} days.[/dim]")
    if classes:
        _print_table(classes, title=f"Classes — next {days} days")
    else:
        console.print(f"[dim]No classes in the next {days} days.[/dim]")
    if personal:
        _print_table(personal, title=f"Personal & Other — next {days} days")


# ---------------------------------------------------------------------------
# todo
# ---------------------------------------------------------------------------

@cli.command("todo")
@click.option("--days", default=None, type=int, help="Lookahead days (default from config).")
@click.option("--email", "send_email", is_flag=True, default=False, help="Send email.")
@click.option("--sms", "send_sms", is_flag=True, default=False, help="Send SMS.")
@click.option("--to-email", "to_email", default=None, help="Override recipient email.")
@click.option("--assignments", "show_assignments", is_flag=True, default=False, help="Show assignment details.")
@click.option("--classes", "show_classes", is_flag=True, default=False, help="Show class details.")
@click.option("--personal", "show_personal", is_flag=True, default=False, help="Show personal details.")
def todo(
    days: int | None,
    send_email: bool,
    send_sms: bool,
    to_email: str | None,
    show_assignments: bool,
    show_classes: bool,
    show_personal: bool,
) -> None:
    """
    Show upcoming deadlines summary. Use --email/--sms to send.

    \b
    Examples:
      canvas-manager todo
      canvas-manager todo --assignments
      canvas-manager todo --email --sms
      canvas-manager todo --days 7 --classes
    """
    reminder_cfg = get_reminder_config()
    lookahead = days if days is not None else reminder_cfg["lookahead_days"]
    deadlines = _load_cache()

    if not deadlines:
        console.print("[yellow]No cache found. Run sync first.[/yellow]")
        return

    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(days=lookahead)
    upcoming = sorted(
        [d for d in deadlines if now <= d["due_at"] <= cutoff],
        key=lambda d: d["due_at"],
    )

    assignments = [d for d in upcoming if d.get("type") in ("assignment", "study")]
    classes     = [d for d in upcoming if d.get("type") == "class"]
    personal    = [d for d in upcoming if d.get("type") in ("personal", "other")]

    # Summary line — always shown
    parts = []
    if assignments:
        parts.append(f"[magenta]{len(assignments)} assignment{'s' if len(assignments) != 1 else ''}[/magenta]")
    if classes:
        parts.append(f"[blue]{len(classes)} class{'es' if len(classes) != 1 else ''}[/blue]")
    if personal:
        parts.append(f"[cyan]{len(personal)} personal item{'s' if len(personal) != 1 else ''}[/cyan]")

    if parts:
        console.print(f"[bold]Next {lookahead} days:[/bold] {', '.join(parts)}")
    else:
        console.print(f"[green]Nothing due in the next {lookahead} days.[/green]")

    # Optional detail tables
    if show_assignments and assignments:
        _print_table(assignments, title=f"Assignments & Study — next {lookahead} days")
    if show_classes and classes:
        _print_table(classes, title=f"Classes — next {lookahead} days")
    if show_personal and personal:
        _print_table(personal, title=f"Personal & Other — next {lookahead} days")

    # Send
    if send_email or send_sms:
        notifier = Notifier()
        if send_email:
            email_cfg = get_email_config()
            recipient = to_email or email_cfg["to_address"]
            console.print(f"[bold]Sending todo email to {recipient}...[/bold]", end=" ")
            try:
                msg_id = notifier.send_email(recipient, deadlines, lookahead)
                console.print(f"[green]Sent! ({msg_id})[/green]")
            except Exception as e:
                console.print(f"[red]Failed: {e}[/red]")
        if send_sms:
            sms_cfg = get_sms_config()
            console.print(f"[bold]Sending todo SMS...[/bold]", end=" ")
            try:
                msg_id = notifier.send_sms(sms_cfg["sms_email"], deadlines, lookahead)
                console.print(f"[green]Sent! ({msg_id})[/green]")
            except Exception as e:
                console.print(f"[red]Failed: {e}[/red]")


# ---------------------------------------------------------------------------
# setup-cron
# ---------------------------------------------------------------------------

@cli.command("setup-cron")
@click.option("--time", "reminder_time", default=None, help="Send time HH:MM (24h).")
def setup_cron(reminder_time: str | None) -> None:
    """Install (or reinstall) the daily sync + todo cron job."""
    from shutil import which
    import subprocess

    reminder_cfg = get_reminder_config()
    t = reminder_time or reminder_cfg["reminder_time"]
    try:
        hour, minute = map(int, t.split(":"))
    except ValueError:
        console.print(f"[red]Invalid time: {t}. Use HH:MM.[/red]")
        sys.exit(1)

    cmd = which("canvas-manager") or "canvas-manager"
    log = Path.home() / ".canvas_manager.log"
    cron_line = f"{minute} {hour} * * * {cmd} sync && {cmd} todo --email --sms >> {log} 2>&1"

    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing_cron = result.stdout if result.returncode == 0 else ""
    new_cron = "\n".join(
        line for line in existing_cron.splitlines()
        if "canvas-manager" not in line
    )
    new_cron = new_cron.rstrip() + f"\n{cron_line}\n"
    subprocess.run(["crontab", "-"], input=new_cron, text=True)
    console.print(f"[green]✓[/green] Cron job installed: [bold]sync + todo[/bold] daily at [bold]{t}[/bold]")
    console.print(f"[dim]Logs → {log}[/dim]")


# ---------------------------------------------------------------------------
# clear-cache
# ---------------------------------------------------------------------------

@cli.command("clear-cache")
def clear_cache() -> None:
    """Delete the local deadlines cache."""
    if not DEADLINES_CACHE.exists():
        console.print("[yellow]No cache file found — nothing to delete.[/yellow]")
        return
    DEADLINES_CACHE.unlink()
    console.print(f"[green]✓[/green] Cache deleted. Run [cyan]canvas-manager sync[/cyan] to rebuild.")


# ---------------------------------------------------------------------------
# habits
# ---------------------------------------------------------------------------

@cli.command("habits")
def habits() -> None:
    """Set up or review your daily schedule and focus habits."""
    existing = _load_habits()

    if existing:
        _print_habits(existing)
        if not Confirm.ask("\nWant to update your habits profile?", default=False):
            return

    console.rule("[bold cyan]Habits Profile[/bold cyan]")
    console.print("[dim]Press Enter to keep the default shown in brackets.[/dim]\n")

    def ask_time(prompt: str, default: str) -> str:
        while True:
            val = Prompt.ask(prompt, default=default).strip()
            try:
                h, m = map(int, val.split(":"))
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            except (ValueError, AttributeError):
                pass
            console.print("[red]  Enter a time in HH:MM format (e.g. 07:30).[/red]")

    def ask_minutes(prompt: str, default: int) -> int:
        while True:
            val = Prompt.ask(prompt, default=str(default)).strip()
            try:
                n = int(val)
                if n > 0:
                    return n
            except ValueError:
                pass
            console.print("[red]  Enter a positive whole number.[/red]")

    def ask_time_list(prompt: str, default: str) -> list[str]:
        while True:
            val = Prompt.ask(prompt, default=default).strip()
            parts = [p.strip() for p in val.split(",") if p.strip()]
            try:
                for p in parts:
                    h, m = map(int, p.split(":"))
                    assert 0 <= h <= 23 and 0 <= m <= 59
                return [f"{int(p.split(':')[0]):02d}:{int(p.split(':')[1]):02d}" for p in parts]
            except (ValueError, AssertionError):
                console.print("[red]  Enter comma-separated times in HH:MM format (e.g. 12:00, 18:00).[/red]")

    def ask_range_list(prompt: str, default: str) -> list[str]:
        while True:
            val = Prompt.ask(prompt, default=default).strip()
            if not val or val.lower() == "none":
                return []
            parts = [p.strip() for p in val.split(",") if p.strip()]
            try:
                out = []
                for p in parts:
                    start, end = p.split("-")
                    for t in (start.strip(), end.strip()):
                        h, m = map(int, t.split(":"))
                        assert 0 <= h <= 23 and 0 <= m <= 59
                    out.append(p.strip())
                return out
            except (ValueError, AssertionError):
                console.print("[red]  Enter comma-separated ranges like 09:00-11:00, 14:00-16:00.[/red]")

    _VALID_TYPES = ["class", "assignment", "personal", "study", "other"]
    _DEFAULT_PRIORITY = ["class", "assignment", "personal", "study", "other"]

    def ask_priority(prompt: str, default: list[str]) -> list[str]:
        default_str = ", ".join(default)
        while True:
            val = Prompt.ask(prompt, default=default_str).strip()
            parts = [p.strip().lower() for p in val.split(",") if p.strip()]
            if sorted(parts) == sorted(_VALID_TYPES):
                return parts
            console.print(f"[red]  Must include all 5 types exactly once: {', '.join(_VALID_TYPES)}[/red]")

    d = existing or {}

    existing_stops_str = ", ".join(
        f"{s['start']}-{s['end']}" for s in d.get("hard_stops", [])
    ) or "none"

    console.print("\n[bold]Schedule[/bold]")
    wake_time               = ask_time(    "  Wake time",                        d.get("wake_time", "07:00"))
    sleep_time              = ask_time(    "  Sleep time",                       d.get("sleep_time", "23:00"))
    peak_focus_hours        = ask_range_list("  Peak focus hours (e.g. 09:00-11:00, 14:00-16:00)", ", ".join(d.get("peak_focus_hours", ["09:00-11:00"])))
    preferred_block_minutes = ask_minutes( "  Preferred focus block (minutes)",  d.get("preferred_block_minutes", 90))
    break_minutes           = ask_minutes( "  Break between blocks (minutes)",   d.get("break_minutes", 15))
    hard_stops              = _parse_hard_stops(
                                  ask_range_list(
                                      "  Hard-stop ranges (e.g. 12:00-13:00, 18:00-19:00) — or 'none'",
                                      existing_stops_str if existing_stops_str != "none" else "",
                                  )
                              )

    console.print("\n[bold]Priority[/bold] [dim](highest → lowest; controls which blocks get peak hours and earliest slots)[/dim]")
    priority_order = ask_priority(
        "  Priority order",
        d.get("priority_order", _DEFAULT_PRIORITY),
    )

    console.print("\n[bold]Exam & Quiz prep[/bold]")
    exam_prep_days_before   = ask_minutes( "  Start studying N days before exam",       d.get("exam_prep_days_before", 2))
    exam_prep_blocks_per_day= ask_minutes( "  Study blocks per day (X)",                d.get("exam_prep_blocks_per_day", 2))
    exam_prep_block_minutes = ask_minutes( "  Minutes per study block (Y)",             d.get("exam_prep_block_minutes", 60))

    profile = {
        "wake_time":               wake_time,
        "sleep_time":              sleep_time,
        "peak_focus_hours":        peak_focus_hours,
        "preferred_block_minutes": preferred_block_minutes,
        "break_minutes":           break_minutes,
        "hard_stops":              hard_stops,
        "priority_order":          priority_order,
        "exam_prep_days_before":   exam_prep_days_before,
        "exam_prep_blocks_per_day":exam_prep_blocks_per_day,
        "exam_prep_block_minutes": exam_prep_block_minutes,
    }

    HABITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HABITS_FILE.write_text(json.dumps(profile, indent=2))
    console.print("\n[green]✓[/green] Habits profile saved.")
    _print_habits(profile)


def _load_habits() -> dict | None:
    if not HABITS_FILE.exists():
        return None
    try:
        return json.loads(HABITS_FILE.read_text())
    except Exception:
        return None


def _print_habits(profile: dict) -> None:
    table = Table(title="Your Habits Profile", show_lines=True)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="bold")
    hard_stops_str = ", ".join(
        f"{s['start']}–{s['end']}" for s in profile.get("hard_stops", [])
    ) or "none"
    default_priority = ["class", "assignment", "personal", "study", "other"]
    priority_str = " > ".join(profile.get("priority_order", default_priority))
    table.add_row("Wake time",             profile.get("wake_time", "—"))
    table.add_row("Sleep time",            profile.get("sleep_time", "—"))
    table.add_row("Peak focus hours",      ", ".join(profile.get("peak_focus_hours", [])))
    table.add_row("Focus block",           f"{profile.get('preferred_block_minutes', '—')} min")
    table.add_row("Break between blocks",  f"{profile.get('break_minutes', '—')} min")
    table.add_row("Hard stops",            hard_stops_str)
    table.add_row("Priority order",        priority_str)
    table.add_row("Exam prep — days before",      str(profile.get("exam_prep_days_before", 2)))
    table.add_row("Exam prep — blocks/day (X)",   str(profile.get("exam_prep_blocks_per_day", 2)))
    table.add_row("Exam prep — min/block (Y)",    str(profile.get("exam_prep_block_minutes", 60)))
    console.print(table)


def _parse_hard_stops(ranges: list[str]) -> list[dict]:
    """Convert ["09:00-11:00", ...] to [{"start": "09:00", "end": "11:00"}, ...]."""
    result = []
    for r in ranges:
        if "-" in r:
            parts = r.split("-", 1)
            result.append({"start": parts[0].strip(), "end": parts[1].strip()})
    return result


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

@cli.command("send")
@click.option("--date", "plan_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Day's schedule to send (default: today).")
@click.option("--email", "send_email", is_flag=True, default=False, help="Send email only.")
@click.option("--sms",   "send_sms",   is_flag=True, default=False, help="Send SMS only.")
@click.option("--preview", is_flag=True, default=False, help="Print without sending.")
@click.option("--to-email", "to_email", default=None, help="Override recipient email.")
def send(
    plan_date: datetime | None,
    send_email: bool,
    send_sms: bool,
    preview: bool,
    to_email: str | None,
) -> None:
    """Send today's block schedule via email + SMS (default: both; use --email or --sms for one).

    \b
    Examples:
      canvas-manager send
      canvas-manager send --preview
      canvas-manager send --email
      canvas-manager send --date 2026-05-01
    """
    d = plan_date.date() if plan_date else date.today()
    blocks = _sched.list_blocks(d)
    if not blocks:
        console.print(f"[yellow]No blocks scheduled for {d}. Run 'plan' first.[/yellow]")
        return

    counts = {t: sum(1 for b in blocks if b.get("type") == t)
              for t in ("class", "assignment", "study", "personal", "other")}
    summary_parts = []
    if counts["class"]:
        summary_parts.append(f"{counts['class']} class{'es' if counts['class'] != 1 else ''}")
    if counts["assignment"]:
        summary_parts.append(f"{counts['assignment']} assignment{'s' if counts['assignment'] != 1 else ''}")
    if counts["study"]:
        summary_parts.append(f"{counts['study']} study block{'s' if counts['study'] != 1 else ''}")
    if counts["personal"]:
        summary_parts.append(f"{counts['personal']} personal item{'s' if counts['personal'] != 1 else ''}")
    if counts["other"]:
        summary_parts.append(f"{counts['other']} other")
    summary = ", ".join(summary_parts) or "0 blocks"

    plain, html = _render_schedule_email(d, blocks)
    subject = f"Your schedule for {d} — {summary}"

    if preview:
        console.print(f"\n[bold cyan]SCHEDULE PREVIEW[/bold cyan]")
        console.print(f"[bold]Subject:[/bold] {subject}")
        console.print(f"[dim]{'─'*60}[/dim]")
        console.print(plain)
        console.print(f"\n[dim]Summary: {summary}[/dim]")
        console.print("\n[yellow]--preview: nothing sent.[/yellow]")
        return

    do_email = send_email or (not send_email and not send_sms)
    do_sms   = send_sms   or (not send_email and not send_sms)

    notifier = Notifier()
    if do_email:
        email_cfg = get_email_config()
        recipient = to_email or email_cfg["to_address"]
        console.print(f"[bold]Sending schedule email to {recipient}...[/bold]", end=" ")
        try:
            msg_id = notifier.send_email_raw(recipient, subject, plain, html)
            console.print(f"[green]Sent! ({msg_id})[/green]")
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")
    if do_sms:
        sms_cfg = get_sms_config()
        sms_text = _render_schedule_sms(d, blocks, summary)
        console.print(f"[bold]Sending schedule SMS...[/bold]", end=" ")
        try:
            msg_id = notifier.send_sms_raw(sms_cfg["sms_email"], sms_text)
            console.print(f"[green]Sent! ({msg_id})[/green]")
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

@cli.command("schedule")
@click.argument("command_text")
@click.option("--date", "plan_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Day to modify (default: today).")
@click.option("--preview", is_flag=True, default=False,
              help="Show proposed changes without saving.")
def schedule_cmd(command_text: str, plan_date: datetime | None, preview: bool) -> None:
    """Modify today's schedule with a natural-language command (powered by Gemini).

    \b
    Examples:
      canvas-manager schedule "add gym from 3pm to 4pm"
      canvas-manager schedule "move the ML homework block to 2pm"
      canvas-manager schedule "delete the study block"
      canvas-manager schedule "clear everything after 6pm"
    """
    gemini_cfg = get_gemini_config()
    if not gemini_cfg["api_key"]:
        console.print("[red]Error: GEMINI_API_KEY is not set. Run 'canvas-manager setup' to configure Gemini.[/red]")
        return

    d = plan_date.date() if plan_date else date.today()
    habits = _load_habits() or {}
    current_blocks = _sched.list_blocks(d)

    console.print("[dim]Parsing command...[/dim]", end=" ")
    updated_blocks = parse_schedule_command(
        command_text, d, current_blocks, habits,
        gemini_cfg["api_key"], gemini_cfg["model"],
    )
    console.print("[green]done[/green]")

    # Diff display
    old_map = {b["id"]: b for b in current_blocks}
    new_map = {b["id"]: b for b in updated_blocks}
    changed = False
    for bid, b in new_map.items():
        if bid not in old_map:
            console.print(f"[green]+[/green] Add: {b['start']}–{b['end']} {b['title']} [{b['type']}]")
            changed = True
    for bid, b in old_map.items():
        if bid not in new_map:
            console.print(f"[red]-[/red] Remove: {b['start']}–{b['end']} {b['title']}")
            changed = True
    for bid, b in new_map.items():
        if bid in old_map and b != old_map[bid]:
            old = old_map[bid]
            console.print(
                f"[yellow]~[/yellow] Update: {old['start']}–{old['end']} → "
                f"{b['start']}–{b['end']} {b['title']}"
            )
            changed = True

    if not changed:
        console.print("[yellow]No changes detected.[/yellow]")
        return

    if preview:
        console.print("\n[yellow]--preview: no changes saved.[/yellow]")
        return

    _sched.save_plan(d, updated_blocks)

    all_deadlines = _load_cache()
    console.print("[dim]Running Gemini schedule improvement...[/dim]", end=" ")
    improved = gemini_improve(
        d, updated_blocks, habits, all_deadlines,
        gemini_cfg["api_key"], gemini_cfg["model"],
    )
    console.print("[green]done[/green]")
    if improved != updated_blocks:
        _sched.save_plan(d, improved)

    final_blocks = _sched.list_blocks(d)
    if final_blocks:
        _print_schedule_table(d, final_blocks)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

@cli.command("plan")
@click.option("--date", "plan_date", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Day to plan (default: today). Related: --overwrite, --export.")
@click.option("--overwrite", is_flag=True, default=False,
              help="Clear existing AI blocks and replan from scratch. Related: --date.")
@click.option("--export", "export_ical", is_flag=True, default=False,
              help="Write the plan to a .ics file after generating. Related: --out, --date.")
@click.option("--out", "out_path", default=None,
              help="Output path for the .ics file (default: schedule-YYYY-MM-DD.ics). Requires --export.")
def plan_cmd(plan_date: datetime | None, overwrite: bool,
             export_ical: bool, out_path: str | None) -> None:
    """View and generate the study plan for a day.

    Automatically places study blocks for assignments due that day, then
    displays the full schedule. Use 'schedule add/move/update/delete/clear'
    to manually adjust blocks afterwards.

    \b
    Examples:
      canvas-manager plan
      canvas-manager plan --date 2026-05-01
      canvas-manager plan --overwrite
      canvas-manager plan --export
    """
    d = plan_date.date() if plan_date else date.today()
    habits = _load_habits() or {}
    gemini_cfg = get_gemini_config()

    # --- Gemini call 1: estimate durations for today's flexible deadlines ---
    all_deadlines = _load_cache()
    today_flexible = [
        dl for dl in all_deadlines
        if not dl.get("submitted")
        and not dl.get("start_at")
        and dl.get("source") != "gcal"
        and dl["due_at"].astimezone().date() >= d
    ]
    if today_flexible:
        console.print("[dim]Estimating task durations...[/dim]", end=" ")
        today_flexible = estimate_durations(today_flexible, habits, gemini_cfg["api_key"], gemini_cfg["model"])
        if gemini_cfg["api_key"]:
            console.print("[green]done[/green]")
        duration_map = {e["name"]: e["duration_minutes"] for e in today_flexible}
        enriched_deadlines = [
            {**dl, "duration_minutes": duration_map[dl["name"]]} if dl["name"] in duration_map else dl
            for dl in all_deadlines
        ]
    else:
        enriched_deadlines = all_deadlines

    # --- Rule-based scheduling with enriched durations ---
    result = generate_plan(d, overwrite=overwrite, deadline_overrides=enriched_deadlines)

    habits_note = (
        "[dim]Using custom habits.[/dim]"
        if result["habits_used"] == "custom"
        else "[dim]Using default habits (no ~/.canvas_manager/habits.json found).[/dim]"
    )
    console.print(habits_note)

    if result["blocks"]:
        console.print(f"[green]✓[/green] Placed {len(result['blocks'])} new block(s).")
    elif result["existing_blocks"] and not result["skipped"]:
        console.print("[dim]Plan is up to date.[/dim]")
    elif not result["existing_blocks"] and not result["skipped"]:
        console.print("[green]No more work today![/green]")

    if result["skipped"]:
        console.print(f"[yellow]Could not fit:[/yellow] {', '.join(result['skipped'])}")

    # --- Gemini call 2: improve the drafted schedule ---
    all_blocks = _sched.list_blocks(d)
    if all_blocks:
        console.print("[dim]Running Gemini schedule improvement...[/dim]", end=" ")
        improved = gemini_improve(d, all_blocks, habits, all_deadlines, gemini_cfg["api_key"], gemini_cfg["model"])
        if gemini_cfg["api_key"]:
            console.print("[green]done[/green]")
        if improved != all_blocks:
            _sched.save_plan(d, improved)
            all_blocks = _sched.list_blocks(d)

    if all_blocks:
        _print_schedule_table(d, all_blocks)
    else:
        console.print(f"[yellow]No blocks scheduled for {d}.[/yellow]")

    if export_ical:
        ics_path = Path(out_path) if out_path else Path.cwd() / f"schedule-{d}.ics"
        _export_blocks_to_ical(d, all_blocks, ics_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXAM_KEYWORDS = {"quiz", "exam", "test", "midterm", "final"}

def _classify(d: dict) -> str:
    if d["source"] == "canvas":
        return "assignment"
    if d["source"] == "gcal":
        words = set(d.get("name", "").lower().split())
        if words & _EXAM_KEYWORDS:
            return "assignment"
        return "class"
    if d["source"] in ("manual", "ical"):
        return "personal"
    return "other"


def _print_table(deadlines: list[dict], title: str = "Deadlines") -> None:
    table = Table(title=title, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Assignment", style="bold")
    table.add_column("Course", style="cyan")
    table.add_column("Due", style="yellow")
    table.add_column("In", justify="right")
    table.add_column("Source", style="dim")
    table.add_column("Type")
    table.add_column("Status", style="green")

    now = datetime.now(tz=timezone.utc)
    for i, d in enumerate(deadlines, 1):
        due = d["due_at"]
        if isinstance(due, str):
            due = datetime.fromisoformat(due)
        local_due = due.astimezone()
        due_str = local_due.strftime("%a %b %d %I:%M%p").lower()
        delta = due - now
        h = int(delta.total_seconds() / 3600)
        days_left = delta.days
        if h < 0:
            in_str = "[red]overdue[/red]"
        elif h < 24:
            in_str = f"[red bold]{h}h[/red bold]"
        elif days_left <= 2:
            in_str = f"[yellow]{days_left}d[/yellow]"
        else:
            in_str = f"{days_left}d"
        submitted = "[green]✓ submitted[/green]" if d.get("submitted") else ("[red]✗ not submitted[/red]" if d.get("source") == "canvas" else "")
        dtype = d.get("type", "")
        type_str = {
            "class":      "[blue]class[/blue]",
            "assignment": "[magenta]assignment[/magenta]",
            "personal":   "[cyan]personal[/cyan]",
            "study":      "[green]study[/green]",
            "other":      "[dim]other[/dim]",
        }.get(dtype, "[dim]other[/dim]")
        table.add_row(str(i), d["name"][:45], d.get("course", "")[:20], due_str, in_str, d.get("source", ""), type_str, submitted)

    console.print(table)


def _hhmm_to_minutes(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _render_schedule_sms(d: date, blocks: list[dict], summary: str) -> str:
    """Brief plain-text schedule for SMS (≤320 chars)."""
    lines = [f"Schedule {d}: {summary}"]
    for b in sorted(blocks, key=lambda x: x["start"]):
        line = f"{b['start']}–{b['end']} {b['title'][:22]}"
        if sum(len(ln) + 1 for ln in lines) + len(line) > 290:
            lines.append("…more blocks — check email.")
            break
        lines.append(line)
    return "\n".join(lines)


def _export_blocks_to_ical(d: date, blocks: list[dict], out_path: Path) -> None:
    """Write schedule blocks to an iCal (.ics) file."""
    from icalendar import Calendar, Event as ICalEvent
    import uuid

    cal = Calendar()
    cal.add("prodid", "-//canvas-manager//schedule//EN")
    cal.add("version", "2.0")

    local_tz = datetime.now().astimezone().tzinfo
    for b in blocks:
        ev = ICalEvent()
        ev.add("summary", b["title"])
        ev.add("dtstart", datetime.combine(
            d, datetime.strptime(b["start"], "%H:%M").time(), tzinfo=local_tz))
        ev.add("dtend",   datetime.combine(
            d, datetime.strptime(b["end"],   "%H:%M").time(), tzinfo=local_tz))
        ev.add("uid", str(uuid.uuid4()))
        cal.add_component(ev)

    out_path.write_bytes(cal.to_ical())
    console.print(f"[green]✓[/green] Exported {len(blocks)} block(s) to {out_path}")


def _group_overlapping(blocks: list[dict]) -> list[list[dict]]:
    """Group blocks into clusters where at least one pair overlaps."""
    groups: list[list[dict]] = []
    for b in sorted(blocks, key=lambda x: x["start"]):
        placed = False
        for g in groups:
            if any(_hhmm_to_minutes(b["start"]) < _hhmm_to_minutes(x["end"])
                   and _hhmm_to_minutes(x["start"]) < _hhmm_to_minutes(b["end"])
                   for x in g):
                g.append(b)
                placed = True
                break
        if not placed:
            groups.append([b])
    return groups


def _print_schedule_table(d: date, blocks: list[dict]) -> None:
    """Render a day's blocks as a Rich table."""
    table = Table(title=f"Schedule — {d}", show_lines=True)
    table.add_column("Start", style="cyan", width=7)
    table.add_column("End",   style="cyan", width=7)
    table.add_column("Title", style="bold")
    table.add_column("Type")
    table.add_column("Source", style="dim")
    _TYPE_STYLE = {
        "class":      "[blue]class[/blue]",
        "assignment": "[magenta]assignment[/magenta]",
        "personal":   "[cyan]personal[/cyan]",
        "study":      "[green]study[/green]",
        "other":      "[dim]other[/dim]",
    }
    for group in _group_overlapping(blocks):
        for idx, b in enumerate(group):
            start_cell = b["start"] if idx == 0 else ""
            end_cell   = b["end"]   if idx == 0 else ""
            table.add_row(start_cell, end_cell, b["title"][:40],
                          _TYPE_STYLE.get(b["type"], b["type"]), b["source"])
    console.print(table)


def _render_schedule_email(d: date, blocks: list[dict]) -> tuple[str, str]:
    """Return (plain_text, html) for a day's schedule."""
    _TYPE_COLOR = {
        "class":      "#3B82F6",
        "assignment": "#A855F7",
        "personal":   "#06B6D4",
        "study":      "#22C55E",
        "other":      "#6B7280",
    }

    # plain text
    lines = [f"Your schedule for {d}", "=" * 40]
    for group in _group_overlapping(blocks):
        if len(group) == 1:
            b = group[0]
            lines.append(f"{b['start']}–{b['end']}  {b['title']}")
        else:
            time_range = f"{group[0]['start']}–{max(b['end'] for b in group)}"
            lines.append(f"{time_range}  [overlapping]")
            for b in group:
                lines.append(f"   • {b['start']}–{b['end']}  {b['title']}")
    plain = "\n".join(lines)

    # html
    rows = []
    for group in _group_overlapping(blocks):
        if len(group) == 1:
            b = group[0]
            color = _TYPE_COLOR.get(b["type"], "#6B7280")
            rows.append(
                f"<tr><td style='padding:6px 12px;color:#6B7280;white-space:nowrap'>"
                f"{b['start']}–{b['end']}</td>"
                f"<td colspan='2' style='padding:6px 12px;border-left:3px solid {color}'>"
                f"<strong>{b['title']}</strong>"
                f"<span style='color:#9CA3AF;font-size:12px'> [{b['type']}]</span></td></tr>"
            )
        else:
            # side-by-side columns for overlapping blocks
            time_cell = (f"<td style='padding:6px 12px;color:#6B7280;white-space:nowrap;"
                         f"vertical-align:top' rowspan='{len(group)}'>"
                         f"{group[0]['start']}–{max(b['end'] for b in group)}</td>")
            for i, b in enumerate(group):
                color = _TYPE_COLOR.get(b["type"], "#6B7280")
                cell = (f"<td style='padding:4px 8px;border-left:3px solid {color};"
                        f"background:#F9FAFB'>"
                        f"<strong>{b['title']}</strong> "
                        f"<span style='color:#9CA3AF;font-size:11px'>"
                        f"{b['start']}–{b['end']} [{b['type']}]</span></td>")
                rows.append(f"<tr>{time_cell if i == 0 else ''}{cell}</tr>")

    html = f"""<html><body style='font-family:sans-serif;max-width:600px;margin:auto'>
<h2 style='color:#1F2937'>Schedule — {d}</h2>
<table style='border-collapse:collapse;width:100%'>
{''.join(rows)}
</table></body></html>"""
    return plain, html


def _save_cache(deadlines: list[dict]) -> None:
    items = []
    for d in deadlines:
        item = dict(d)
        if isinstance(item["due_at"], datetime):
            item["due_at"] = item["due_at"].isoformat()
        if isinstance(item.get("start_at"), datetime):
            item["start_at"] = item["start_at"].isoformat()
        items.append(item)
    DEADLINES_CACHE.write_text(json.dumps(items, indent=2))


def _load_cache() -> list[dict]:
    if not DEADLINES_CACHE.exists():
        return []
    try:
        items = json.loads(DEADLINES_CACHE.read_text())
        for item in items:
            item["due_at"] = datetime.fromisoformat(item["due_at"])
            if item.get("start_at"):
                item["start_at"] = datetime.fromisoformat(item["start_at"])
        return items
    except Exception:
        return []
