"""CLI entry point for canvas-manager."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

from .config import get_canvas_config, get_email_config, get_sms_config, get_reminder_config, get_gcal_config
from .canvas_client import CanvasClient, parse_due_date
from .gcal_client import GCalClient
from .ical_parser import parse_ical, merge_with_canvas
from .notifier import Notifier, get_credentials

console = Console()
# Store cache in the project root so it's always found regardless of cwd
DEADLINES_CACHE = Path(__file__).parent.parent / ".canvas_manager_deadlines.json"
HABITS_FILE = Path(__file__).parent.parent / ".canvas_manager_habits.json"


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
    """Interactive setup: configure .env and install the daily cron job."""
    from shutil import which
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

    hour, minute = map(int, reminder_time.split(":"))

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
"""
    env_path.write_text(env_content)
    console.print(f"\n[green]✓[/green] Saved config to [dim]{env_path}[/dim]")

    # Update crontab
    if Confirm.ask("\n  Install daily cron job?", default=True):
        import subprocess
        bin_path = which("canvas-manager") or "canvas-manager"
        log = Path.home() / ".canvas_manager.log"
        cron_line = f"{minute} {hour} * * * {bin_path} sync && {bin_path} remind >> {log} 2>&1"

        # Remove any existing canvas-manager daily entry and add the new one
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing_cron = result.stdout if result.returncode == 0 else ""
        new_cron = "\n".join(
            line for line in existing_cron.splitlines()
            if "canvas-manager" not in line or line.strip().endswith("10 4 *")
        )
        new_cron = new_cron.rstrip() + f"\n{cron_line}\n"
        subprocess.run(["crontab", "-"], input=new_cron, text=True)
        console.print(f"[green]✓[/green] Cron job set for [bold]{reminder_time}[/bold] daily")

    console.print("\n[bold]Next steps:[/bold]")
    console.print("  1. Run [cyan]canvas-manager sync[/cyan] to fetch your first deadlines")
    console.print("  2. Run [cyan]canvas-manager remind --preview[/cyan] to preview your reminder")
    console.print("  3. Run [cyan]canvas-manager remind[/cyan] to send it now\n")


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
    console.print(f"  Merged total: [bold]{len(merged)}[/bold] unique deadline(s).\n")

    for d in merged:
        d["type"] = _classify(d)
        d.pop("recurrence", None)
    _print_table(merged, title="Merged Deadlines (iCal + Canvas)")
    _save_cache(merged)
    console.print(f"\n[dim]Saved. Run [bold]canvas-manager remind[/bold] to send notifications.[/dim]")


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

    deadlines.sort(key=lambda d: d["due_at"])
    for d in deadlines:
        d["type"] = _classify(d)
        d.pop("recurrence", None)
    _print_table(deadlines, title="Upcoming Deadlines (Canvas + Google Calendar)")
    _save_cache(deadlines)
    console.print(f"\n[dim]Saved {len(deadlines)} deadline(s).[/dim]")


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
    assignments = [d for d in upcoming if d.get("type") == "assignment"]
    events = [d for d in upcoming if d.get("type") != "assignment"]
    if assignments:
        _print_table(assignments, title=f"Assignments — next {days} days")
    else:
        console.print(f"[dim]No assignments in the next {days} days.[/dim]")
    if events:
        _print_table(events, title=f"Classes & Events — next {days} days")
    else:
        console.print(f"[dim]No classes or other events in the next {days} days.[/dim]")


# ---------------------------------------------------------------------------
# remind
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--days", default=None, type=int, help="Override lookahead days.")
@click.option("--email-only", "email_only", is_flag=True, default=False, help="Send email only.")
@click.option("--sms-only", "sms_only", is_flag=True, default=False, help="Send SMS only.")
@click.option("--preview", is_flag=True, default=False, help="Print messages without sending.")
@click.option("--to-email", "to_email", default=None, help="Override recipient email address.")
def remind(
    days: int | None,
    email_only: bool,
    sms_only: bool,
    preview: bool,
    to_email: str | None,
) -> None:
    """
    Send email and/or SMS reminders with upcoming deadlines.

    By default sends both. Use --email-only or --sms-only to send just one.

    \b
    Examples:
      canvas-manager remind
      canvas-manager remind --preview
      canvas-manager remind --email-only
      canvas-manager remind --sms-only
      canvas-manager remind --days 5
    """
    reminder_cfg = get_reminder_config()
    lookahead = days if days is not None else reminder_cfg["lookahead_days"]
    deadlines = _load_cache()

    if not deadlines:
        console.print("[yellow]No cache found. Run sync or import-ical first.[/yellow]")
        return

    send_email = not sms_only
    send_sms = not email_only

    # ---- Preview ----
    if preview:
        if send_email:
            from .notifier import _build_email
            subject, _, plain = _build_email(deadlines, lookahead)
            console.print(f"\n[bold cyan]EMAIL PREVIEW[/bold cyan]")
            console.print(f"[bold]Subject:[/bold] {subject}")
            console.print(f"[dim]{'─'*60}[/dim]")
            console.print(plain)

        if send_sms:
            from .notifier import _build_sms
            sms_body = _build_sms(deadlines, lookahead)
            console.print(f"\n[bold yellow]SMS PREVIEW[/bold yellow]")
            console.print(f"[dim]{'─'*60}[/dim]")
            console.print(sms_body)
            console.print(f"[dim]({len(sms_body)} chars)[/dim]")

        console.print("\n[yellow]--preview mode: nothing sent.[/yellow]")
        return

    # ---- Send ----
    notifier = Notifier()

    if send_email:
        email_cfg = get_email_config()
        recipient = to_email or email_cfg["to_address"]
        console.print(f"[bold]Sending email to {recipient}...[/bold]", end=" ")
        try:
            msg_id = notifier.send_email(recipient, deadlines, lookahead)
            console.print(f"[green]Sent! ({msg_id})[/green]")
        except Exception as e:
            console.print(f"[red]Failed: {e}[/red]")

    if send_sms:
        sms_cfg = get_sms_config()
        console.print(f"[bold]Sending SMS to {sms_cfg['phone']} via {sms_cfg['sms_email']}...[/bold]", end=" ")
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
    """Print a crontab line for daily automatic reminders."""
    from shutil import which

    reminder_cfg = get_reminder_config()
    t = reminder_time or reminder_cfg["reminder_time"]
    try:
        hour, minute = map(int, t.split(":"))
    except ValueError:
        console.print(f"[red]Invalid time: {t}. Use HH:MM.[/red]")
        sys.exit(1)

    cmd = which("canvas-manager") or "canvas-manager"
    log = Path.home() / ".canvas_manager.log"
    cron_line = f"{minute} {hour} * * * {cmd} sync && {cmd} remind >> {log} 2>&1"

    console.print("[bold]Add this to your crontab ([bold]crontab -e[/bold]):[/bold]\n")
    console.print(f"  [cyan]{cron_line}[/cyan]\n")
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

    d = existing or {}
    profile = {
        "wake_time":              ask_time(      "  Wake time",                      d.get("wake_time", "07:00")),
        "sleep_time":             ask_time(      "  Sleep time",                     d.get("sleep_time", "23:00")),
        "peak_focus_hours":       ask_range_list("  Peak focus hours (e.g. 09:00-11:00, 14:00-16:00)", ", ".join(d.get("peak_focus_hours", ["09:00-11:00"]))),
        "preferred_block_minutes":ask_minutes(   "  Preferred focus block (minutes)", d.get("preferred_block_minutes", 90)),
        "break_cadence_minutes":  ask_minutes(   "  Break cadence (minutes)",         d.get("break_cadence_minutes", 15)),
        "hard_stop_times":        ask_time_list( "  Hard-stop times (e.g. 12:00, 18:00)", ", ".join(d.get("hard_stop_times", ["18:00"]))),
    }

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
    table.add_row("Wake time",            profile.get("wake_time", "—"))
    table.add_row("Sleep time",           profile.get("sleep_time", "—"))
    table.add_row("Peak focus hours",     ", ".join(profile.get("peak_focus_hours", [])))
    table.add_row("Focus block",          f"{profile.get('preferred_block_minutes', '—')} min")
    table.add_row("Break cadence",        f"{profile.get('break_cadence_minutes', '—')} min")
    table.add_row("Hard stops",           ", ".join(profile.get("hard_stop_times", [])))
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(d: dict) -> str:
    if d["source"] == "canvas":
        return "assignment"
    if d.get("recurrence"):
        return "class"
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
        type_str = {"class": "[blue]class[/blue]", "assignment": "[magenta]assignment[/magenta]"}.get(dtype, "[dim]other[/dim]")
        table.add_row(str(i), d["name"][:45], d.get("course", "")[:20], due_str, in_str, d.get("source", ""), type_str, submitted)

    console.print(table)


def _save_cache(deadlines: list[dict]) -> None:
    items = []
    for d in deadlines:
        item = dict(d)
        if isinstance(item["due_at"], datetime):
            item["due_at"] = item["due_at"].isoformat()
        items.append(item)
    DEADLINES_CACHE.write_text(json.dumps(items, indent=2))


def _load_cache() -> list[dict]:
    if not DEADLINES_CACHE.exists():
        return []
    try:
        items = json.loads(DEADLINES_CACHE.read_text())
        for item in items:
            item["due_at"] = datetime.fromisoformat(item["due_at"])
        return items
    except Exception:
        return []
