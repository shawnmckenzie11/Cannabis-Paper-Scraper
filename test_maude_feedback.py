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


if __name__ == "__main__":
    unittest.main()
