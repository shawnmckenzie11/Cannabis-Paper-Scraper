"""Tests for methods/results section stats and PDF upload helpers."""

import unittest
from unittest.mock import patch

from maude_classifier import (
    extract_results_section,
    paper_classified_from_pdf_body,
    paper_has_direct_pdf_link,
    paper_has_methods_section,
    paper_has_results_section,
)
from section_stats import compute_section_stats, paper_row_has_methods_section


class TestSectionDetection(unittest.TestCase):
    """Section header detection for filtered-dataset stats."""

    def test_extract_results_section_from_full_text(self):
        text = "Introduction\nMethods\nWe did things.\nResults\nOutcome improved.\nDiscussion\nDone."
        self.assertIn("Results", extract_results_section(text))

    def test_paper_has_methods_from_structured_abstract(self):
        abstract = "Background: x.\nMethods: Participants were recruited.\nResults: y."
        self.assertTrue(paper_has_methods_section(None, "Title", abstract))

    def test_paper_has_results_from_structured_abstract(self):
        abstract = "Background: x.\nMethods: y.\nResults: Significant improvement observed."
        self.assertTrue(paper_has_results_section(None, abstract))

    def test_direct_pdf_link_detection(self):
        self.assertTrue(paper_has_direct_pdf_link("https://example.org/paper.pdf"))
        self.assertTrue(paper_has_direct_pdf_link("https://europepmc.org/articles/pmc123?pdf=render"))
        self.assertFalse(paper_has_direct_pdf_link("https://pubmed.ncbi.nlm.nih.gov/123/"))

    def test_pdf_tier_classifier_detection(self):
        self.assertTrue(paper_classified_from_pdf_body("llm-pdf-reclassify-2.1.0"))
        self.assertTrue(paper_classified_from_pdf_body("maude-pdf-2.6.0"))
        self.assertFalse(paper_classified_from_pdf_body("heuristic-1.0.0"))

    def test_compute_section_stats_counts(self):
        papers = [
            {"id": 1, "title": "A", "abstract": "Methods: cohort study.\nResults: improved."},
            {"id": 2, "title": "B", "abstract": "Background only."},
        ]
        with patch("section_stats.paper_text_cache.read_cached_meta_light", return_value=None):
            stats = compute_section_stats(papers)
        self.assertEqual(stats["total_count"], 2)
        self.assertEqual(stats["methods_count"], 1)
        self.assertEqual(stats["results_count"], 1)
        self.assertEqual(stats["methods_pct"], 50.0)
        self.assertEqual(stats["results_pct"], 50.0)

    def test_pdf_link_proxy_when_no_cached_text(self):
        paper = {
            "id": 99,
            "title": "Cannabis RCT",
            "abstract": "Background only.",
            "full_text_link": "https://example.org/study.pdf",
            "classifier_version": "heuristic-1.0.0",
        }
        with patch("section_stats.paper_text_cache.read_cached_meta_light", return_value=None):
            self.assertTrue(paper_row_has_methods_section(paper, paper["abstract"]))
            stats = compute_section_stats([paper])
        self.assertEqual(stats["methods_pct"], 100.0)
        self.assertEqual(stats["results_pct"], 100.0)

    def test_commentary_title_not_inferred_without_text(self):
        paper = {
            "id": 100,
            "title": "Commentary on cannabis policy",
            "abstract": "Background only.",
            "full_text_link": "https://example.org/commentary.pdf",
        }
        with patch("section_stats.paper_text_cache.read_cached_meta_light", return_value=None):
            stats = compute_section_stats([paper])
        self.assertEqual(stats["methods_pct"], 0.0)
        self.assertEqual(stats["results_pct"], 0.0)


class TestPdfUploadHelpers(unittest.TestCase):
    """PDF ingest helper behavior."""

    def test_extract_title_from_pdf_text(self):
        try:
            from harvest import extract_title_from_pdf_text
        except ModuleNotFoundError:
            self.skipTest("harvest dependencies unavailable")

        text = "Cannabis and Anxiety in Adults\nAbstract\nWe studied participants."
        title = extract_title_from_pdf_text(text, "fallback.pdf")
        self.assertEqual(title, "Cannabis and Anxiety in Adults")

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("requests"),
        "requests not installed",
    )
    @patch("harvest.paper_text_cache.write_cached_entry")
    @patch("harvest.paper_text_cache._extract_pdf_text_from_bytes")
    @patch("harvest.classifier.process_paper_metadata")
    @patch("harvest.DatabaseManager")
    def test_ingest_uploaded_pdf_updates_existing_title(
        self,
        mock_db_cls,
        mock_process,
        mock_extract,
        _mock_cache_write,
    ):
        from harvest import ingest_uploaded_pdf

        mock_extract.return_value = "Sample Paper Title\nAbstract\nMethods and results."
        mock_db = mock_db_cls.return_value
        mock_db.find_paper_id_by_title.return_value = 42
        mock_db.get_paper.return_value = {
            "title": "Sample Paper Title",
            "study_type": ["clinical_observational"],
        }
        mock_db.insert_paper.return_value = 42
        mock_process.return_value = {
            "study_type": ["clinical_rct"],
            "classifier_version": "maude-2.6.0",
        }

        with patch("harvest.paper_text_cache.write_cached_entry"):
            result = ingest_uploaded_pdf(b"%PDF-1.4 sample", filename="paper.pdf")
        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["is_new_paper"])
        self.assertEqual(result["paper_id"], 42)
        self.assertTrue(any(row["field"] == "study_type" for row in result["rows"]))
        mock_process.assert_called_once()
        self.assertFalse(mock_process.call_args.kwargs.get("run_llm"))
        mock_db.insert_paper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
