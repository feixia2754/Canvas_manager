"""Gemini AI client for canvas-manager.

Three public functions:
  classify_events     — assign/correct type labels on deadline dicts
  estimate_durations  — estimate how long each of today's tasks will take
  improve_schedule    — review and improve a drafted day's block list

All functions accept api_key and model_name explicitly so callers control
which key/model is used. The underlying GenerativeModel is cached per
(api_key, model_name) pair so it is created at most once per process.
"""

from __future__ import annotations

import functools
import json
import uuid
from datetime import date

from rich.console import Console

console = Console()

_VALID_TYPES = ["class", "assignment", "personal", "study", "other"]

_TYPE_DESCRIPTIONS = (
    "class      : lecture, recitation, office hours, lab session\n"
    "assignment : homework, project, quiz, exam, submission, midterm, final\n"
    "personal   : meeting, appointment, social event, errand, seminar, talk\n"
    "study      : dedicated study prep session before an exam or quiz\n"
    "other      : anything that does not clearly fit the above"
)


@functools.lru_cache(maxsize=4)
def _get_client(api_key: str, model_name: str):
    """Return a cached GenerativeModel for the given key and model."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


def classify_events(
    events: list[dict],
    api_key: str,
    model_name: str,
) -> list[dict]:
    """Classify/correct the type field on each deadline dict.

    Returns the same list with updated type fields. Leaves type unchanged
    when Gemini's confidence cannot be determined or the call fails.
    """
    if not api_key:
        console.print("[yellow]  Gemini API key not set — skipping smart classification, types unchanged.[/yellow]")
        return events
    if not events:
        return events

    import google.generativeai as genai

    items_text = "\n".join(
        f"{i}: name={e['name']!r} course={e.get('course', '')!r} source={e.get('source', '')!r}"
        for i, e in enumerate(events)
    )

    prompt = (
        f"You are classifying calendar and academic events into exactly one type.\n\n"
        f"Type definitions:\n{_TYPE_DESCRIPTIONS}\n\n"
        f"Classify each item. Return a JSON array where each element has:\n"
        f'  "index": integer index as shown\n'
        f'  "type": one of {_VALID_TYPES}\n\n'
        f"Items:\n{items_text}"
    )

    try:
        client = _get_client(api_key, model_name)
        response = client.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        results = json.loads(response.text)
        updated = [dict(e) for e in events]
        for r in results:
            idx = r.get("index")
            t = str(r.get("type", "")).lower()
            if isinstance(idx, int) and 0 <= idx < len(updated) and t in _VALID_TYPES:
                updated[idx]["type"] = t
        return updated
    except Exception as exc:
        console.print(f"[yellow]  Gemini classification failed ({exc}) — using fallback types.[/yellow]")
        return events


def estimate_durations(
    events: list[dict],
    habits: dict,
    api_key: str,
    model_name: str,
) -> list[dict]:
    """Estimate work/study duration in minutes for each event.

    Adds a 'duration_minutes' key to each returned dict.
    Falls back to habits['preferred_block_minutes'] when the key is missing
    or the call fails.
    """
    default = habits.get("preferred_block_minutes", 90)

    if not api_key:
        console.print("[yellow]  Gemini API key not set — using default block duration.[/yellow]")
        return [{**e, "duration_minutes": default} for e in events]
    if not events:
        return events

    import google.generativeai as genai

    items_text = "\n".join(
        f"{i}: name={e['name']!r} course={e.get('course', '')!r} type={e.get('type', '')!r}"
        for i, e in enumerate(events)
    )

    prompt = (
        f"You are estimating realistic work time for a student's academic tasks.\n"
        f"Default block size is {default} minutes. Be concise and realistic.\n\n"
        f"Return a JSON array where each element has:\n"
        f'  "index": integer index as shown\n'
        f'  "duration_minutes": integer between 30 and 240\n\n'
        f"Items:\n{items_text}"
    )

    try:
        client = _get_client(api_key, model_name)
        response = client.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        results = json.loads(response.text)
        updated = [dict(e) for e in events]
        for r in results:
            idx = r.get("index")
            dur = r.get("duration_minutes")
            if isinstance(idx, int) and 0 <= idx < len(updated) and isinstance(dur, int) and 30 <= dur <= 240:
                updated[idx]["duration_minutes"] = dur
        for e in updated:
            e.setdefault("duration_minutes", default)
        return updated
    except Exception as exc:
        console.print(f"[yellow]  Gemini duration estimation failed ({exc}) — using default.[/yellow]")
        return [{**e, "duration_minutes": default} for e in events]


def improve_schedule(
    target_date: date,
    blocks: list[dict],
    habits: dict,
    deadlines: list[dict],
    api_key: str,
    model_name: str,
) -> list[dict]:
    """Review and improve a drafted schedule for target_date.

    Gemini may adjust start/end times, title, and type of unlocked blocks.
    Locked blocks (source='manual' or source='gcal') are never modified.
    Returns the updated block list sorted by start time.
    """
    if not api_key:
        console.print("[yellow]  Gemini API key not set — schedule improvement skipped.[/yellow]")
        return blocks
    if not blocks:
        return blocks

    import google.generativeai as genai

    locked_ids = {b["id"] for b in blocks if b.get("source") in ("manual", "gcal")}

    habits_summary = (
        f"wake={habits.get('wake_time')}  sleep={habits.get('sleep_time')}  "
        f"peak_focus={habits.get('peak_focus_hours', [])}  "
        f"priority={habits.get('priority_order', ['class', 'assignment', 'personal', 'study', 'other'])}"
    )

    blocks_payload = json.dumps(
        [
            {
                "id": b["id"],
                "start": b["start"],
                "end": b["end"],
                "title": b["title"],
                "type": b["type"],
                "locked": b["id"] in locked_ids,
            }
            for b in blocks
        ],
        indent=2,
    )

    deadlines_text = "\n".join(
        f"  {d['name']!r}  type={d.get('type', '?')}  due={d['due_at'].astimezone().strftime('%H:%M')}"
        for d in deadlines
    ) or "  (none)"

    prompt = (
        f"You are optimizing a student's daily schedule for {target_date}.\n\n"
        f"Student habits: {habits_summary}\n\n"
        f"Type definitions:\n{_TYPE_DESCRIPTIONS}\n\n"
        f"Deadlines due today:\n{deadlines_text}\n\n"
        f"Current schedule (locked=true blocks must not be changed):\n{blocks_payload}\n\n"
        f"Improve the schedule by:\n"
        f"1. Placing high-priority blocks (class, assignment) in peak focus hours when possible.\n"
        f"2. Grouping related work to minimise context switching.\n"
        f"3. Ensuring adequate breaks between blocks.\n"
        f"4. Correcting any type labels that seem wrong (choose from: {_VALID_TYPES}).\n\n"
        f"Return a JSON array of ALL blocks with fields: id, start (HH:MM), end (HH:MM), title, type.\n"
        f"Do not add or remove blocks. Do not modify locked blocks."
    )

    try:
        client = _get_client(api_key, model_name)
        response = client.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        result = json.loads(response.text)
        block_map = {b["id"]: dict(b) for b in blocks}
        for r in result:
            bid = r.get("id")
            if not bid or bid not in block_map or bid in locked_ids:
                continue
            b = block_map[bid]
            if r.get("start") and r.get("end"):
                b["start"] = r["start"]
                b["end"] = r["end"]
            if r.get("title"):
                b["title"] = r["title"]
            if str(r.get("type", "")).lower() in _VALID_TYPES:
                b["type"] = r["type"].lower()
        return sorted(block_map.values(), key=lambda b: b["start"])
    except Exception as exc:
        console.print(f"[yellow]  Gemini schedule improvement failed ({exc}) — keeping original.[/yellow]")
        return blocks


def parse_schedule_command(
    text: str,
    target_date: date,
    blocks: list[dict],
    habits: dict,
    api_key: str,
    model_name: str,
) -> list[dict]:
    """Apply a natural-language schedule command to the current block list.

    Returns the updated block list. Source for new blocks is set to 'manual'.
    Locked blocks (source='gcal') are never removed unless explicitly named.
    Falls back to the original blocks (with a warning) if the call fails.
    """
    if not api_key:
        console.print("[yellow]  Gemini API key not set — cannot parse free-text schedule commands.[/yellow]")
        return blocks

    import google.generativeai as genai

    habits_summary = (
        f"wake={habits.get('wake_time', '08:00')}  "
        f"sleep={habits.get('sleep_time', '23:00')}  "
        f"peak_focus={habits.get('peak_focus_hours', [])}"
    )

    blocks_json = json.dumps(
        [
            {
                "id": b["id"],
                "start": b["start"],
                "end": b["end"],
                "title": b["title"],
                "type": b["type"],
                "source": b.get("source", "manual"),
            }
            for b in blocks
        ],
        indent=2,
    )

    prompt = (
        f"You are managing a student's daily schedule for {target_date}.\n"
        f"Student habits: {habits_summary}\n\n"
        f"Type definitions:\n{_TYPE_DESCRIPTIONS}\n\n"
        f"Current blocks:\n{blocks_json}\n\n"
        f'Command: "{text}"\n\n'
        f"Apply the command exactly. Rules:\n"
        f"1. Return ALL blocks that should remain (unchanged and modified).\n"
        f'2. For new blocks, use source="manual" and id="new_<n>" (e.g. new_1).\n'
        f"3. Preserve the original id for modified blocks.\n"
        f"4. Times must be in HH:MM (24-hour) format.\n"
        f"5. Type must be one of: {_VALID_TYPES}.\n"
        f'6. Never remove or modify blocks with source="gcal" unless the command explicitly names them.\n\n'
        f"Return a JSON array with fields: id, start, end, title, type, source."
    )

    try:
        client = _get_client(api_key, model_name)
        response = client.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        result = json.loads(response.text)
        if not isinstance(result, list):
            return blocks

        existing_ids = {b["id"] for b in blocks}
        updated: list[dict] = []
        for r in result:
            if not isinstance(r, dict):
                continue
            bid = r.get("id", "")
            if not bid or bid not in existing_ids:
                r["id"] = str(uuid.uuid4())[:8]
                r["source"] = "manual"
            updated.append(r)
        return updated
    except Exception as exc:
        console.print(f"[yellow]  Gemini schedule command failed ({exc}) — no changes made.[/yellow]")
        return blocks
