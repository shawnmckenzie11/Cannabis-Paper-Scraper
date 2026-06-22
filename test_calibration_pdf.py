# test_calibration_pdf.py
"""Tests for calibration PDF loading helpers."""

import unittest
from unittest.mock import patch

import calibration_pdf


class CalibrationPdfTests(unittest.TestCase):
    """PDF-backed Maude calibration classification."""

    @patch("reclassify_with_llm.download_and_extract_pdf_text")
    def test_classify_maude_for_calibration_uses_pdf(self, mock_download):
        """Maude receives extracted PDF text when a link is available."""
        mock_download.return_value = "Methods: oral gavage 10 mg/kg THC daily for 14 days."
        with patch("calibration_pdf.maude_classifier.classify_paper") as mock_classify:
            mock_classify.return_value = {"study_type": ["Animal Models (Mouse)"]}
            maude_out, pdf_used = calibration_pdf.classify_maude_for_calibration(
                "Title",
                "Abstract",
                full_text_link="https://example.com/paper.pdf",
                rules_version="2.6.0",
            )
        self.assertTrue(pdf_used)
        self.assertEqual(maude_out["study_type"], ["Animal Models (Mouse)"])
        mock_classify.assert_called_once()
        kwargs = mock_classify.call_args.kwargs
        self.assertIn("oral gavage", kwargs["full_text"])
        self.assertFalse(kwargs["abstract_only_extraction"])

    @patch("reclassify_with_llm.download_and_extract_pdf_text")
    def test_load_pdf_full_text_caches_by_link(self, mock_download):
        """Repeated links reuse cached PDF text within one batch run."""
        mock_download.return_value = "cached pdf body"
        cache = {}
        first = calibration_pdf.load_pdf_full_text("https://example.com/a.pdf", cache=cache)
        second = calibration_pdf.load_pdf_full_text("https://example.com/a.pdf", cache=cache)
        self.assertEqual(first, "cached pdf body")
        self.assertEqual(second, "cached pdf body")
        mock_download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
