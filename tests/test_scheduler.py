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

TODAY = date(2026, 4, 20)


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
        if isinstance(item["due_at"], datetime):
            item["due_at"] = item["due_at"].isoformat()
        items.append(item)
    (tmp_path / "deadlines.json").write_text(json.dumps(items))


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _deadline(name: str, due_iso: str, submitted: bool = False) -> dict:
    return {
        "name": name,
        "due_at": _dt(due_iso),
        "submitted": submitted,
    }


# ---------------------------------------------------------------------------
# _free_slots
# ---------------------------------------------------------------------------

class TestFreeSlots:
    def test_no_occupied_returns_full_window(self):
        assert _free_slots(480, 1380, []) == [(480, 1380)]

    def test_occupied_splits_window(self):
        free = _free_slots(480, 1380, [(720, 780)])
        assert free == [(480, 720), (780, 1380)]

    def test_occupied_at_start_trims_left(self):
        free = _free_slots(480, 1380, [(480, 600)])
        assert free == [(600, 1380)]

    def test_occupied_at_end_trims_right(self):
        free = _free_slots(480, 1380, [(1200, 1380)])
        assert free == [(480, 1200)]

    def test_overlapping_occupied_are_merged(self):
        free = _free_slots(480, 1380, [(600, 720), (680, 800)])
        assert free == [(480, 600), (800, 1380)]

    def test_outside_window_occupied_clipped(self):
        free = _free_slots(480, 1380, [(0, 200), (1400, 1440)])
        assert free == [(480, 1380)]

    def test_fully_occupied_returns_empty(self):
        assert _free_slots(480, 1380, [(0, 1440)]) == []

    def test_multiple_gaps(self):
        free = _free_slots(480, 1380, [(540, 600), (720, 780), (900, 960)])
        assert free == [(480, 540), (600, 720), (780, 900), (960, 1380)]

    def test_work_start_equals_work_end_returns_empty(self):
        assert _free_slots(480, 480, []) == []


# ---------------------------------------------------------------------------
# _urgency_score
# ---------------------------------------------------------------------------

class TestUrgencyScore:
    def test_submitted_returns_inf(self):
        dl = _deadline("HW1", "2026-04-21T23:59:00+00:00", submitted=True)
        assert _urgency_score(dl, TODAY) == float("inf")

    def test_closer_deadline_has_lower_score(self):
        sooner = _deadline("A", "2026-04-21T00:00:00+00:00")
        later  = _deadline("B", "2026-04-25T00:00:00+00:00")
        assert _urgency_score(sooner, TODAY) < _urgency_score(later, TODAY)

    def test_past_deadline_has_negative_score(self):
        dl = _deadline("Late", "2026-04-19T00:00:00+00:00")
        assert _urgency_score(dl, TODAY) < 0


# ---------------------------------------------------------------------------
# _filter_relevant_deadlines
# ---------------------------------------------------------------------------

class TestFilterRelevantDeadlines:
    def test_submitted_excluded(self):
        dl = _deadline("Done", "2026-04-21T12:00:00+00:00", submitted=True)
        assert _filter_relevant_deadlines([dl], TODAY) == []

    def test_within_window_included(self):
        dl = _deadline("Due", "2026-04-22T12:00:00+00:00")
        result = _filter_relevant_deadlines([dl], TODAY)
        assert result == [dl]

    def test_beyond_window_excluded(self):
        dl = _deadline("Far", "2026-05-01T12:00:00+00:00")
        assert _filter_relevant_deadlines([dl], TODAY, window_days=7) == []

    def test_boundary_day_included(self):
        dl = _deadline("Edge", "2026-04-27T23:59:00+00:00")
        result = _filter_relevant_deadlines([dl], TODAY, window_days=7)
        assert result == [dl]

    def test_multiple_mixed(self):
        inside   = _deadline("In",   "2026-04-22T12:00:00+00:00")
        outside  = _deadline("Out",  "2026-05-10T12:00:00+00:00")
        done     = _deadline("Done", "2026-04-22T12:00:00+00:00", submitted=True)
        result = _filter_relevant_deadlines([inside, outside, done], TODAY)
        assert result == [inside]


# ---------------------------------------------------------------------------
# generate_plan -- habits loading
# ---------------------------------------------------------------------------

class TestGeneratePlanHabits:
    def test_uses_default_when_no_habits_file(self):
        result = generate_plan(TODAY)
        assert result["habits_used"] == "default"

    def test_uses_custom_when_habits_file_present(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "07:00",
            "sleep_time": "22:00",
            "preferred_block_minutes": 60,
            "break_minutes": 10,
            "hard_stops": [],
        })
        result = generate_plan(TODAY)
        assert result["habits_used"] == "custom"

    def test_falls_back_to_default_on_malformed_habits(self, tmp_path):
        (tmp_path / "habits.json").write_text("{{ bad json")
        result = generate_plan(TODAY)
        assert result["habits_used"] == "default"


# ---------------------------------------------------------------------------
# generate_plan -- block placement
# ---------------------------------------------------------------------------

class TestGeneratePlanPlacement:
    def test_no_deadlines_places_nothing(self):
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == []

    def test_places_block_for_urgent_deadline(self, tmp_path):
        _write_deadlines(tmp_path, [
            _deadline("HW1", "2026-04-22T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        assert len(result["blocks"]) == 1
        assert result["blocks"][0]["title"] == "Study: HW1"
        assert result["blocks"][0]["type"] == "study"
        assert result["blocks"][0]["source"] == "ai"

    def test_places_soonest_first(self, tmp_path):
        _write_deadlines(tmp_path, [
            _deadline("Later",  "2026-04-26T12:00:00+00:00"),
            _deadline("Sooner", "2026-04-21T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        titles = [b["title"] for b in result["blocks"]]
        assert titles.index("Study: Sooner") < titles.index("Study: Later")

    def test_skips_when_no_free_slot_fits(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00",
            "sleep_time": "09:00",   # only 60 min window
            "preferred_block_minutes": 90,
            "break_minutes": 15,
            "hard_stops": [],
        })
        _write_deadlines(tmp_path, [
            _deadline("HW1", "2026-04-22T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == ["HW1"]

    def test_skips_submitted(self, tmp_path):
        _write_deadlines(tmp_path, [
            _deadline("Done", "2026-04-22T12:00:00+00:00", submitted=True),
        ])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == []

    def test_break_gap_consumed_between_blocks(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00",
            "sleep_time": "23:00",
            "preferred_block_minutes": 60,
            "break_minutes": 30,
            "hard_stops": [],
        })
        _write_deadlines(tmp_path, [
            _deadline("A", "2026-04-21T12:00:00+00:00"),
            _deadline("B", "2026-04-22T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        assert len(result["blocks"]) == 2
        # second block starts at least 30 min after first ends
        b1_end   = int(result["blocks"][0]["end"].split(":")[0]) * 60 + int(result["blocks"][0]["end"].split(":")[1])
        b2_start = int(result["blocks"][1]["start"].split(":")[0]) * 60 + int(result["blocks"][1]["start"].split(":")[1])
        assert b2_start >= b1_end + 30


# ---------------------------------------------------------------------------
# generate_plan -- existing blocks and overwrite
# ---------------------------------------------------------------------------

class TestGeneratePlanExisting:
    def test_existing_blocks_reported(self):
        schedule.add_block(TODAY, {
            "id": "", "start": "09:00", "end": "10:00",
            "title": "Manual", "type": "class", "source": "manual",
        })
        result = generate_plan(TODAY)
        assert result["existing_blocks"] == 1

    def test_existing_block_occupies_slot(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00",
            "sleep_time": "10:00",  # 2-hour window
            "preferred_block_minutes": 90,
            "break_minutes": 15,
            "hard_stops": [],
        })
        # Pre-fill the only slot that would fit (08:00–09:30)
        schedule.add_block(TODAY, {
            "id": "", "start": "08:00", "end": "09:30",
            "title": "Class", "type": "class", "source": "manual",
        })
        _write_deadlines(tmp_path, [
            _deadline("HW1", "2026-04-22T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        assert result["skipped"] == ["HW1"]

    def test_overwrite_clears_existing_blocks(self):
        schedule.add_block(TODAY, {
            "id": "", "start": "09:00", "end": "10:00",
            "title": "Manual", "type": "class", "source": "manual",
        })
        result = generate_plan(TODAY, overwrite=True)
        assert result["existing_blocks"] == 0
        assert schedule.load_plan(TODAY) == result["blocks"]

    def test_hard_stops_respected(self, tmp_path):
        _write_habits(tmp_path, {
            "wake_time": "08:00",
            "sleep_time": "23:00",
            "preferred_block_minutes": 90,
            "break_minutes": 15,
            "hard_stops": [{"start": "08:00", "end": "23:00"}],  # block everything
        })
        _write_deadlines(tmp_path, [
            _deadline("HW1", "2026-04-22T12:00:00+00:00"),
        ])
        result = generate_plan(TODAY)
        assert result["blocks"] == []
        assert result["skipped"] == ["HW1"]
