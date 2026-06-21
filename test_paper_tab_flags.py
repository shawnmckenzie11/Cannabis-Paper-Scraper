"""Tests for indexed database UI tab membership helpers."""
import json
import unittest

from paper_tab_flags import compute_tab_flags, tab_sql_for


class TestPaperTabFlags(unittest.TestCase):
    """Validate tab routing parity for denormalized tab flags."""

    def test_clinical_original_research(self):
        """Clinical RCT original research maps to the clinical tab."""
        flags = compute_tab_flags(
            publication_type="original research",
            study_type=["Clinical (RCT)"],
            ingestion_status="relevant",
        )
        self.assertEqual(flags["tab_clinical"], 1)
        self.assertEqual(flags["tab_preclinical"], 0)
        self.assertEqual(flags["tab_review"], 0)

    def test_preclinical_mouse_model(self):
        """Mouse model original research maps to the preclinical tab."""
        flags = compute_tab_flags(
            publication_type="original research",
            study_type=["Animal Models (Mouse)"],
            ingestion_status="relevant",
        )
        self.assertEqual(flags["tab_preclinical"], 1)
        self.assertEqual(flags["tab_clinical"], 0)

    def test_overlap_clinical_and_preclinical(self):
        """Mixed clinical and in vitro labels may appear in both tabs."""
        flags = compute_tab_flags(
            publication_type="original research",
            study_type=["Clinical (observational)", "Cell Culture (Cell Lines)"],
            ingestion_status="relevant",
        )
        self.assertEqual(flags["tab_clinical"], 1)
        self.assertEqual(flags["tab_preclinical"], 1)

    def test_unclassified_original_research(self):
        """Original research without resolved study design is unclassified preclinical."""
        flags = compute_tab_flags(
            publication_type="original research",
            study_type=[],
            ingestion_status="relevant",
        )
        self.assertEqual(flags["tab_unclassified_preclinical"], 1)
        self.assertEqual(flags["tab_clinical"], 0)
        self.assertEqual(flags["tab_preclinical"], 0)

    def test_tangential_routing(self):
        """Tangential ingestion status maps only to the tangential tab."""
        flags = compute_tab_flags(
            publication_type="review",
            study_type=["review"],
            ingestion_status="tangential",
        )
        self.assertEqual(flags["tab_tangential"], 1)
        self.assertEqual(flags["tab_review"], 0)
        self.assertEqual(flags["tab_clinical"], 0)

    def test_irrelevant_excluded_from_primary_tabs(self):
        """Irrelevant papers are excluded from primary study tabs."""
        flags = compute_tab_flags(
            publication_type="original research",
            study_type=["Clinical (RCT)"],
            ingestion_status="irrelevant",
        )
        self.assertEqual(flags["tab_clinical"], 0)
        self.assertEqual(flags["tab_preclinical"], 0)

    def test_review_publication(self):
        """Review publication type maps to the review tab."""
        flags = compute_tab_flags(
            publication_type="systematic review",
            study_type=["review"],
            ingestion_status="relevant",
        )
        self.assertEqual(flags["tab_review"], 1)
        self.assertEqual(flags["tab_clinical"], 0)

    def test_tab_sql_uses_indexed_columns(self):
        """Tab SQL fragments should use indexed membership columns."""
        self.assertEqual(tab_sql_for("clinical"), "papers.tab_clinical = 1")
        self.assertEqual(tab_sql_for("preclinical"), "papers.tab_preclinical = 1")


if __name__ == "__main__":
    unittest.main()
