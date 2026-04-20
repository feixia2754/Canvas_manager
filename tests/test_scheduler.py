"""Tests for canvas_manager.scheduler -- rule-based study planner."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import canvas_manager.schedule as schedule
import canvas_manager.scheduler as scheduler
from canvas_manager.scheduler import (
    _filter_relevant_deadlines,
    _free_slots,
    _urgency_score,
    generate_plan,
)

TODAY  = date(2026, 4, 20)
FUTURE = date(2026, 5, 1)   # a date clearly in the future for event tests

# Times on TODAY
_NOON = "2026-04-20T12:00:00+00:00"
_2PM  = "2026-04-20T14:00:00+00:00"
_4PM  = "2026-04-20T16:00:00+00:00"
_6PM  = "2026-04-20T18:00:00+00:00"
_8PM  = "2026-04-20T20:00:00+00:00"

# Times on FUTURE (May 1)
_F2PM = "2026-05-01T14:00:00+00:00"
_F4PM = "2026-05-01T16:00:00+00:00"
_F8PM = "2026-05-01T20:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plans = tmp_path / "plans"
    monkeypatch.setattr(schedule, "PLANS_DIR", plans)
    monkeypatch.setattr(scheduler, "HABITS_FILE", tmp_path / "habits.json")
    monkeypatch.setattr(scheduler, "DEADLINES_CACHE", tmp_path / "deadlines.json")
    return tmp_path


def _write_habits(tmp_path: Path, habits: dict) -> None:
    (tmp_path / "habits.json").write_text(json.dumps(habits))


def _write_deadlines(tmp_path: Path, deadlines: list[dict]) -> None:
    items = []
    for d in deadlines:
        item = dict(d)
        for key in ("due_at", "start_at"):
            if isinstance(item.get(key), datetime):
                item[key] = item[key].isoformat()
        items.append(item)
    (tmp_path / "deadlines.json").write_text(json.dumps(items))


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _assignment(name: str, due_iso: str, submitted: bool = False) -> dict:
    return {"name": name, "due_at": _dt(due_iso), "submitted": submitted}


def _event(name: str, start_iso: str, end_iso: str, submitted: bool = False,
           etype: str = "class") -> dict:
    return {
        "name": name,
        "due_at": _dt(end_iso),
        "start_at": _dt(start_iso),
        "type": etype,
        "submitted": submitted,
    }


# ---------------------------------------------------------------------------
# _free_slots
# ---------------------------------------------------------------------------

class TestFreeSlots:
    def test_no_occupied_returns_full_window(self):
        assert _free_slots(480, 1380, []) == [(480, 1380)]

    def test_occupied_splits_window(self):
        assert _free_slots(480, 1380, [(720, 780)]) == [(480, 720), (780, 1380)]

    def test_occupied_at_start_trims_left(self):
        assert _free_slots(480, 1380, [(480, 600)]) == [(600, 1380)]

    def test_occupied_at_end_trims_right(self):
        assert _free_slots(480, 1380, [(1200, 1380)]) == [(480, 1200)]

    def test_overlapping_occupied_are_merged(self):
        assert _free_slots(480, 1380, [(600, 720), (680, 800)]) == [(480, 600), (800, 1380)]

    def test_outside_window_clipped(self):
        assert _free_slots(480, 1380, [(0, 200), (1400, 1440)]) == [(480, 1380)]

    def test_fully_occupied_returns_empty(self):
        assert _free_slots(480, 1380, [(0, 1440)]) == []

    def test_multiple_gaps(self):
        result = _free_slots(480, 1380, [(540, 600), (720, 780), (900, 960)])
        assert result == [(480, 540), (600, 720), (780, 900), (960, 1380)]

    def test_work_start_equals_work_end_returns_empty(self):
        assert _free_slots(480, 480, []) == []


# ---------------------------------------------------------------------------
# _urgency_score
# ---------------------------------------------------------------------------

class TestUrgencyScore:
    def test_submitted_returns_inf(self):
        dl = _assignment("HW1", _8PM, submitted=True)
        assert _urgency_score(dl, TODAY) == float("inf")

    def test_closer_deadline_has_lower_score(self):
        sooner = _assignment("A", _2PM)
        later  = _assignment("B", _8PM)
        assert _urgency_score(sooner, TODAY) < _urgency_score(later, TODAY)

    def test_past_deadline_has_negative_score(self):
        dl = _assignment("Late", "2026-04-19T12:00:00+00:00")
        assert _urgency_score(dl, TODAY) < 0


# ---------------------------------------------------------------------------
# _filter_relevant_deadlines
# ---------------------------------------------------------------------------

class TestFilterRelevantDeadlines:
    def test_submitted_excluded(self):
        dl = _assignment("Done", _2PM, submitted=True)
        assert _filter_relevant_deadlines([dl], TODAY) == []

    def test_within_window_included(self):
        dl = _assignment("Due", "2026-04-22T12:00:00+00:00")
        assert _filter_relevant_deadlines([dl], TODAY) == [dl]

    def test_beyond_window_excluded(self):
        dl = _assignment("Far", "2026-05-01T12:00:00+00:00")
        assert _filter_relevant_deadlines([dl], TODAY, window_days=7) == []

    def test_multiple_mixed(self):
        inside  = _assignment("In",   "2026-04-22T12:00:00+00:00")
        outside = _assignment("Out",  "2026-05-10T12:00:00+00:00")
        done    = _assignment("Done", "2026-04-22T12:00:00+00:00", submitted=True)
        assert _filter_relevant_deadlines([inside, outside, done], TODAY) == [inside]


# ---------------------------------------------------------------------------
# generate_plan -- habits loading
# ---------------------------------------------------------------------------

class TestGeneratePlanHabits:
    def test_uses_default_when_no_habits_file(self):
        assert generate_plan(TODAY)["habits_used"] == "default"

    def test_uses_custom_when_habits_file_present(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "07:00", "sleep_time": "22:00",
            "preferred_block_minutes": 60, "break_minutes": 10, "hard_stops": [],
        })
        assert generate_plan(TODAY)["habits_used"] == "custom"

    def test_falls_back_to_default_on_malformed_habits(self, tmp_path):
        (tmp_path / "habits.json").write_text("{{ bad json")
        assert generate_plan(TODAY)["habits_used"] == "default"


# ---------------------------------------------------------------------------
# generate_plan -- timed event placement
# ---------------------------------------------------------------------------

class TestGeneratePlanEvents:
    def test_class_placed_at_exact_time(self, tmp_path):
        _write_deadlines(tmp_path, [_event("Lecture", _F2PM, _F4PM)])
        result = generate_plan(FUTURE)
        assert len(result["blocks"]) == 1
        b = result["blocks"][0]
        assert b["type"] == "class"
        assert b["source"] == "gcal"
        assert b["start"] < b["end"]

    def test_event_type_other_placed_too(self, tmp_path):
        _write_deadlines(tmp_path, [_event("Office Hours", _F2PM, _F4PM, etype="other")])
        result = generate_plan(FUTURE)
        assert len(result["blocks"]) == 1
        assert result["blocks"][0]["type"] == "other"

    def test_submitted_event_skipped(self, tmp_path):
        _write_deadlines(tmp_path, [_event("Done Class", _F2PM, _F4PM, submitted=True)])
        assert generate_plan(FUTURE)["blocks"] == []

    def test_event_on_wrong_date_not_placed(self, tmp_path):
        _write_deadlines(tmp_path, [_event("Other Day", _F2PM, _F4PM)])
        assert generate_plan(TODAY)["blocks"] == []

    def test_past_event_today_not_placed(self, tmp_path):
        # An event whose end time is in the past should be skipped when planning today
        past_start = "2026-04-20T00:00:00+00:00"
        past_end   = "2026-04-20T01:00:00+00:00"
        _write_deadlines(tmp_path, [_event("Early Class", past_start, past_end)])
        result = generate_plan(TODAY)
        assert result["blocks"] == []

    def test_conflicting_event_skipped(self, tmp_path):
        _write_deadlines(tmp_path, [_event("Class", _F2PM, _F4PM)])
        _write_habits(tmp_path, {
            "wake_time": "00:00", "sleep_time": "23:59",
            "preferred_block_minutes": 30, "break_minutes": 5, "hard_stops": [],
        })
        result = generate_plan(FUTURE)
        assert isinstance(result["blocks"], list)


# ---------------------------------------------------------------------------
# generate_plan -- assignment placement
# ---------------------------------------------------------------------------

class TestGeneratePlanAssignments:
    def test_no_deadlines_places_nothing(self):
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == []

    def test_assignment_due_today_placed(self, tmp_path):
        _write_deadlines(tmp_path, [_assignment("HW1", _8PM)])
        result = generate_plan(TODAY)
        assert len(result["blocks"]) == 1
        assert result["blocks"][0]["title"] == "Study: HW1"
        assert result["blocks"][0]["type"] == "study"
        assert result["blocks"][0]["source"] == "ai"

    def test_assignment_not_due_today_not_placed(self, tmp_path):
        _write_deadlines(tmp_path, [_assignment("HW1", "2026-04-22T12:00:00+00:00")])
        assert generate_plan(TODAY)["blocks"] == []

    def test_earlier_due_time_placed_first(self, tmp_path):
        _write_deadlines(tmp_path, [
            _assignment("Later",  _8PM),
            _assignment("Sooner", _2PM),
        ])
        result = generate_plan(TODAY)
        titles = [b["title"] for b in result["blocks"]]
        assert titles.index("Study: Sooner") < titles.index("Study: Later")

    def test_assignment_placed_after_last_class(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00", "sleep_time": "23:00",
            "preferred_block_minutes": 60, "break_minutes": 10, "hard_stops": [],
        })
        _write_deadlines(tmp_path, [
            _event("Lecture", _F2PM, _F4PM),
            _assignment("HW1", _F8PM),
        ])
        result = generate_plan(FUTURE)
        blocks = {b["title"]: b for b in result["blocks"]}
        assert "Lecture" in blocks
        assert "Study: HW1" in blocks
        assert blocks["Study: HW1"]["start"] >= blocks["Lecture"]["end"]

    def test_skips_when_no_slot_fits(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00", "sleep_time": "09:00",  # only 60 min
            "preferred_block_minutes": 90,
            "break_minutes": 15, "hard_stops": [],
        })
        _write_deadlines(tmp_path, [_assignment("HW1", _8PM)])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == ["HW1"]

    def test_break_consumed_between_two_assignments(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00", "sleep_time": "23:00",
            "preferred_block_minutes": 60, "break_minutes": 30, "hard_stops": [],
        })
        _write_deadlines(tmp_path, [
            _assignment("A", _2PM),
            _assignment("B", _8PM),
        ])
        result = generate_plan(TODAY)
        assert len(result["blocks"]) == 2
        b1_end   = _parse_mm(result["blocks"][0]["end"])
        b2_start = _parse_mm(result["blocks"][1]["start"])
        assert b2_start >= b1_end + 30

    def test_skips_submitted_assignment(self, tmp_path):
        _write_deadlines(tmp_path, [_assignment("Done", _8PM, submitted=True)])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == []

    def test_hard_stops_block_all_slots(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00", "sleep_time": "23:00",
            "preferred_block_minutes": 90,
            "break_minutes": 15,
            "hard_stops": [{"start": "08:00", "end": "23:00"}],
        })
        _write_deadlines(tmp_path, [_assignment("HW1", _8PM)])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == ["HW1"]


# ---------------------------------------------------------------------------
# generate_plan -- existing blocks and overwrite
# ---------------------------------------------------------------------------

class TestGeneratePlanExisting:
    def test_existing_blocks_reported(self):
        schedule.add_block(TODAY, {
            "id": "", "start": "09:00", "end": "10:00",
            "title": "Manual", "type": "class", "source": "manual",
        })
        assert generate_plan(TODAY)["existing_blocks"] == 1

    def test_overwrite_clears_existing(self):
        schedule.add_block(TODAY, {
            "id": "", "start": "09:00", "end": "10:00",
            "title": "Manual", "type": "class", "source": "manual",
        })
        result = generate_plan(TODAY, overwrite=True)
        assert result["existing_blocks"] == 0
        assert schedule.load_plan(TODAY) == result["blocks"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_mm(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m
