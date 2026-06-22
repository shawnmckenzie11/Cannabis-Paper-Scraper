# test_calibration_pdf.py
"""Tests for calibration PDF loading helpers."""

import unittest
from unittest.mock import patch

import calibration_pdf


class CalibrationPdfTests(unittest.TestCase):
    """PDF-backed Maude calibration classification."""

    @patch("reclassify_with_llm.download_and_extract_pdf_text")
    def test_classify_maude_for_calibration_caches_on_fetch(self, mock_download):
        """RL classification writes fetched PDF text to the local disk cache."""
        import tempfile
        from pathlib import Path

        import paper_text_cache

        mock_download.return_value = "Methods: oral gavage THC daily."
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with patch("paper_text_cache.resolve_cache_dir", return_value=cache_dir):
                with patch("calibration_pdf.maude_classifier.classify_paper") as mock_classify:
                    mock_classify.return_value = {"study_type": ["Animal Models (Mouse)"]}
                    calibration_pdf.classify_maude_for_calibration(
                        "Title",
                        "Abstract",
                        full_text_link="https://example.com/paper.pdf",
                        paper_id=4242,
                        rules_version="2.6.0",
                    )
            entry = paper_text_cache.read_cached_entry(4242, cache_dir)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertIn("oral gavage", entry["text"])

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

    @patch("calibration_pdf.fetch_pmc_full_text")
    @patch("reclassify_with_llm.download_and_extract_pdf_text")
    def test_resolve_prefers_pdf_over_pmc(self, mock_download, mock_pmc):
        """PDF text wins over Europe PMC when both are available."""
        mock_download.return_value = "PDF methods section"
        mock_pmc.return_value = "PMC full text body"
        text, source = calibration_pdf.resolve_classification_full_text(
            full_text_link="https://example.com/paper.pdf",
            pmid="12345",
        )
        self.assertEqual(source, calibration_pdf.CLASSIFICATION_SOURCE_PDF)
        self.assertEqual(text, "PDF methods section")
        mock_pmc.assert_not_called()

    @patch("calibration_pdf.fetch_html_article_text")
    @patch("calibration_pdf.fetch_pmc_full_text")
    @patch("reclassify_with_llm.download_and_extract_pdf_text")
    def test_resolve_falls_back_to_pmc(self, mock_download, mock_pmc, mock_html):
        """Europe PMC is used when PDF extraction fails."""
        mock_download.return_value = None
        mock_pmc.return_value = "PMC article text"
        text, source = calibration_pdf.resolve_classification_full_text(
            full_text_link="https://example.com/paper.pdf",
            pmid="12345",
        )
        self.assertEqual(source, calibration_pdf.CLASSIFICATION_SOURCE_FULLTEXT)
        self.assertEqual(text, "PMC article text")
        mock_html.assert_not_called()

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
        self.assertIn("pdf:https://example.com/a.pdf", cache)

    def test_maude_classifier_version_labels(self):
        """Classifier version strings encode the text tier Maude used."""
        self.assertEqual(
            calibration_pdf.maude_classifier_version("pdf", "2.6.0"),
            "maude-pdf-2.6.0",
        )
        self.assertEqual(
            calibration_pdf.maude_classifier_version("fulltext", "2.6.0"),
            "maude-fulltext-2.6.0",
        )
        self.assertEqual(
            calibration_pdf.maude_classifier_version("abstract", "2.6.0"),
            "maude-2.6.0",
        )


if __name__ == "__main__":
    unittest.main()
