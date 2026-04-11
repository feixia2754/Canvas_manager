"""Parse an iCal (.ics) file and extract deadlines."""

from __future__ import annotations

import re
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

from icalendar import Calendar


def parse_ical(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    cal = Calendar.from_ical(raw)

    deadlines: list[dict] = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", ""))
        description = str(component.get("DESCRIPTION", ""))
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        url = str(component.get("URL", ""))

        due_dt = _to_aware_datetime(dtend or dtstart)
        if due_dt is None or due_dt < datetime.now(tz=timezone.utc):
            continue

        deadlines.append({
            "name": summary,
            "due_at": due_dt,
            "course": _extract_course(summary, description),
            "url": url,
            "source": "ical",
        })

    deadlines.sort(key=lambda d: d["due_at"])
    return deadlines


def _to_aware_datetime(dt_prop) -> Optional[datetime]:
    if dt_prop is None:
        return None
    val = dt_prop.dt
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, 23, 59, tzinfo=timezone.utc)
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
    return "Unknown"


def merge_with_canvas(ical_deadlines: list[dict], canvas_assignments: list[dict]) -> list[dict]:
    from datetime import timedelta

    canvas_items = []
    for a in canvas_assignments:
        due = datetime.fromisoformat(a["due_at"].replace("Z", "+00:00"))
        canvas_items.append({
            "name": a["name"],
            "due_at": due,
            "course": a.get("_course_name", "Unknown"),
            "url": a.get("html_url", ""),
            "source": "canvas",
        })

    merged = list(canvas_items)
    tolerance = timedelta(hours=2)

    for item in ical_deadlines:
        is_dup = any(
            _similar(item["name"], c["name"]) and abs(item["due_at"] - c["due_at"]) <= tolerance
            for c in canvas_items
        )
        if not is_dup:
            merged.append(item)

    merged.sort(key=lambda d: d["due_at"])
    return merged


def _similar(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a in b or b in a:
        return True
    wa = set(re.findall(r"\w+", a))
    wb = set(re.findall(r"\w+", b))
    if not wa or not wb:
        return False
    return len(wa & wb) / max(len(wa), len(wb)) >= 0.6
