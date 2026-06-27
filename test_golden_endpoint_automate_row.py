"""Tests for golden endpoint row orchestration guard gating."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from golden_endpoint_status import prior_rows_guard_passed, save_status


class PriorRowsGuardPassedTests(unittest.TestCase):
    """Verify prior-row golden guard checks before starting a new row."""

    def test_row_zero_always_allowed(self) -> None:
        """Row 0 has no prior rows to validate."""
        ids = ["a", "b", "c"]
        ok, blocking, message = prior_rows_guard_passed(0, ids)
        self.assertTrue(ok)
        self.assertIsNone(blocking)
        self.assertEqual(message, "")

    def test_blocks_when_prior_row_guard_not_passed(self) -> None:
        """Row 2 is blocked when row 1 lacks guard_passed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            save_status(
                {
                    "endpoints": {
                        "row0": {"guard_passed": True, "status": "completed"},
                        "row1": {
                            "guard_passed": False,
                            "status": "blocked_golden_guard",
                            "batch_alignment_pct": 65.0,
                        },
                    }
                },
                path=path,
            )
            ids = ["row0", "row1", "row2"]
            ok, blocking, message = prior_rows_guard_passed(2, ids, path=path)
            self.assertFalse(ok)
            self.assertEqual(blocking, "row1")
            self.assertIn("row 1", message)
            self.assertIn("blocked_golden_guard", message)

    def test_allows_when_all_priors_passed(self) -> None:
        """Row 2 may start when rows 0 and 1 both passed guard."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            save_status(
                {
                    "endpoints": {
                        "row0": {"guard_passed": True, "status": "completed"},
                        "row1": {"guard_passed": True, "status": "completed"},
                    }
                },
                path=path,
            )
            ids = ["row0", "row1", "row2"]
            ok, blocking, message = prior_rows_guard_passed(2, ids, path=path)
            self.assertTrue(ok)
            self.assertIsNone(blocking)

    def test_blocks_when_prior_row_missing_from_status(self) -> None:
        """A prior row with no status record is treated as not having passed guard."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({"endpoints": {}}), encoding="utf-8")
            ids = ["row0", "row1"]
            ok, blocking, message = prior_rows_guard_passed(1, ids, path=path)
            self.assertFalse(ok)
            self.assertEqual(blocking, "row0")


if __name__ == "__main__":
    unittest.main()
