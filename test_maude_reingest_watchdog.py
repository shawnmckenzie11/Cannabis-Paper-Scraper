"""Tests for Maude bulk re-ingest watchdog gating."""

import unittest
from unittest.mock import MagicMock, patch

import maude_reingest_watchdog as watchdog


class MaudeReingestWatchdogTests(unittest.TestCase):
    """Watchdog only runs while a bulk session is armed."""

    def test_run_watchdog_idle_when_not_active(self):
        """The scheduler should no-op until a bulk run arms the watchdog."""
        db = MagicMock()
        db.get_metadata.side_effect = lambda key, default=None: {
            watchdog.METADATA_BULK_ACTIVE: "",
        }.get(key, default)

        result = watchdog.run_watchdog(db)

        self.assertEqual(result["action"], "idle")
        self.assertEqual(result["reason"], "watchdog_not_active")

    @patch.object(watchdog, "is_reingest_running", return_value=True)
    @patch.object(watchdog, "count_papers_needing_reingest", return_value=100)
    def test_run_watchdog_checks_when_active(self, _count, _running):
        """An armed bulk session allows periodic health checks."""
        db = MagicMock()
        db.get_metadata.side_effect = lambda key, default=None: {
            watchdog.METADATA_BULK_ACTIVE: "true",
            watchdog.METADATA_BULK_COMPLETE: "",
            watchdog.METADATA_BULK_ACTIVE_CONFIG: '{"batch_size": 50, "workers": 4}',
            watchdog.METADATA_LAST_RUN: "",
        }.get(key, default)

        result = watchdog.run_watchdog(db)

        self.assertEqual(result["action"], "running")
        self.assertEqual(result["remaining"], 100)


if __name__ == "__main__":
    unittest.main()
