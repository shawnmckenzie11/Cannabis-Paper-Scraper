"""Tests for push checkpointing and stall detection."""

import os
import tempfile
import time
import unittest

from push_resilience import (
    PushProgressTracker,
    PushStalledError,
    load_push_checkpoint,
    save_push_checkpoint,
)


class PushResilienceTests(unittest.TestCase):
    """Checkpoint persistence and stall watchdog behavior."""

    def test_save_and_load_checkpoint(self):
        """Checkpoint round-trips through JSON on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "checkpoint.json")
            save_push_checkpoint(
                {"status": "in_progress", "total_pushed_lifetime": 1380},
                path,
            )
            loaded = load_push_checkpoint(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["status"], "in_progress")
            self.assertEqual(loaded["total_pushed_lifetime"], 1380)
            self.assertIn("updated_at", loaded)

    def test_progress_tracker_detects_stall(self):
        """Watchdog marks stalled runs when progress stops."""
        tracker = PushProgressTracker(stall_seconds=0.2, heartbeat_seconds=999)
        tracker.start()
        try:
            deadline = time.time() + 1.5
            while time.time() < deadline and not tracker.stalled:
                time.sleep(0.05)
            self.assertTrue(tracker.stalled)
            tracker.check_stalled()
        except PushStalledError:
            pass
        else:
            self.fail("expected PushStalledError")
        finally:
            tracker.stop()

    def test_progress_touch_resets_stall_timer(self):
        """Recent progress prevents false-positive stall detection."""
        tracker = PushProgressTracker(stall_seconds=1.0, heartbeat_seconds=999)
        tracker.start()
        try:
            for _ in range(5):
                tracker.touch("working")
                time.sleep(0.2)
            self.assertFalse(tracker.stalled)
        finally:
            tracker.stop()


if __name__ == "__main__":
    unittest.main()
