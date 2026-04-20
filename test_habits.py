"""Tests for canvas-manager habits command."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

PROJECT_DIR = Path(__file__).parent

# Simulated answers for a full questionnaire run (one answer per prompt)
_FULL_INPUT = "\n".join([
    "07:30",        # wake time
    "23:00",        # sleep time
    "09:00-11:00",  # peak focus hours
    "90",           # block length
    "15",           # break cadence
    "18:00",        # hard stop times
]) + "\n"


class TestHabitsCommand(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()
        from canvas_manager.main import HABITS_FILE
        self._habits_file = HABITS_FILE
        self._original = HABITS_FILE.read_text() if HABITS_FILE.exists() else None

    def tearDown(self):
        if self._original is not None:
            self._habits_file.write_text(self._original)
        elif self._habits_file.exists():
            self._habits_file.unlink()

    # ------------------------------------------------------------------
    # First run — no existing file
    # ------------------------------------------------------------------

    def test_first_run_creates_file(self):
        if self._habits_file.exists():
            self._habits_file.unlink()
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["habits"], input=_FULL_INPUT)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(self._habits_file.exists())

    def test_first_run_saves_correct_values(self):
        if self._habits_file.exists():
            self._habits_file.unlink()
        from canvas_manager.main import cli
        self.runner.invoke(cli, ["habits"], input=_FULL_INPUT)
        profile = json.loads(self._habits_file.read_text())
        self.assertEqual(profile["wake_time"], "07:30")
        self.assertEqual(profile["sleep_time"], "23:00")
        self.assertEqual(profile["peak_focus_hours"], ["09:00-11:00"])
        self.assertEqual(profile["preferred_block_minutes"], 90)
        self.assertEqual(profile["break_cadence_minutes"], 15)
        self.assertEqual(profile["hard_stop_times"], ["18:00"])

    def test_first_run_shows_saved_confirmation(self):
        if self._habits_file.exists():
            self._habits_file.unlink()
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["habits"], input=_FULL_INPUT)
        self.assertIn("saved", result.output.lower())

    # ------------------------------------------------------------------
    # Existing profile — decline to update
    # ------------------------------------------------------------------

    def test_existing_profile_shown_as_table(self):
        self._habits_file.write_text(json.dumps({
            "wake_time": "06:00", "sleep_time": "22:00",
            "peak_focus_hours": ["08:00-10:00"],
            "preferred_block_minutes": 60,
            "break_cadence_minutes": 10,
            "hard_stop_times": ["17:00"],
        }))
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["habits"], input="n\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("06:00", result.output)
        self.assertIn("Habits Profile", result.output)

    def test_existing_profile_no_update_does_not_overwrite(self):
        original = {"wake_time": "06:00", "sleep_time": "22:00",
                    "peak_focus_hours": ["08:00-10:00"],
                    "preferred_block_minutes": 60,
                    "break_cadence_minutes": 10,
                    "hard_stop_times": ["17:00"]}
        self._habits_file.write_text(json.dumps(original))
        from canvas_manager.main import cli
        self.runner.invoke(cli, ["habits"], input="n\n")
        self.assertEqual(json.loads(self._habits_file.read_text()), original)

    # ------------------------------------------------------------------
    # Existing profile — accept update
    # ------------------------------------------------------------------

    def test_existing_profile_yes_update_overwrites(self):
        self._habits_file.write_text(json.dumps({
            "wake_time": "06:00", "sleep_time": "22:00",
            "peak_focus_hours": ["08:00-10:00"],
            "preferred_block_minutes": 60,
            "break_cadence_minutes": 10,
            "hard_stop_times": ["17:00"],
        }))
        from canvas_manager.main import cli
        self.runner.invoke(cli, ["habits"], input="y\n" + _FULL_INPUT)
        profile = json.loads(self._habits_file.read_text())
        self.assertEqual(profile["wake_time"], "07:30")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def test_invalid_time_reprompts(self):
        if self._habits_file.exists():
            self._habits_file.unlink()
        from canvas_manager.main import cli
        # First wake-time answer is bad; second is good
        bad_then_good = "\n".join([
            "25:00",        # invalid — reprompt
            "07:30",        # valid wake time
            "23:00", "09:00-11:00", "90", "15", "18:00",
        ]) + "\n"
        result = self.runner.invoke(cli, ["habits"], input=bad_then_good)
        self.assertEqual(result.exit_code, 0, result.output)
        profile = json.loads(self._habits_file.read_text())
        self.assertEqual(profile["wake_time"], "07:30")

    def test_invalid_minutes_reprompts(self):
        if self._habits_file.exists():
            self._habits_file.unlink()
        from canvas_manager.main import cli
        bad_then_good = "\n".join([
            "07:30", "23:00", "09:00-11:00",
            "0",    # invalid block length
            "90",   # valid
            "15", "18:00",
        ]) + "\n"
        result = self.runner.invoke(cli, ["habits"], input=bad_then_good)
        self.assertEqual(result.exit_code, 0, result.output)
        profile = json.loads(self._habits_file.read_text())
        self.assertEqual(profile["preferred_block_minutes"], 90)

    # ------------------------------------------------------------------
    # File path
    # ------------------------------------------------------------------

    def test_habits_file_path_is_absolute(self):
        from canvas_manager.main import HABITS_FILE
        self.assertTrue(HABITS_FILE.is_absolute())

    def test_habits_file_resolves_inside_project(self):
        from canvas_manager.main import HABITS_FILE
        self.assertEqual(HABITS_FILE.parent, PROJECT_DIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
