"""Tests for golden Claude patch feedback helpers."""

import unittest

import calibration_feedback_agent as cfa


class GoldenClaudeFeedbackTests(unittest.TestCase):
    """Unit tests for golden feedback prompt packet building."""

    def test_build_golden_claude_paper_packets_includes_text_and_labels(self):
        """Packets should carry golden LLM labels, Maude output, and text excerpts."""
        batch_payload = {
            "results": [
                {
                    "paper_id": 101,
                    "title": "Test paper",
                    "content_tier": "abstract_only",
                    "llm": {
                        "study_type": "clinical_observational",
                        "exposure_method": ["inhaled"],
                        "classifier_version": "llm-golden-test",
                    },
                    "maude": {
                        "study_type": "clinical_observational",
                        "exposure_method": ["oral"],
                    },
                    "scoped_disagreement": {
                        "fields": {
                            "exposure_method": {
                                "llm": ["inhaled"],
                                "maude": ["oral"],
                            },
                        },
                    },
                },
            ],
        }
        llm_results = {
            "results": [
                {
                    "paper_id": 101,
                    "text": "Title line\n\nAbstract about inhaled cannabis exposure.",
                    "classifier_version": "llm-golden-test",
                    "classification_confidence": 0.9,
                    "text_source": "abstract",
                },
            ],
        }
        packets = cfa.build_golden_claude_paper_packets(batch_payload, llm_results)
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet["paper_id"], 101)
        self.assertIn("inhaled", packet["golden_llm_ground_truth"]["exposure_method"])
        self.assertEqual(packet["maude_classification"]["exposure_method"], ["oral"])
        self.assertIn("inhaled cannabis", packet["text_excerpt"])
        self.assertIn("exposure_method", packet["scoped_disagreements"])

    def test_truncate_text_for_prompt(self):
        """Long excerpts should be truncated with a marker."""
        long_text = "x" * 4000
        truncated = cfa._truncate_text_for_prompt(long_text, max_chars=100)
        self.assertLessEqual(len(truncated), 150)
        self.assertIn("truncated", truncated)

    def test_golden_handoff_defaults_to_synthesized(self):
        """Call #2 should not use Claude unless GOLDEN_HANDOFF_CLAUDE=1."""
        import os
        os.environ.pop("GOLDEN_HANDOFF_CLAUDE", None)
        os.environ.pop("GOLDEN_SYNTHESIZE_HANDOFF_ONLY", None)
        self.assertFalse(cfa._golden_handoff_uses_claude())
        os.environ["GOLDEN_HANDOFF_CLAUDE"] = "1"
        self.assertTrue(cfa._golden_handoff_uses_claude())
        os.environ.pop("GOLDEN_HANDOFF_CLAUDE", None)


if __name__ == "__main__":
    unittest.main()
