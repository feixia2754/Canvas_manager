"""Tests for mana CLI commands: export, todo, send --preview, plan smoke."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

import canvas_manager.schedule as schedule
import canvas_manager.scheduler as scheduler
from canvas_manager.main import cli
import canvas_manager.main as main_mod

TODAY = date(2026, 4, 20)
TODAY_STR = "2026-04-20"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plans = tmp_path / "plans"
    cache = tmp_path / "deadlines.json"
    monkeypatch.setattr(schedule, "PLANS_DIR", plans)
    monkeypatch.setattr(scheduler, "HABITS_FILE", tmp_path / "habits.json")
    monkeypatch.setattr(scheduler, "DEADLINES_CACHE", cache)
    monkeypatch.setattr(main_mod, "DEADLINES_CACHE", cache)
    return tmp_path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _seed_blocks(tmp_path: Path, blocks: list[dict]) -> None:
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / f"{TODAY_STR}.json").write_text(json.dumps(blocks, indent=2))


def _seed_cache(tmp_path: Path, deadlines: list[dict]) -> None:
    items = [{**d, "due_at": d["due_at"].isoformat()} for d in deadlines]
    (tmp_path / "deadlines.json").write_text(json.dumps(items))


def _make_deadline(name: str, hours: int = 2, dtype: str = "assignment") -> dict:
    due = datetime.now(tz=timezone.utc) + timedelta(hours=hours)
    return {"name": name, "due_at": due, "course": "15-601", "url": "",
            "source": "canvas", "submitted": False, "type": dtype}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

class TestExportCommand:
    def test_no_blocks_shows_helpful_message(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["export", "--date", TODAY_STR])
        assert result.exit_code == 0
        assert "mana plan" in result.output

    def test_exports_ics_file(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Study Session", "type": "study", "source": "manual"},
        ])
        out = tmp_path / "out.ics"
        result = runner.invoke(cli, ["export", "--date", TODAY_STR, "--out", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text()
        assert "BEGIN:VCALENDAR" in content
        assert "Study Session" in content

    def test_default_output_filename(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Lecture", "type": "class", "source": "gcal"},
        ])
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["export", "--date", TODAY_STR])
            assert result.exit_code == 0
            assert Path(f"schedule-{TODAY_STR}.ics").exists()

    def test_ics_contains_correct_date(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "10:00", "end": "11:00",
             "title": "HW Review", "type": "assignment", "source": "canvas"},
        ])
        out = tmp_path / "check.ics"
        runner.invoke(cli, ["export", "--date", TODAY_STR, "--out", str(out)])
        assert "20260420" in out.read_text()


# ---------------------------------------------------------------------------
# todo
# ---------------------------------------------------------------------------

class TestTodoCommand:
    def test_empty_cache_shows_warning(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["todo"])
        assert result.exit_code == 0
        assert "No cache found" in result.output

    def test_shows_assignment_count(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [_make_deadline("HW1"), _make_deadline("HW2")])
        result = runner.invoke(cli, ["todo"])
        assert result.exit_code == 0
        assert "2 assignments" in result.output

    def test_shows_class_count(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [_make_deadline("Lecture", dtype="class")])
        result = runner.invoke(cli, ["todo"])
        assert result.exit_code == 0
        assert "1 class" in result.output

    def test_assignments_flag_shows_table(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [_make_deadline("HW1")])
        result = runner.invoke(cli, ["todo", "--assignments"])
        assert result.exit_code == 0
        assert "HW1" in result.output

    def test_classes_flag_shows_table(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [_make_deadline("Lecture", dtype="class")])
        result = runner.invoke(cli, ["todo", "--classes"])
        assert result.exit_code == 0
        assert "Lecture" in result.output

    def test_days_flag_excludes_far_future(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [_make_deadline("Far HW", hours=10 * 24)])
        result = runner.invoke(cli, ["todo", "--days", "3"])
        assert result.exit_code == 0
        assert "Nothing due" in result.output

    def test_nothing_due_empty_cache(self, runner: CliRunner, tmp_path: Path):
        _seed_cache(tmp_path, [])
        result = runner.invoke(cli, ["todo"])
        assert result.exit_code == 0
        assert "Nothing due" in result.output

    def test_submitted_still_shown_in_count(self, runner: CliRunner, tmp_path: Path):
        dl = _make_deadline("HW Submitted")
        dl["submitted"] = True
        _seed_cache(tmp_path, [dl])
        result = runner.invoke(cli, ["todo"])
        assert result.exit_code == 0
        assert "1 assignment" in result.output


# ---------------------------------------------------------------------------
# send --preview
# ---------------------------------------------------------------------------

class TestSendPreview:
    def test_no_blocks_shows_helpful_message(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["send", "--preview", "--date", TODAY_STR])
        assert result.exit_code == 0
        assert "mana plan" in result.output

    def test_preview_shows_block_title(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Morning Study", "type": "study", "source": "manual"},
        ])
        result = runner.invoke(cli, ["send", "--preview", "--date", TODAY_STR])
        assert result.exit_code == 0
        assert "Morning Study" in result.output

    def test_preview_shows_nothing_sent(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Lecture", "type": "class", "source": "gcal"},
        ])
        result = runner.invoke(cli, ["send", "--preview", "--date", TODAY_STR])
        assert result.exit_code == 0
        assert "nothing sent" in result.output.lower()

    def test_preview_shows_summary_counts(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Lecture", "type": "class", "source": "gcal"},
            {"id": "blk_aa000002", "start": "11:00", "end": "12:00",
             "title": "HW1", "type": "assignment", "source": "canvas"},
        ])
        result = runner.invoke(cli, ["send", "--preview", "--date", TODAY_STR])
        assert result.exit_code == 0
        assert "1 class" in result.output
        assert "1 assignment" in result.output


# ---------------------------------------------------------------------------
# plan (smoke — no credentials, no Gemini key)
# ---------------------------------------------------------------------------

class TestPlanSmoke:
    def test_plan_empty_exits_cleanly(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["plan", "--date", TODAY_STR])
        assert result.exit_code == 0

    def test_plan_overwrite_flag_accepted(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["plan", "--date", TODAY_STR, "--overwrite"])
        assert result.exit_code == 0

    def test_plan_export_creates_ics(self, runner: CliRunner, tmp_path: Path):
        _seed_blocks(tmp_path, [
            {"id": "blk_aa000001", "start": "09:00", "end": "10:00",
             "title": "Study", "type": "study", "source": "manual"},
        ])
        out = tmp_path / "plan.ics"
        result = runner.invoke(cli, [
            "plan", "--date", TODAY_STR, "--export", "--out", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()
