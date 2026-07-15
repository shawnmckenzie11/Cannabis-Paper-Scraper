"""Tests for Maude classifier_version tier labeling."""

import unittest
from unittest.mock import patch

import calibration_pdf
import classifier


class ClassifierMaudeVersionTests(unittest.TestCase):
    """Ensure pre-resolved text sources stamp the correct classifier_version."""

    @patch("classifier.maude_classifier.classify_paper")
    @patch("classifier.get_rules_version")
    def test_classify_with_maude_preserves_pdf_source_when_text_preresolved(
        self,
        mock_rules,
        mock_classify,
    ):
        """Pre-resolved PDF text stamps maude-pdf-* instead of abstract-only label."""
        mock_rules.return_value = "2.6.0"
        mock_classify.return_value = {"study_type": ["Animal Models (Mouse)"]}

        result = classifier.classify_with_maude(
            "Title",
            "Abstract",
            full_text="PDF body with methods section.",
            text_source=calibration_pdf.CLASSIFICATION_SOURCE_PDF,
        )

        self.assertEqual(result["classifier_version"], "maude-pdf-2.6.0")
        self.assertFalse(mock_classify.call_args.kwargs["abstract_only_extraction"])

    @patch("classifier.maude_classifier.classify_paper")
    @patch("classifier.get_rules_version")
    def test_classify_with_maude_preserves_fulltext_source_when_text_preresolved(
        self,
        mock_rules,
        mock_classify,
    ):
        """Pre-resolved PMC/HTML text stamps maude-ft-*."""
        mock_rules.return_value = "2.6.0"
        mock_classify.return_value = {"study_type": ["Clinical Trial"]}

        result = classifier.classify_with_maude(
            "Title",
            "Abstract",
            full_text="PMC article body.",
            text_source=calibration_pdf.CLASSIFICATION_SOURCE_FULLTEXT,
        )

        self.assertEqual(result["classifier_version"], "maude-ft-2.6.0")

    @patch("classifier.maude_classifier.classify_paper")
    @patch("classifier.get_rules_version")
    def test_classify_with_maude_allows_abstract_downstream_when_no_pdf(
        self,
        mock_rules,
        mock_classify,
    ):
        """Without resolved text, defer abstract-only policy to classify_paper heuristics."""
        mock_rules.return_value = "2.7.0"
        mock_classify.return_value = {
            "study_type": ["Animal Models (Rat)"],
            "duration_days": 21.0,
            "administration_frequency": "daily",
        }

        classifier.classify_with_maude(
            "Cannabis oil in Wistar rats",
            "CO was administered daily throughout the 3-week experimental period.",
        )

        self.assertIsNone(mock_classify.call_args.kwargs["abstract_only_extraction"])


if __name__ == "__main__":
    unittest.main()
