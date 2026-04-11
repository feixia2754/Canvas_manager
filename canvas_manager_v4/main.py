"""CLI entry point for canvas-manager-v4."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from .config import get_canvas_config, get_email_config, get_sms_config, get_reminder_config, get_gcal_config
from .canvas_client import CanvasClient, parse_due_date
from .gcal_client import GCalClient
from .ical_parser import parse_ical, merge_with_canvas
from .notifier import Notifier, get_credentials

console = Console()
# Store cache in the project root so it's always found regardless of cwd
DEADLINES_CACHE = Path(__file__).parent.parent / ".canvas_manager_v4_deadlines.json"


@click.group()
def cli() -> None:
    """Canvas Manager V4 — Canvas + Google Calendar → email & SMS reminders."""
    pass


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
      canvas-manager-v4 import-ical ~/Downloads/calendar.ics
      canvas-manager-v4 import-ical
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

    _print_table(merged, title="Merged Deadlines (iCal + Canvas)")
    _save_cache(merged)
    console.print(f"\n[dim]Saved. Run [bold]canvas-manager-v4 remind[/bold] to send notifications.[/dim]")


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
    _print_table(upcoming, title=f"Deadlines in the next {days} days")


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
      canvas-manager-v4 remind
      canvas-manager-v4 remind --preview
      canvas-manager-v4 remind --email-only
      canvas-manager-v4 remind --sms-only
      canvas-manager-v4 remind --days 5
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

    cmd = which("canvas-manager-v4") or "canvas-manager-v4"
    log = Path.home() / ".canvas_manager_v4.log"
    cron_line = f"{minute} {hour} * * * {cmd} sync && {cmd} remind >> {log} 2>&1"

    console.print("[bold]Add this to your crontab ([bold]crontab -e[/bold]):[/bold]\n")
    console.print(f"  [cyan]{cron_line}[/cyan]\n")
    console.print(f"[dim]Logs → {log}[/dim]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_table(deadlines: list[dict], title: str = "Deadlines") -> None:
    table = Table(title=title, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Assignment", style="bold")
    table.add_column("Course", style="cyan")
    table.add_column("Due", style="yellow")
    table.add_column("In", justify="right")
    table.add_column("Source", style="dim")
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
        submitted = "[green]✓ submitted[/green]" if d.get("submitted") else ""
        table.add_row(str(i), d["name"][:45], d.get("course", "")[:20], due_str, in_str, d.get("source", ""), submitted)

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
