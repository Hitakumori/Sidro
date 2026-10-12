"""Local timing, restart, deferral and failure behavior; no network calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from support_reminders import MAX_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS, SupportReminders, format_usage_time


class SupportReminderTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".source-publication-audit"
        parent.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "support_reminder.json"
        self.now = 100.0
        self.interval = Mock(return_value=MIN_INTERVAL_SECONDS)
        self.reminders = SupportReminders(self.path, clock=lambda: self.now, interval=self.interval)

    def advance(self, seconds=30, **kwargs):
        self.now += seconds
        return self.reminders.tick(**kwargs)

    def test_random_delay_is_requested_with_two_to_four_hour_bounds(self):
        self.interval.assert_called_once_with(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
        self.assertIsNone(self.reminders.tick())
        self.assertEqual(self.reminders.total_seconds, 0)

    def test_due_reminder_contains_actual_cumulative_time_and_reschedules(self):
        for _ in range(MIN_INTERVAL_SECONDS // 30 - 1):
            self.assertIsNone(self.advance())
        self.assertEqual(self.advance(), "You have been using this app for 2 hours, "
                         "please consider supporting me from Patreon 🥹")
        self.assertEqual(self.reminders.next_reminder_at, 2 * MIN_INTERVAL_SECONDS)
        self.assertIsNone(self.advance())

    def test_busy_or_unfocused_dashboard_defers_instead_of_losing_reminder(self):
        self.reminders.next_reminder_at = 30
        self.assertIsNone(self.advance(can_show=False))
        self.assertIsNone(self.advance(can_show=False))
        self.assertEqual(self.reminders.next_reminder_at, 30)
        self.assertIsNotNone(self.advance(can_show=True))
        self.assertIsNone(self.advance(can_show=True))

    def test_restart_keeps_runtime_and_pending_deadline_without_counting_closed_time(self):
        self.reminders.next_reminder_at = 30
        self.advance(can_show=False)
        self.now += 86400
        restored = SupportReminders(self.path, clock=lambda: self.now, interval=self.interval)
        self.assertEqual(restored.total_seconds, 30)
        self.assertEqual(restored.next_reminder_at, 30)
        self.assertIsNone(restored.tick(can_show=False))
        self.assertIsNotNone(restored.tick(can_show=True))
        self.assertEqual(restored.total_seconds, 30)

    def test_suspend_gap_is_not_counted_and_normal_ticks_resume(self):
        self.advance()
        self.advance(3600)
        self.assertEqual(self.reminders.total_seconds, 30)
        self.advance()
        self.assertEqual(self.reminders.total_seconds, 60)

    def test_clock_reversal_is_not_counted(self):
        self.advance(-10)
        self.assertEqual(self.reminders.total_seconds, 0)
        self.advance()
        self.assertEqual(self.reminders.total_seconds, 30)

    def test_malformed_or_oversized_state_is_ignored(self):
        for content in ("{", "[]", "null", '{"total_seconds": true, "next_reminder_at": 10}',
                        '{"total_seconds": -1, "next_reminder_at": 10}',
                        '{"total_seconds": NaN, "next_reminder_at": 10}', " " * 4097):
            with self.subTest(content=content[:50]):
                self.path.write_text(content, encoding="utf-8")
                restored = SupportReminders(self.path, clock=lambda: self.now, interval=self.interval)
                self.assertEqual(restored.total_seconds, 0)
                self.assertEqual(restored.next_reminder_at, MIN_INTERVAL_SECONDS)

    def test_storage_failure_does_not_block_app_or_prompt_repeatedly(self):
        self.reminders.next_reminder_at = 30
        with patch("support_reminders.tempfile.NamedTemporaryFile", side_effect=PermissionError):
            self.assertIsNotNone(self.advance())
            self.assertIsNone(self.advance())

    def test_state_contains_only_local_timing(self):
        self.advance()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"total_seconds": 30, "next_reminder_at": MIN_INTERVAL_SECONDS})

    def test_duration_format(self):
        for seconds, expected in ((60, "1 minute"), (120, "2 minutes"), (3600, "1 hour"),
                                  (7260, "2 hours and 1 minute")):
            self.assertEqual(format_usage_time(seconds), expected)


if __name__ == "__main__":
    unittest.main()
