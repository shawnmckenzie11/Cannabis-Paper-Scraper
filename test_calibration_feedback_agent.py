# test_calibration_feedback_agent.py
"""Tests for Claude calibration feedback agent helpers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import calibration_feedback_agent
import subnode_field_scopes


class CalibrationFeedbackAgentTests(unittest.TestCase):
    """Disagreement collection and staged patch persistence."""

    def test_collect_disagreement_rows_scoped(self):
        """Scoped disagreements are extracted from batch results."""
        batch = {
            "batch_id": "node2b_calibration_test",
            "target_subnode": "node2b",
            "results": [{
                "paper_id": 1,
                "title": "Mouse study",
                "llm": {
                    "study_type": ["Animal Models (Mouse)"],
                    "exposure_method": ["oral administration"],
                },
                "maude": {
                    "study_type": ["Animal Models (Rat)"],
                    "exposure_method": ["oral administration"],
                },
                "scoped_disagreement": {
                    "fields": {
                        "study_type": {
                            "maude": ["Animal Models (Rat)"],
                            "llm": ["Animal Models (Mouse)"],
                        }
                    }
                },
            }],
        }
        rows = calibration_feedback_agent.collect_disagreement_rows(batch, "node2b")
        self.assertEqual(len(rows), 1)
        self.assertIn("study_type", rows[0]["fields"])

    def test_save_staged_patches(self):
        """Proposed rules changes are written under staged_patches/."""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            path = calibration_feedback_agent.save_staged_patches(
                {
                    "pattern_summary": "test",
                    "proposed_rules_changes": [{
                        "type": "classifier_logic",
                        "description": "Fix rodent routing",
                        "patch_hint": "maude_classifier.py",
                    }],
                },
                "node2b",
                output_dir,
            )
            self.assertIsNotNone(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["target_subnode"], "node2b")
            self.assertEqual(len(payload["proposed_rules_changes"]), 1)

    @patch("calibration_feedback_agent.request_claude_feedback")
    def test_run_feedback_cycle_skips_when_empty(self, mock_claude):
        """Feedback cycle skips when there are no disagreements and few papers."""
        with tempfile.TemporaryDirectory() as tmp:
            batch_path = Path(tmp) / "node2b_calibration_test.json"
            batch_path.write_text(json.dumps({
                "batch_id": "node2b_calibration_test",
                "target_subnode": "node2b",
                "results": [],
            }), encoding="utf-8")
            result = calibration_feedback_agent.run_feedback_cycle(
                batch_path,
                output_dir=Path(tmp),
                skip_lock=True,
            )
            self.assertEqual(result["status"], "skipped")
            mock_claude.assert_not_called()


if __name__ == "__main__":
    unittest.main()
