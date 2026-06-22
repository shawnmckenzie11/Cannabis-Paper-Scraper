# test_rl_node_progress.py
"""Tests for RL node progress metrics."""

import unittest

import calibration_metrics


class RlNodeProgressTests(unittest.TestCase):
    """Cross-node alignment and extraction-fill scoring."""

    def test_field_is_populated(self):
        """Population helper treats empty values as missing."""
        self.assertFalse(calibration_metrics.field_is_populated(None))
        self.assertFalse(calibration_metrics.field_is_populated([]))
        self.assertTrue(calibration_metrics.field_is_populated(["Animal Models (Mouse)"]))

    def test_build_rl_node_progress_empty(self):
        """Empty batches still return full node registry scaffolding."""
        progress = calibration_metrics.build_rl_node_progress([])
        self.assertIn("node2a", progress["nodes"])
        self.assertIn("node2b", progress["nodes"])
        self.assertEqual(progress["nodes"]["node0"]["status"], "passed")
        self.assertEqual(progress["nodes"]["node2b"]["status"], "pending")

    def test_build_rl_node_progress_scores_batch(self):
        """Batch payloads produce alignment and extraction-fill run metrics."""
        batch = {
            "batch_id": "node2b_calibration_test",
            "created_at": "2026-06-22T10:00:00",
            "target_subnode": "node2b",
            "mode": "node2b_in_vivo",
            "results": [{
                "llm": {
                    "study_type": ["Animal Models (Mouse)"],
                    "exposure_method": ["oral administration"],
                    "species": ["mouse"],
                },
                "maude": {
                    "study_type": ["Animal Models (Rat)"],
                    "exposure_method": ["oral administration"],
                    "species": ["mouse"],
                },
            }],
        }
        progress = calibration_metrics.build_rl_node_progress([batch])
        node2b = progress["nodes"]["node2b"]
        self.assertEqual(node2b["run_count"], 1)
        self.assertIsNotNone(node2b["latest_alignment_pct"])
        self.assertIsNotNone(node2b["latest_extraction_fill_pct"])


if __name__ == "__main__":
    unittest.main()
