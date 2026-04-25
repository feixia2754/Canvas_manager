"""Rule-based smart scheduler for canvas-manager.

Reads habit preferences from HABITS_FILE (falls back to DEFAULT_HABITS)
and upcoming deadlines from the shared deadlines cache, then:

  Pass 1 — place timed GCal events at their exact start_at → due_at slots.
  Pass 2 — place remaining deadlines by priority then urgency, awarding
            peak focus hours to the highest-priority items first.
  Pass 3 — place exam prep study blocks for any exam/quiz within the
            prep window defined by exam_prep_days_before in habits.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path

from . import schedule as _sched
from .schedule import Block

HABITS_FILE: Path = Path.home() / ".canvas_manager" / "habits.json"
DEADLINES_CACHE: Path = Path(__file__).parent.parent / ".canvas_manager_deadlines.json"

_EXAM_KEYWORDS = {"quiz", "exam", "test", "midterm", "final"}
_DEFAULT_PRIORITY = ["class", "assignment", "personal", "study", "other"]

DEFAULT_HABITS: dict = {
    "wake_time": "08:00",
    "sleep_time": "23:00",
    "peak_focus_hours": ["09:00-11:00"],
    "preferred_block_minutes": 90,
    "break_minutes": 15,
    "hard_stops": [{"start": "12:00", "end": "13:00"}],
    "priority_order": _DEFAULT_PRIORITY,
    "exam_prep_days_before": 2,
    "exam_prep_blocks_per_day": 2,
    "exam_prep_block_minutes": 60,
}


def _load_habits() -> tuple[dict, bool]:
    if HABITS_FILE.exists():
        try:
            return json.loads(HABITS_FILE.read_text()), True
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_HABITS), False


def _parse_hhmm(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


def _format_hhmm(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _parse_peak_ranges(ranges: list[str]) -> list[tuple[int, int]]:
    result = []
    for r in ranges:
        if "-" in r:
            parts = r.split("-", 1)
            try:
                result.append((_parse_hhmm(parts[0].strip()), _parse_hhmm(parts[1].strip())))
            except ValueError:
                pass
    return result


def _slot_overlaps_peak(start: int, end: int, peak_ranges: list[tuple[int, int]]) -> bool:
    return any(start < pe and ps < end for ps, pe in peak_ranges)


def _priority_score(deadline: dict, priority_order: list[str]) -> int:
    try:
        return priority_order.index(deadline.get("type", "other"))
    except ValueError:
        return len(priority_order)


def _is_exam(name: str) -> bool:
    return bool(set(name.lower().split()) & _EXAM_KEYWORDS)


def _free_slots(
    work_start: int,
    work_end: int,
    occupied: list[tuple[int, int]],
) -> list[tuple[int, int]]:
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


def _consume_slot(
    free: list[tuple[int, int]],
    start: int,
    duration: int,
    break_min: int,
) -> list[tuple[int, int]]:
    """Remove [start, start+duration] from free and add a break gap."""
    end = start + duration
    tail = end + break_min
    new_free: list[tuple[int, int]] = []
    for fs, fe in free:
        if fe <= start or fs >= end:
            new_free.append((fs, fe))
        else:
            if fs < start:
                new_free.append((fs, start))
            if tail < fe:
                new_free.append((tail, fe))
    return sorted(new_free)


def _load_deadlines() -> list[dict]:
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


def _urgency_score(deadline: dict, target_date: date) -> float:
    if deadline.get("submitted"):
        return float("inf")
    local_midnight = datetime.combine(
        target_date, datetime_time(0, 0)
    ).astimezone(timezone.utc)
    return (deadline["due_at"] - local_midnight).total_seconds()


def _on_target_date(deadline: dict, target_date: date, use_start: bool = False) -> bool:
    if use_start and deadline.get("start_at"):
        dt = deadline["start_at"].astimezone()
    else:
        dt = deadline["due_at"].astimezone()
    return dt.date() == target_date


def _place_block(
    target_date: date,
    free: list[tuple[int, int]],
    title: str,
    block_type: str,
    source: str,
    duration: int,
    break_min: int,
    peak_ranges: list[tuple[int, int]],
    prefer_peak: bool,
) -> tuple[Block | None, list[tuple[int, int]]]:
    """Try to place a block of `duration` minutes into free slots.

    Returns (placed_block, updated_free) or (None, free) if no slot fits.
    Peak slots are tried first when prefer_peak is True.
    """
    eligible = [(s, e) for s, e in free if e - s >= duration]
    if not eligible:
        return None, free

    sorted_slots = sorted(
        eligible,
        key=lambda se: (
            0 if prefer_peak and _slot_overlaps_peak(se[0], se[0] + duration, peak_ranges) else 1,
            se[0],
        ),
    )

    for s, _ in sorted_slots:
        try:
            block = _sched.add_block(target_date, {
                "id": "",
                "start": _format_hhmm(s),
                "end": _format_hhmm(s + duration),
                "title": title,
                "type": block_type,
                "source": source,
            })
            return block, _consume_slot(free, s, duration, break_min)
        except ValueError:
            continue

    return None, free


def generate_plan(
    target_date: date,
    overwrite: bool = False,
    deadline_overrides: list[dict] | None = None,
) -> dict:
    """Generate a study plan for target_date.

    Pass 1: place timed GCal events at their exact slots.
    Pass 2: place remaining deadlines by priority then urgency, with peak
            focus hours awarded to the highest-priority items.
    Pass 3: place exam prep study blocks for exams within the prep window.

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

    wake      = _parse_hhmm(habits["wake_time"])
    sleep     = _parse_hhmm(habits["sleep_time"])
    preferred = habits["preferred_block_minutes"]
    break_min = habits["break_minutes"]

    priority_order   = habits.get("priority_order", _DEFAULT_PRIORITY)
    peak_ranges      = _parse_peak_ranges(habits.get("peak_focus_hours", []))
    exam_prep_days   = habits.get("exam_prep_days_before", 2)
    exam_prep_blocks = habits.get("exam_prep_blocks_per_day", 2)
    exam_prep_mins   = habits.get("exam_prep_block_minutes", 60)

    now_local  = datetime.now().astimezone()
    is_today   = (target_date == now_local.date())
    now_mins   = now_local.hour * 60 + now_local.minute if is_today else 0

    deadlines = deadline_overrides if deadline_overrides is not None else _load_deadlines()

    # Titles already covered — prevents double-booking on re-runs
    already_covered: set[str] = set()
    for b in existing:
        if b["type"] in ("assignment", "personal", "other"):
            already_covered.add(b["title"])
        elif b["type"] == "study":
            already_covered.add(b["title"])
            if b["title"].startswith("Study: "):
                already_covered.add(b["title"][7:])

    placed: list[Block] = []
    skipped: list[str] = []

    # --- Pass 1: timed GCal events at their exact slots ---
    events_today = sorted(
        [
            d for d in deadlines
            if d.get("source") == "gcal"
            and d.get("start_at") and not d.get("submitted")
            and _on_target_date(d, target_date, use_start=True)
        ],
        key=lambda d: d["start_at"],
    )

    for dl in events_today:
        local_start = dl["start_at"].astimezone()
        local_end   = dl["due_at"].astimezone()
        s = local_start.hour * 60 + local_start.minute
        e = local_end.hour * 60 + local_end.minute
        if s >= e:
            skipped.append(dl["name"])
            continue
        try:
            block = _sched.add_block(target_date, {
                "id": "", "start": _format_hhmm(s), "end": _format_hhmm(e),
                "title": dl["name"][:40], "type": "class", "source": "gcal",
            })
            placed.append(block)
        except ValueError:
            skipped.append(dl["name"])

    # Build free slots after Pass 1
    all_blocks = _sched.list_blocks(target_date)
    timed_blocks = [b for b in all_blocks if b["type"] == "class"]
    last_event_end = (
        max(_parse_hhmm(b["end"]) for b in timed_blocks) if timed_blocks else wake
    )
    free_start = max(last_event_end, now_mins) if is_today else last_event_end

    occupied: list[tuple[int, int]] = [
        (_parse_hhmm(b["start"]), _parse_hhmm(b["end"])) for b in all_blocks
    ]
    for hs in habits.get("hard_stops", []):
        occupied.append((_parse_hhmm(hs["start"]), _parse_hhmm(hs["end"])))

    free = _free_slots(free_start, sleep, occupied)

    # --- Pass 2: flexible deadlines sorted by priority then urgency ---
    flexible_today = sorted(
        [
            d for d in deadlines
            if d.get("source") != "gcal"
            and not d.get("submitted")
            and _on_target_date(d, target_date, use_start=False)
        ],
        key=lambda d: (_priority_score(d, priority_order), _urgency_score(d, target_date)),
    )

    for dl in flexible_today:
        name = dl["name"][:40]
        if name in already_covered:
            continue

        prefer_peak = _priority_score(dl, priority_order) <= 1  # class or assignment
        duration = dl.get("duration_minutes", preferred)
        block, free = _place_block(
            target_date, free, name,
            dl.get("type", "assignment"), dl.get("source", "canvas"),
            duration, break_min, peak_ranges, prefer_peak,
        )
        if block:
            placed.append(block)
            already_covered.add(name)
        else:
            skipped.append(dl["name"])

    # --- Pass 3: exam prep study blocks ---
    exams_in_window = [
        d for d in deadlines
        if _is_exam(d.get("name", ""))
        and not d.get("submitted")
        and _exam_in_prep_window(d, target_date, exam_prep_days)
    ]

    for exam in exams_in_window:
        study_title = f"Study: {exam['name'][:33]}"
        if study_title in already_covered:
            continue
        blocks_placed = 0
        for _ in range(exam_prep_blocks):
            block, free = _place_block(
                target_date, free, study_title,
                "study", exam.get("source", "canvas"),
                exam_prep_mins, break_min, peak_ranges, prefer_peak=True,
            )
            if block:
                placed.append(block)
                blocks_placed += 1
            else:
                break
        if blocks_placed == 0:
            skipped.append(study_title)
        already_covered.add(study_title)

    return {
        "blocks": placed,
        "skipped": skipped,
        "habits_used": "custom" if used_custom else "default",
        "existing_blocks": existing_count,
    }


def _filter_relevant_deadlines(
    deadlines: list[dict],
    target_date: date,
    window_days: int = 7,
) -> list[dict]:
    """Return non-submitted deadlines due within window_days of target_date."""
    cutoff = target_date + timedelta(days=window_days)
    return [
        d for d in deadlines
        if not d.get("submitted")
        and target_date <= d["due_at"].astimezone().date() <= cutoff
    ]


def _exam_in_prep_window(deadline: dict, target_date: date, prep_days: int) -> bool:
    """True if target_date falls within [exam_date - prep_days, exam_date)."""
    exam_date = deadline["due_at"].astimezone().date()
    prep_start = exam_date - timedelta(days=prep_days)
    return prep_start <= target_date < exam_date
