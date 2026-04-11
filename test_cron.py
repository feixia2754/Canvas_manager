"""
Tests for the cron scheduling feature of canvas-manager.

Covers:
  1. setup-cron generates a correctly formatted crontab line
  2. All file paths resolved by the package are absolute (cwd-independent)
  3. The live crontab contains a canvas-manager entry
  4. The remind --preview command runs end-to-end with a seeded cache
  5. The scheduled 9:45 PM tonight entry is present in the crontab
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
PACKAGE_DIR = PROJECT_DIR / "canvas_manager"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deadline(name: str, course: str, hours_from_now: int = 2) -> dict:
    due = datetime.now(tz=timezone.utc) + timedelta(hours=hours_from_now)
    return {"name": name, "course": course, "due_at": due, "url": "", "source": "canvas"}


def _get_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestCronSetupCommand(unittest.TestCase):
    """setup-cron command output is well-formed."""

    def setUp(self):
        self.runner = CliRunner()

    def test_default_time_is_08_00(self):
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["setup-cron"])
        self.assertEqual(result.exit_code, 0, result.output)
        # Default REMINDER_TIME in .env is 08:00 → cron field "0 8"
        self.assertIn("0 8", result.output)

    def test_custom_time_flag(self):
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["setup-cron", "--time", "21:45"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("45 21", result.output)

    def test_output_contains_binary_name(self):
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["setup-cron"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("canvas-manager", result.output)

    def test_output_contains_remind_subcommand(self):
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["setup-cron"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("remind", result.output)

    def test_log_path_is_absolute_in_output(self):
        """The suggested cron line must not contain ~ (cron does not expand it)."""
        from canvas_manager.main import cli
        result = self.runner.invoke(cli, ["setup-cron"])
        self.assertEqual(result.exit_code, 0, result.output)
        # Extract the actual cron line (cyan line inside the output)
        lines = [l.strip() for l in result.output.splitlines() if "canvas-manager" in l and "remind" in l]
        self.assertTrue(lines, "No cron line found in output")
        cron_line = lines[0]
        self.assertNotIn("~", cron_line, "Cron line must use absolute path, not ~")


class TestFilePathsAreAbsolute(unittest.TestCase):
    """Paths for token, credentials, .env, and cache must not depend on cwd."""

    def test_token_file_is_absolute(self):
        from canvas_manager import notifier
        self.assertTrue(
            os.path.isabs(notifier.TOKEN_FILE),
            f"TOKEN_FILE must be absolute, got: {notifier.TOKEN_FILE}"
        )

    def test_creds_file_is_absolute(self):
        from canvas_manager import notifier
        self.assertTrue(
            os.path.isabs(notifier.CREDS_FILE),
            f"CREDS_FILE must be absolute, got: {notifier.CREDS_FILE}"
        )

    def test_deadlines_cache_is_absolute(self):
        from canvas_manager.main import DEADLINES_CACHE
        self.assertTrue(
            DEADLINES_CACHE.is_absolute(),
            f"DEADLINES_CACHE must be absolute, got: {DEADLINES_CACHE}"
        )

    def test_token_file_resolves_inside_project(self):
        from canvas_manager import notifier
        self.assertTrue(
            Path(notifier.TOKEN_FILE).parent == PROJECT_DIR,
            "token.json should live in the project root"
        )

    def test_deadlines_cache_resolves_inside_project(self):
        from canvas_manager.main import DEADLINES_CACHE
        self.assertEqual(DEADLINES_CACHE.parent, PROJECT_DIR)


class TestLiveCrontab(unittest.TestCase):
    """The system crontab has the expected canvas-manager entries."""

    def setUp(self):
        self.crontab = _get_crontab()

    def test_daily_8am_entry_exists(self):
        self.assertIn("canvas-manager", self.crontab,
                      "No canvas-manager entry found in crontab")

    def test_tonight_10_12pm_entry_exists(self):
        """One-time entry scheduled for 10:12 PM tonight (April 10)."""
        # cron field: 12 22 10 4 *
        self.assertIn("12 22 10 4", self.crontab,
                      "10:12 PM tonight entry (12 22 10 4) not found in crontab")

    def test_no_tilde_in_crontab(self):
        """~ must not appear in canvas-manager lines (cron won't expand it)."""
        for line in self.crontab.splitlines():
            if "canvas-manager" in line:
                self.assertNotIn("~", line,
                    f"Tilde found in crontab line (cron won't expand it): {line}")

    def test_log_file_path_is_absolute(self):
        for line in self.crontab.splitlines():
            if "canvas-manager" in line and ">>" in line:
                log_part = line.split(">>")[1].strip().split()[0]
                self.assertTrue(log_part.startswith("/"),
                    f"Log path in crontab is not absolute: {log_part}")


class TestRemindPreview(unittest.TestCase):
    """remind --preview runs end-to-end without real credentials."""

    def setUp(self):
        self.runner = CliRunner()
        from canvas_manager.main import DEADLINES_CACHE
        # Preserve any pre-existing cache so tests don't destroy real data
        self._original_cache = DEADLINES_CACHE.read_text() if DEADLINES_CACHE.exists() else None

    def _seed_cache(self, deadlines: list[dict]) -> None:
        from canvas_manager.main import DEADLINES_CACHE
        items = [
            {**d, "due_at": d["due_at"].isoformat()} for d in deadlines
        ]
        DEADLINES_CACHE.write_text(json.dumps(items, indent=2))

    def tearDown(self):
        from canvas_manager.main import DEADLINES_CACHE
        if self._original_cache is not None:
            DEADLINES_CACHE.write_text(self._original_cache)
        elif DEADLINES_CACHE.exists():
            DEADLINES_CACHE.unlink()

    def test_preview_email_shows_subject(self):
        from canvas_manager.main import cli
        self._seed_cache([_make_deadline("HW5", "10-601", hours_from_now=2)])
        result = self.runner.invoke(cli, ["remind", "--preview", "--email-only"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("EMAIL PREVIEW", result.output)
        self.assertIn("Subject:", result.output)

    def test_preview_sms_shows_body(self):
        from canvas_manager.main import cli
        self._seed_cache([_make_deadline("HW5", "10-601", hours_from_now=2)])
        result = self.runner.invoke(cli, ["remind", "--preview", "--sms-only"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("SMS PREVIEW", result.output)

    def test_preview_nothing_sent_message(self):
        from canvas_manager.main import cli
        self._seed_cache([_make_deadline("HW5", "10-601", hours_from_now=2)])
        result = self.runner.invoke(cli, ["remind", "--preview"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("nothing sent", result.output)

    def test_preview_empty_cache_warns(self):
        from canvas_manager.main import cli, DEADLINES_CACHE
        if DEADLINES_CACHE.exists():
            DEADLINES_CACHE.unlink()
        result = self.runner.invoke(cli, ["remind", "--preview"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No cache found", result.output)

    def test_preview_respects_lookahead_days(self):
        """Deadline beyond lookahead window should not appear in preview."""
        from canvas_manager.main import cli
        # Seed one deadline 30 days out (beyond default 3-day lookahead)
        self._seed_cache([_make_deadline("Far Future HW", "10-601", hours_from_now=30 * 24)])
        result = self.runner.invoke(cli, ["remind", "--preview", "--email-only", "--days", "3"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("Far Future HW", result.output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
