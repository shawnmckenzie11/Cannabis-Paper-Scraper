"""Tests for scheduled background jobs."""

import unittest
from unittest import mock
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


    def test_run_post_harvest_maude_upgrade_skips_empty_ids(self):
        """No subprocess is started when the harvest batch is empty."""
        result = scheduled_jobs.run_post_harvest_maude_upgrade([])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["paper_count"], 0)

    @unittest.mock.patch("scheduled_jobs._prioritize_open_access_paper_ids", side_effect=lambda ids: ids)
    @unittest.mock.patch("scheduled_jobs._count_open_access_among", return_value=1)
    @unittest.mock.patch("maude_reingest_watchdog.start_detached_two_pass", return_value=12345)
    def test_run_post_harvest_maude_upgrade_starts_slow_pass(
        self, mock_start, _mock_count, _mock_prio
    ):
        """Fresh harvest ids trigger a scoped slow-only two-pass re-ingest."""
        result = scheduled_jobs.run_post_harvest_maude_upgrade([101, 102, 101])
        self.assertEqual(result["status"], "started")
        self.assertEqual(result["paper_count"], 2)
        self.assertEqual(result["open_access_count"], 1)
        mock_start.assert_called_once()
        kwargs = mock_start.call_args.kwargs
        self.assertEqual(kwargs["paper_ids"], [101, 102])
        self.assertTrue(kwargs["slow_only"])


if __name__ == "__main__":
    unittest.main()
