# test_rl_node_progress.py
"""Tests for RL node progress metrics."""

import unittest

import calibration_metrics


class RlNodeProgressTests(unittest.TestCase):
    """Cross-node alignment and Maude recall scoring."""

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

    def test_score_paper_maude_recall(self):
        """Maude recall counts Claude-populated fields Maude also populated."""
        result = {
            "content_tier": "pdf_extracted",
            "llm": {
                "study_type": ["Animal Models (Mouse)"],
                "exposure_method": ["oral administration"],
                "species": ["mouse"],
                "dose_mg": 10,
            },
            "maude": {
                "study_type": ["Animal Models (Rat)"],
                "exposure_method": ["oral administration"],
                "species": ["mouse"],
            },
            "scoped_disagreement": {
                "agreement_rate": 0.6667,
                "scoped_field_count": 3,
                "fields": {"study_type": {}},
            },
        }
        scored = calibration_metrics.score_paper_rl_metrics(result, "node2b")
        self.assertIsNotNone(scored)
        assert scored is not None
        self.assertEqual(scored["claude_fields_populated"], 2)
        self.assertEqual(scored["maude_fields_populated"], 1)
        self.assertEqual(scored["maude_recall_rate"], 0.5)

    def test_build_rl_node_progress_scores_batch(self):
        """Batch payloads produce alignment and Maude recall run metrics."""
        batch = {
            "batch_id": "node2b_calibration_test",
            "created_at": "2026-06-22T10:00:00",
            "target_subnode": "node2b",
            "mode": "node2b_in_vivo",
            "results": [{
                "content_tier": "pdf_extracted",
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
        self.assertIsNotNone(node2b["latest_maude_recall_pct"])


if __name__ == "__main__":
    unittest.main()
