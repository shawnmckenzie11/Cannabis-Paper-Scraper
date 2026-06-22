"""Tests for Maude disagreement resolution and cue learning."""

import json
import tempfile
import unittest
from pathlib import Path

import maude_classifier
import maude_feedback


class TestMaudeFeedback(unittest.TestCase):
    """Tests cue extraction and learned routing updates."""

    def test_extract_cue_from_quoted_explanation(self):
        """Quoted phrases in expert explanations become cue candidates."""
        cue = maude_feedback.extract_cue_from_explanation(
            "Abstract states 'This overview paper' so this is a review.",
            "This overview paper discusses CBD oil products.",
        )
        self.assertEqual(cue, "this overview paper")

    def test_learned_cue_routes_overview_paper_to_review(self):
        """Learned overview-paper cue routes Maude to review publication_type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            maude_cues = __import__("maude_cues")
            maude_cues.apply_learned_cue(
                "node1b_reviews",
                "overview paper",
                "publication_type",
                17330,
                "overview paper in abstract",
                output_dir=output_dir,
            )

            old_env = __import__("os").environ.get("CALIBRATION_OUTPUT_DIR")
            __import__("os").environ["CALIBRATION_OUTPUT_DIR"] = str(output_dir)
            try:
                result = maude_classifier.classify_paper(
                    "The Trouble with CBD Oil",
                    "This overview paper summarizes regulatory issues with CBD oil products.",
                )
            finally:
                if old_env is None:
                    __import__("os").environ.pop("CALIBRATION_OUTPUT_DIR", None)
                else:
                    __import__("os").environ["CALIBRATION_OUTPUT_DIR"] = old_env

            self.assertEqual(result["publication_type"], "review")

    def test_claude_auto_skips_eval_counter(self):
        """Auto-resolutions can skip feedback_corrections_since_eval increment."""
        class FakeDB:
            def __init__(self):
                self.increment_calls = 0

            def increment_metadata(self, key, amount=1):
                self.increment_calls += amount

            def set_metadata(self, key, value):
                return None

            def insert_feedback_audit(self, **kwargs):
                self.last_audit_version = kwargs.get("classifier_version")

        fake_db = FakeDB()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            batch_id = "node2b_calibration_test"
            batch_path = output_dir / f"{batch_id}.json"
            batch_path.write_text(json.dumps({
                "batch_id": batch_id,
                "results": [{
                    "paper_id": 99,
                    "title": "Rat gavage study",
                    "abstract": "Mice received oral gavage of THC.",
                    "routing_subnode": "node2b",
                    "llm": {"publication_type": "original research", "study_type": ["Animal Models (Mouse)"]},
                    "maude": {"publication_type": "original research", "study_type": ["Animal Models (Rat)"]},
                    "disagreement": {
                        "fields": {
                            "study_type": {
                                "maude": ["Animal Models (Rat)"],
                                "llm": ["Animal Models (Mouse)"],
                            }
                        }
                    },
                }],
            }), encoding="utf-8")

            old_env = __import__("os").environ.get("CALIBRATION_OUTPUT_DIR")
            __import__("os").environ["CALIBRATION_OUTPUT_DIR"] = str(output_dir)
            try:
                maude_feedback.resolve_disagreement(
                    paper_id=99,
                    batch_id=batch_id,
                    field_resolutions=[{
                        "field": "study_type",
                        "source": "llm",
                        "resolved_value": ["Animal Models (Mouse)"],
                        "explanation": "Methods section says murine model",
                    }],
                    output_dir=output_dir,
                    db=fake_db,
                    skip_feedback_eval_counter=True,
                    resolution_source="claude_auto",
                )
            finally:
                if old_env is None:
                    __import__("os").environ.pop("CALIBRATION_OUTPUT_DIR", None)
                else:
                    __import__("os").environ["CALIBRATION_OUTPUT_DIR"] = old_env

        self.assertEqual(fake_db.increment_calls, 0)
        self.assertTrue(str(fake_db.last_audit_version).startswith("claude-auto-feedback-"))


if __name__ == "__main__":
    unittest.main()
