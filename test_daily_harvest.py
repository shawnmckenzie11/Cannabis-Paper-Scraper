"""Tests for idempotent daily harvest, CHEAP_OPS, and mindate catch-up."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import daily_harvest

ROOT = Path(__file__).resolve().parent


class FakeHarvestDb:
    """In-memory metadata stand-in for DatabaseManager in harvest tests."""

    def __init__(self, meta=None):
        self.meta = dict(meta or {})
        self.synced_paper_ids = []

    def get_metadata(self, key, default=None):
        """Return a stored metadata value or default."""
        return self.meta.get(key, default)

    def set_metadata(self, key, value):
        """Persist a metadata value."""
        self.meta[key] = value

    def sync_tab_flags_for_paper(self, paper_id):
        """Record tab-flag syncs for assertions."""
        self.synced_paper_ids.append(int(paper_id))

    def count_expert_edits_since(self, *_args, **_kwargs):
        """No pending expert edits in unit tests."""
        return 0


class DailyHarvestTests(unittest.TestCase):
    """Mindate, lock, skip-today, and CHEAP_OPS behavior."""

    def tearDown(self):
        os.environ.pop("CHEAP_OPS", None)
        if daily_harvest._cycle_lock.locked():
            daily_harvest._cycle_lock.release()

    def test_resolve_harvest_mindate_reuses_stale_last_run(self):
        """Catch-up uses the last successful date, not an unbounded historical pull."""
        self.assertEqual(
            daily_harvest.resolve_harvest_mindate("2026-08-20"),
            "2026-08-20",
        )

    def test_resolve_harvest_mindate_never_uses_catchup_window(self):
        """A missing watermark falls back to a short catch-up window."""
        today = date(2026, 8, 28)
        expected = (today - timedelta(days=3)).isoformat()
        self.assertEqual(
            daily_harvest.resolve_harvest_mindate("Never", today=today),
            expected,
        )
        self.assertEqual(
            daily_harvest.resolve_harvest_mindate(None, today=today),
            expected,
        )

    def test_cheap_ops_enabled_reads_env(self):
        """CHEAP_OPS truthy strings enable cheap-ops mode."""
        self.assertFalse(daily_harvest.cheap_ops_enabled({}))
        self.assertTrue(daily_harvest.cheap_ops_enabled({"CHEAP_OPS": "1"}))
        self.assertTrue(daily_harvest.cheap_ops_enabled({"CHEAP_OPS": "true"}))
        self.assertFalse(daily_harvest.cheap_ops_enabled({"CHEAP_OPS": "0"}))

    def test_run_daily_harvest_skips_when_already_ran_today(self):
        """Idempotent skip does not call PubMed ingest."""
        today = date.today().isoformat()
        db = FakeHarvestDb({"last_daily_harvest_date": today})
        with mock.patch("harvest.run_harvest_pipeline") as mock_harvest:
            result = daily_harvest.run_daily_harvest_if_due(db)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_ran_today")
        mock_harvest.assert_not_called()

    def test_run_daily_harvest_skips_when_lock_held(self):
        """A fresh metadata lock blocks a second overlapping harvest."""
        db = FakeHarvestDb(
            {
                "last_daily_harvest_date": "2026-01-01",
                daily_harvest.LOCK_METADATA_KEY: date.today().isoformat() + "T00:00:00",
            }
        )
        # Age-parse uses datetime.now(); pin lock to now so it is not stale.
        from datetime import datetime

        db.set_metadata(daily_harvest.LOCK_METADATA_KEY, datetime.now().isoformat())
        with mock.patch("harvest.run_harvest_pipeline") as mock_harvest:
            result = daily_harvest.run_daily_harvest_if_due(db)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "locked")
        mock_harvest.assert_not_called()

    @mock.patch("purge_unrelated.run_purger")
    @mock.patch("manual_edit_cycle.should_run_pre_harvest_cycle", return_value=False)
    @mock.patch("scheduled_jobs.run_post_harvest_maude_upgrade", return_value={"status": "started"})
    @mock.patch("harvest.run_harvest_pipeline", return_value=(2, 1, 0, [10, 11]))
    def test_run_daily_harvest_runs_when_due(
        self, mock_harvest, mock_upgrade, _mock_edits, mock_purge
    ):
        """A new calendar day triggers ingest, tab flags, upgrade, and purge."""
        db = FakeHarvestDb({"last_daily_harvest_date": "2026-08-20"})
        result = daily_harvest.run_daily_harvest_if_due(db)
        self.assertEqual(result["status"], "ran")
        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["mindate"], "2026-08-20")
        self.assertEqual(db.synced_paper_ids, [10, 11])
        self.assertEqual(db.get_metadata("last_daily_harvest_date"), date.today().isoformat())
        self.assertIn("Ingested 2 papers", db.get_metadata("last_daily_harvest_status"))
        mock_harvest.assert_called_once()
        kwargs = mock_harvest.call_args.kwargs
        self.assertEqual(kwargs["mindate"], "2026-08-20")
        self.assertFalse(kwargs["classify"])
        mock_upgrade.assert_called_once_with([10, 11])
        mock_purge.assert_called_once_with(dry_run=False)
        self.assertEqual(db.get_metadata(daily_harvest.LOCK_METADATA_KEY), "")

    @mock.patch("purge_unrelated.run_purger")
    @mock.patch("manual_edit_cycle.should_run_pre_harvest_cycle", return_value=False)
    @mock.patch("scheduled_jobs.run_post_harvest_maude_upgrade", return_value={"status": "started"})
    @mock.patch("harvest.run_harvest_pipeline", return_value=(2, 1, 0, [10, 11]))
    def test_cheap_ops_skips_full_catalog_purge(
        self, mock_harvest, mock_upgrade, _mock_edits, mock_purge
    ):
        """CHEAP_OPS harvests new papers but does not scan-delete the whole catalog."""
        os.environ["CHEAP_OPS"] = "1"
        db = FakeHarvestDb({"last_daily_harvest_date": "2026-08-20"})
        result = daily_harvest.run_daily_harvest_if_due(db)
        self.assertEqual(result["status"], "ran")
        self.assertTrue(result.get("purge_skipped"))
        mock_harvest.assert_called_once()
        mock_upgrade.assert_not_called()
        mock_purge.assert_not_called()

    @mock.patch("user_notifications.run_due_notification_digests", return_value={"sent": 0})
    @mock.patch("maude_reingest_watchdog.run_watchdog")
    @mock.patch("scheduled_jobs.run_due_jobs")
    @mock.patch.object(daily_harvest, "run_daily_harvest_if_due", return_value={"status": "skipped"})
    def test_cheap_ops_skips_watchdog_and_due_jobs(
        self, mock_harvest_fn, mock_due, mock_watchdog, _mock_digests
    ):
        """CHEAP_OPS=1 does not start bulk reingest or the Maude watchdog."""
        os.environ["CHEAP_OPS"] = "1"
        db = FakeHarvestDb()
        result = daily_harvest.run_scheduled_cycle(db)
        self.assertTrue(result["ok"])
        self.assertTrue(result["cheap_ops"])
        self.assertEqual(result["watchdog"]["reason"], "cheap_ops")
        mock_due.assert_not_called()
        mock_watchdog.assert_not_called()
        mock_harvest_fn.assert_called_once()

    @mock.patch("user_notifications.run_due_notification_digests", return_value={"sent": 0})
    @mock.patch("maude_reingest_watchdog.run_watchdog", return_value={"action": "idle"})
    @mock.patch("scheduled_jobs.run_due_jobs", return_value=[{"id": "job-1"}])
    @mock.patch.object(daily_harvest, "run_daily_harvest_if_due", return_value={"status": "skipped"})
    def test_full_cycle_runs_watchdog_when_cheap_ops_off(
        self, _mock_harvest_fn, mock_due, mock_watchdog, _mock_digests
    ):
        """Without CHEAP_OPS, due jobs and watchdog still run after harvest."""
        os.environ["CHEAP_OPS"] = "0"
        db = FakeHarvestDb()
        result = daily_harvest.run_scheduled_cycle(db)
        self.assertTrue(result["ok"])
        mock_due.assert_called_once_with(db)
        mock_watchdog.assert_called_once_with(db)
        self.assertEqual(result["due_jobs"], [{"id": "job-1"}])

    def test_overlapping_cycle_returns_already_running(self):
        """A second in-process trigger is rejected while a cycle holds the lock."""
        db = FakeHarvestDb()
        self.assertTrue(daily_harvest._cycle_lock.acquire(blocking=False))
        try:
            result = daily_harvest.run_scheduled_cycle(db)
        finally:
            daily_harvest._cycle_lock.release()
        self.assertEqual(result["status"], "already_running")
        self.assertFalse(result["ok"])

    def test_cli_exits_zero_on_skip(self):
        """python -m daily_harvest is a supported one-shot entrypoint."""
        proc = subprocess.run(
            [sys.executable, "-m", "daily_harvest", "--help"],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertIn("--force", proc.stdout)
        self.assertIn("--harvest-only", proc.stdout)


class CheapOpsConfigTests(unittest.TestCase):
    """fly.toml, entrypoint, and workflow stay aligned with cheap daily harvest."""

    def test_fly_toml_allows_autostop_and_cheap_ops(self):
        """Web machine may stop between HTTP requests; CHEAP_OPS is on."""
        text = (ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertIn("auto_stop_machines = 'stop'", text)
        self.assertIn("min_machines_running = 0", text)
        self.assertIn("CHEAP_OPS", text)
        self.assertNotIn("auto_stop_machines = 'off'", text)
        self.assertIn("256mb", text.lower())

    def test_entrypoint_skips_bulk_jobs_in_cheap_ops(self):
        """Startup must not launch stale full-corpus reclassify when CHEAP_OPS=1."""
        text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("CHEAP_OPS", text)
        self.assertIn("upgrade_stale_harvest_classifications.py", text)

    def test_daily_harvest_workflow_uses_hub_store(self):
        """Actions cron harvests SQLite and reloads the Hugging Face Space."""
        text = (ROOT / ".github" / "workflows" / "daily-harvest.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 11 * * *"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("scripts/ci_daily_harvest.py", text)
        self.assertIn("unset DATABASE_URL", text)
        self.assertIn("/api/catalog/reload", text)
        self.assertIn("HF_TOKEN", text)
        self.assertNotIn("flyctl", text)
        self.assertNotIn("FLY_API_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
