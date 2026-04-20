"""Tests for canvas-manager schedule CLI commands."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

import canvas_manager.schedule as schedule
from canvas_manager.main import cli

TODAY = date(2026, 4, 20)
TODAY_STR = "2026-04-20"


@pytest.fixture(autouse=True)
def isolated_plans_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "plans"
    monkeypatch.setattr(schedule, "PLANS_DIR", d)
    return d


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _add(runner: CliRunner, title: str, start: str, end: str,
         block_type: str = "study", date_str: str = TODAY_STR) -> str:
    """Add a block via CLI and return its generated id."""
    result = runner.invoke(cli, [
        "schedule", "add", title,
        "--from", start, "--to", end,
        "--type", block_type, "--date", date_str,
    ])
    assert result.exit_code == 0, result.output
    m = re.search(r"(blk_[0-9a-f]{8})", result.output)
    assert m, f"No block id in output: {result.output}"
    return m.group(1)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_empty_shows_message(runner: CliRunner):
    result = runner.invoke(cli, ["schedule", "list", "--date", TODAY_STR])
    assert result.exit_code == 0
    assert "No blocks scheduled" in result.output


def test_list_shows_blocks_sorted_by_start(runner: CliRunner):
    _add(runner, "Afternoon", "14:00", "15:00")
    _add(runner, "Morning",   "09:00", "10:00")
    result = runner.invoke(cli, ["schedule", "list", "--date", TODAY_STR])
    assert result.exit_code == 0
    assert result.output.index("Morning") < result.output.index("Afternoon")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def test_add_creates_block_and_prints_confirmation(runner: CliRunner):
    result = runner.invoke(cli, [
        "schedule", "add", "Study Session",
        "--from", "09:00", "--to", "10:00", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    assert "Added block" in result.output
    assert "09:00" in result.output
    assert "10:00" in result.output


def test_add_conflict_exits_nonzero(runner: CliRunner):
    _add(runner, "First", "09:00", "11:00")
    result = runner.invoke(cli, [
        "schedule", "add", "Overlap",
        "--from", "10:00", "--to", "12:00", "--date", TODAY_STR,
    ])
    assert result.exit_code != 0
    assert "overlap" in result.output.lower()


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------

def test_move_to_only_preserves_duration(runner: CliRunner):
    block_id = _add(runner, "Lecture", "09:00", "11:00")  # 2h block
    result = runner.invoke(cli, [
        "schedule", "move", block_id, "--to", "13:00", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    b = next(b for b in schedule.load_plan(TODAY) if b["id"] == block_id)
    assert b["start"] == "11:00"
    assert b["end"]   == "13:00"


def test_move_both_from_and_to_uses_literally(runner: CliRunner):
    block_id = _add(runner, "Lecture", "09:00", "10:00")
    result = runner.invoke(cli, [
        "schedule", "move", block_id,
        "--from", "11:00", "--to", "13:00", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    b = next(b for b in schedule.load_plan(TODAY) if b["id"] == block_id)
    assert b["start"] == "11:00"
    assert b["end"]   == "13:00"


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_update_changes_only_specified_fields(runner: CliRunner):
    block_id = _add(runner, "Original", "09:00", "10:00", block_type="study")
    result = runner.invoke(cli, [
        "schedule", "update", block_id, "--title", "Renamed", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    b = next(b for b in schedule.load_plan(TODAY) if b["id"] == block_id)
    assert b["title"]  == "Renamed"
    assert b["type"]   == "study"    # unchanged
    assert b["start"]  == "09:00"   # unchanged
    assert b["source"] == "manual"  # unchanged


def test_update_unknown_id_exits_nonzero(runner: CliRunner):
    result = runner.invoke(cli, [
        "schedule", "update", "blk_nonexistent",
        "--title", "X", "--date", TODAY_STR,
    ])
    assert result.exit_code != 0
    assert "Error" in result.output


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_delete_yes_removes_block(runner: CliRunner):
    block_id = _add(runner, "To Remove", "09:00", "10:00")
    result = runner.invoke(cli, [
        "schedule", "delete", block_id, "--yes", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    assert "Deleted" in result.output
    assert not any(b["id"] == block_id for b in schedule.load_plan(TODAY))


def test_delete_unknown_id_exits_nonzero(runner: CliRunner):
    result = runner.invoke(cli, [
        "schedule", "delete", "blk_nonexistent", "--yes", "--date", TODAY_STR,
    ])
    assert result.exit_code != 0
    assert "Error" in result.output


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

def test_clear_yes_wipes_all_blocks(runner: CliRunner):
    _add(runner, "Block A", "09:00", "10:00")
    _add(runner, "Block B", "11:00", "12:00")
    result = runner.invoke(cli, [
        "schedule", "clear", "--yes", "--date", TODAY_STR,
    ])
    assert result.exit_code == 0
    assert "Cleared" in result.output
    assert schedule.load_plan(TODAY) == []
