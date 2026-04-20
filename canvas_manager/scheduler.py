"""Rule-based smart scheduler for canvas-manager.

Reads habit preferences from HABITS_FILE (falls back to DEFAULT_HABITS)
and upcoming deadlines from the shared deadlines cache, then greedily
places study blocks into the day's free time sorted by urgency.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path

from . import schedule as _sched
from .schedule import Block

HABITS_FILE: Path = Path.home() / ".canvas_manager" / "habits.json"
DEADLINES_CACHE: Path = Path(__file__).parent.parent / ".canvas_manager_deadlines.json"

DEFAULT_HABITS: dict = {
    "wake_time": "08:00",
    "sleep_time": "23:00",
    "preferred_block_minutes": 90,
    "break_minutes": 15,
    "hard_stops": [{"start": "12:00", "end": "13:00"}],
}


def _load_habits() -> tuple[dict, bool]:
    """Load habits from HABITS_FILE or fall back to DEFAULT_HABITS.

    Returns:
        Tuple of (habits_dict, used_custom) where used_custom is True
        when HABITS_FILE was found and loaded successfully.
    """
    if HABITS_FILE.exists():
        try:
            return json.loads(HABITS_FILE.read_text()), True
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_HABITS), False


def _parse_hhmm(s: str) -> int:
    """Convert "HH:MM" to minutes since midnight.

    Args:
        s: Time string in "HH:MM" 24-hour format.

    Returns:
        Integer minutes since midnight.
    """
    h, m = map(int, s.split(":"))
    return h * 60 + m


def _format_hhmm(mins: int) -> str:
    """Convert minutes since midnight to "HH:MM".

    Args:
        mins: Minutes since midnight (0–1439).

    Returns:
        Time string in "HH:MM" 24-hour format.
    """
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _free_slots(
    work_start: int,
    work_end: int,
    occupied: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Subtract occupied ranges from [work_start, work_end].

    Occupied ranges may overlap or be unsorted; they are merged before
    subtraction and clipped to the work window.

    Args:
        work_start: Start of the work window in minutes since midnight.
        work_end: End of the work window in minutes since midnight.
        occupied: List of (start, end) minute tuples already taken.

    Returns:
        Sorted list of non-overlapping (start, end) free ranges.
    """
    if not occupied:
        return [(work_start, work_end)] if work_start < work_end else []

    clipped = [(max(s, work_start), min(e, work_end)) for s, e in occupied]
    clipped = [(s, e) for s, e in clipped if s < e]
    clipped.sort()

    merged: list[list[int]] = []
    for s, e in clipped:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    free: list[tuple[int, int]] = []
    cursor = work_start
    for s, e in merged:
        if cursor < s:
            free.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < work_end:
        free.append((cursor, work_end))

    return free


def _load_deadlines() -> list[dict]:
    """Load cached deadlines from DEADLINES_CACHE.

    Returns:
        List of deadline dicts with timezone-aware due_at datetimes,
        or [] if the file does not exist or cannot be parsed.
    """
    if not DEADLINES_CACHE.exists():
        return []
    try:
        items = json.loads(DEADLINES_CACHE.read_text())
        for item in items:
            item["due_at"] = datetime.fromisoformat(item["due_at"])
        return items
    except Exception:
        return []


def _urgency_score(deadline: dict, target_date: date) -> float:
    """Return urgency score; lower = more urgent.

    Uses seconds between local midnight of target_date and due_at.
    Submitted deadlines always return float("inf").

    Args:
        deadline: Deadline dict containing at least "due_at" and "submitted".
        target_date: The day for which the plan is being generated.

    Returns:
        Float score; smaller means the deadline is sooner.
    """
    if deadline.get("submitted"):
        return float("inf")
    local_midnight = datetime.combine(
        target_date, datetime_time(0, 0)
    ).astimezone(timezone.utc)
    return (deadline["due_at"] - local_midnight).total_seconds()


def _filter_relevant_deadlines(
    deadlines: list[dict],
    target_date: date,
    window_days: int = 7,
) -> list[dict]:
    """Keep only upcoming, non-submitted deadlines within the planning window.

    Args:
        deadlines: Full list of deadline dicts.
        target_date: Start of the window (inclusive, local midnight).
        window_days: Number of days ahead to include (default 7).

    Returns:
        Filtered list; input is not modified.
    """
    local_start = datetime.combine(
        target_date, datetime_time(0, 0)
    ).astimezone(timezone.utc)
    local_end = datetime.combine(
        target_date + timedelta(days=window_days), datetime_time(23, 59)
    ).astimezone(timezone.utc)
    return [
        d for d in deadlines
        if not d.get("submitted") and local_start <= d["due_at"] <= local_end
    ]


def generate_plan(target_date: date, overwrite: bool = False) -> dict:
    """Generate a study plan for target_date.

    Loads habits, reads existing blocks, computes free slots, then greedily
    places study blocks for upcoming deadlines sorted by urgency (soonest first).

    Args:
        target_date: The day to plan.
        overwrite: If True, clears all existing blocks before planning.

    Returns:
        Dict with keys:
            blocks          -- list[Block] added this run
            skipped         -- list[str] deadline names that did not fit
            habits_used     -- "custom" or "default"
            existing_blocks -- int count of pre-existing blocks kept
    """
    habits, used_custom = _load_habits()

    existing = _sched.list_blocks(target_date)
    existing_count = len(existing)
    if overwrite:
        _sched.save_plan(target_date, [])
        existing = []
        existing_count = 0

    wake = _parse_hhmm(habits["wake_time"])
    sleep = _parse_hhmm(habits["sleep_time"])
    preferred = habits["preferred_block_minutes"]
    break_min = habits["break_minutes"]

    occupied: list[tuple[int, int]] = [
        (_parse_hhmm(b["start"]), _parse_hhmm(b["end"])) for b in existing
    ]
    for hs in habits.get("hard_stops", []):
        occupied.append((_parse_hhmm(hs["start"]), _parse_hhmm(hs["end"])))

    free = _free_slots(wake, sleep, occupied)

    deadlines = _load_deadlines()
    relevant = _filter_relevant_deadlines(deadlines, target_date)
    relevant.sort(key=lambda d: _urgency_score(d, target_date))

    placed: list[Block] = []
    skipped: list[str] = []

    for dl in relevant:
        placed_this = False
        new_free: list[tuple[int, int]] = []
        for s, e in free:
            if not placed_this and e - s >= preferred:
                block = _sched.add_block(target_date, {
                    "id": "",
                    "start": _format_hhmm(s),
                    "end": _format_hhmm(s + preferred),
                    "title": f"Study: {dl['name'][:40]}",
                    "type": "study",
                    "source": "ai",
                })
                placed.append(block)
                placed_this = True
                consumed_end = s + preferred + break_min
                if consumed_end < e:
                    new_free.append((consumed_end, e))
            else:
                new_free.append((s, e))
        free = new_free
        if not placed_this:
            skipped.append(dl["name"])

    return {
        "blocks": placed,
        "skipped": skipped,
        "habits_used": "custom" if used_custom else "default",
        "existing_blocks": existing_count,
    }
