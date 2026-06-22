"""Tests for node2b patch from batch node2b_calibration_20260622_200722_248."""

import unittest

import calibration_pdf
import maude_classifier
from calibration_agent import get_rules_version


class PatchNode2b200722Tests(unittest.TestCase):
    """Regression tests from the offset-20 node2b RL holdout."""

    def test_pdf_dosing_routes_original_despite_temporal_learned_cues(self):
        """Animal mg/kg PDF text routes to original research, not review via days/weeks cues."""
        title = (
            "Cannabidiol may prevent the development of congestive hepatopathy "
            "secondary to right ventricular hypertrophy associated"
        )
        abstract = ""
        full_text = (
            "Methods: Male Wistar rats received cannabidiol (CBD, 10 mg/kg, i.p.) "
            "once daily for 21 days."
        )
        pub, _, nodes, _ = maude_classifier.route_publication_type(
            title,
            abstract,
            routing_blob=f"{title} {full_text}",
        )
        self.assertEqual(pub, "original research")
        self.assertIn("node1a_original", nodes)

    def test_learned_temporal_cues_blocked_for_review_routing(self):
        """Temporal dosing tokens must not become review strong patterns."""
        self.assertFalse(maude_cues.is_valid_review_learned_cue("days"))
        self.assertFalse(maude_cues.is_valid_review_learned_cue("weeks"))
        patterns = maude_classifier.get_review_strong_patterns()
        self.assertNotIn(r"\bdays\b", patterns)
        self.assertNotIn(r"\bweeks\b", patterns)

    def test_holdout_paper_11824_classifies_as_invivo(self):
        """Springer holdout paper extracts as in-vivo original research with PDF."""
        title = (
            "Cannabidiol may prevent the development of congestive hepatopathy "
            "secondary to right ventricular hypertrophy associated"
        )
        rules = get_rules_version()
        maude_out, pdf_used = calibration_pdf.classify_maude_for_calibration(
            title,
            "",
            full_text_link="https://link.springer.com/content/pdf/10.1007/s43440-024-00579-4.pdf",
            rules_version=rules,
        )
        self.assertTrue(pdf_used)
        self.assertEqual(maude_out.get("publication_type"), "original research")
        self.assertNotEqual(maude_out.get("study_type"), ["review"])


import maude_cues  # noqa: E402 — used in test_learned_temporal_cues_blocked


if __name__ == "__main__":
    unittest.main()
