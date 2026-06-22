"""Tests for scheduled background jobs."""

import unittest
from zoneinfo import ZoneInfo

import scheduled_jobs


class ScheduledJobsTests(unittest.TestCase):
    """Scheduled job parsing and registration."""

    def test_parse_local_run_at_time_only(self):
        """HH:MM parses against the provided local date."""
        run_at = scheduled_jobs.parse_local_run_at(
            "23:00",
            run_date="2026-06-22",
            timezone_name="America/Toronto",
        )
        self.assertEqual(run_at.hour, 23)
        self.assertEqual(run_at.minute, 0)
        self.assertEqual(str(run_at.tzinfo), "America/Toronto")

    def test_schedule_maude_reingest_rejects_past_time(self):
        """Past run times are rejected."""
        with self.assertRaises(ValueError):
            scheduled_jobs.schedule_maude_reingest(
                at_time="00:01",
                run_date="2020-01-01",
                timezone_name="America/Toronto",
            )


if __name__ == "__main__":
    unittest.main()
