# test_calibration_coordinator.py
"""Tests for calibration coordination lock."""

import unittest

from db_manager import DatabaseManager
import calibration_coordinator


class CalibrationCoordinatorTests(unittest.TestCase):
    """Lock acquire/release behavior."""

    def setUp(self):
        self.db = DatabaseManager()
        calibration_coordinator.release_lock(db=self.db)

    def tearDown(self):
        calibration_coordinator.release_lock(db=self.db)

    def test_acquire_and_release(self):
        """Lock transitions idle → running_batch → idle."""
        status = calibration_coordinator.acquire_lock(
            "running_batch",
            "test-owner",
            subnode="node2b",
            db=self.db,
        )
        self.assertEqual(status["state"], "running_batch")
        self.assertTrue(status["is_blocked"])

        released = calibration_coordinator.release_lock(db=self.db)
        self.assertEqual(released["state"], "idle")
        self.assertFalse(released["is_blocked"])

    def test_concurrent_acquire_rejected(self):
        """Second acquire raises while lock is held."""
        calibration_coordinator.acquire_lock("running_batch", "first", db=self.db)
        with self.assertRaises(calibration_coordinator.CalibrationLockError):
            calibration_coordinator.acquire_lock("running_batch", "second", db=self.db)


if __name__ == "__main__":
    unittest.main()
