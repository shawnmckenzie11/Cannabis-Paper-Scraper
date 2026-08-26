"""Tests for scheduled background jobs."""

import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import scheduled_jobs

ROOT = Path(__file__).resolve().parent


class FakeSchedulerDb:
    """In-memory metadata stand-in for DatabaseManager in cycle tests."""

    def __init__(self, meta=None):
        self.meta = dict(meta or {})
        self.synced_paper_ids = []

    def get_metadata(self, key, default=None):
        """Return a stored metadata value or default."""
        return self.meta.get(key, default)

    def set_metadata(self, key, value):
        """Persist a metadata value."""
        self.meta[key] = value

    def sync_orphan_tab_flags_since(self, _since):
        """No-op tab-flag repair used by run_scheduled_cycle."""
        return 0

    def sync_tab_flags_for_paper(self, paper_id):
        """Record tab-flag syncs for assertions."""
        self.synced_paper_ids.append(int(paper_id))

    def count_expert_edits_since(self, *_args, **_kwargs):
        """No pending expert edits in cycle unit tests."""
        return 0


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

    def test_scheduler_token_fails_closed_and_accepts_bearer(self):
        """Empty configured token rejects; matching secret (and Bearer form) accepts."""
        self.assertFalse(scheduled_jobs.scheduler_token_is_authorized("secret", expected=""))
        self.assertFalse(scheduled_jobs.scheduler_token_is_authorized("nope", expected="secret"))
        self.assertTrue(scheduled_jobs.scheduler_token_is_authorized("secret", expected="secret"))
        self.assertTrue(
            scheduled_jobs.scheduler_token_is_authorized("Bearer secret", expected="secret")
        )

    @mock.patch("user_notifications.run_due_notification_digests", return_value={"sent": 0})
    @mock.patch("maude_reingest_watchdog.run_watchdog", return_value={"action": "ok"})
    @mock.patch("scheduled_jobs.run_due_jobs", return_value=[])
    @mock.patch("harvest.run_harvest_pipeline")
    def test_run_scheduled_cycle_skips_harvest_when_already_ran_today(
        self, mock_harvest, mock_jobs, mock_watchdog, _mock_digests
    ):
        """Due jobs still run when the daily harvest already succeeded today."""
        today = date.today().isoformat()
        db = FakeSchedulerDb({"last_daily_harvest_date": today, "last_daily_harvest_status": "ok"})
        result = scheduled_jobs.run_scheduled_cycle(db)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["harvest"]["status"], "skipped")
        mock_harvest.assert_not_called()
        mock_jobs.assert_called_once_with(db)
        mock_watchdog.assert_called_once_with(db)
        self.assertEqual(db.get_metadata("scheduler_trigger"), "external")
        self.assertTrue(db.get_metadata("scheduler_heartbeat_at"))

    @mock.patch("purge_unrelated.run_purger")
    @mock.patch("manual_edit_cycle.should_run_pre_harvest_cycle", return_value=False)
    @mock.patch("user_notifications.run_due_notification_digests", return_value={"sent": 0})
    @mock.patch("maude_reingest_watchdog.run_watchdog", return_value={"action": "ok"})
    @mock.patch("scheduled_jobs.run_due_jobs", return_value=[{"id": "job-1"}])
    @mock.patch("scheduled_jobs.run_post_harvest_maude_upgrade", return_value={"status": "started"})
    @mock.patch("harvest.run_harvest_pipeline", return_value=(2, 1, 0, [10, 11]))
    def test_run_scheduled_cycle_runs_harvest_when_due(
        self,
        mock_harvest,
        mock_upgrade,
        mock_jobs,
        _mock_watchdog,
        _mock_digests,
        _mock_edits,
        mock_purge,
    ):
        """A new calendar day triggers ingest, tab flags, upgrade, and purge."""
        db = FakeSchedulerDb({"last_daily_harvest_date": "2026-01-01"})
        result = scheduled_jobs.run_scheduled_cycle(db)
        self.assertTrue(result["ok"])
        self.assertEqual(result["harvest"]["status"], "ran")
        self.assertEqual(result["harvest"]["success_count"], 2)
        self.assertEqual(db.synced_paper_ids, [10, 11])
        self.assertEqual(db.get_metadata("last_daily_harvest_date"), date.today().isoformat())
        self.assertIn("Ingested 2 papers", db.get_metadata("last_daily_harvest_status"))
        mock_harvest.assert_called_once()
        mock_upgrade.assert_called_once_with([10, 11])
        mock_purge.assert_called_once_with(dry_run=False)
        self.assertEqual(result["due_jobs"], [{"id": "job-1"}])

    def test_overlapping_cycle_returns_already_running(self):
        """A second in-process trigger is rejected while a cycle holds the lock."""
        db = FakeSchedulerDb()
        self.assertTrue(scheduled_jobs._cycle_lock.acquire(blocking=False))
        try:
            result = scheduled_jobs.run_scheduled_cycle(db)
        finally:
            scheduled_jobs._cycle_lock.release()
        self.assertEqual(result["status"], "already_running")
        self.assertFalse(result["ok"])

    def test_cli_exposes_run_cycle_flag(self):
        """python scheduled_jobs.py --help documents the one-shot cycle entrypoint."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scheduled_jobs.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("--run-cycle", proc.stdout)

    def test_fly_toml_allows_autostop(self):
        """Web machine may stop between HTTP requests now that harvest is external."""
        text = (ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertIn("auto_stop_machines = 'stop'", text)
        self.assertIn("min_machines_running = 0", text)
        self.assertNotIn("Keep the VM up", text)
        self.assertNotIn("auto_stop_machines = 'off'", text)

    def test_daily_harvest_workflow_posts_to_fly(self):
        """Actions cron wakes Fly via the authenticated run-cycle endpoint."""
        text = (ROOT / ".github" / "workflows" / "daily-harvest.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 14 * * *"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("/api/scheduler/run-cycle", text)
        self.assertIn("X-Scheduler-Token", text)
        self.assertIn("SCHEDULER_RUN_TOKEN", text)

    def test_app_does_not_start_inprocess_scheduler_thread(self):
        """Gunicorn import must not spawn the old 60s poll loop."""
        text = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("daily_harvest_scheduler", text)
        self.assertIn("/api/scheduler/run-cycle", text)
        self.assertIn("inprocess_harvest", text)


if __name__ == "__main__":
    unittest.main()
