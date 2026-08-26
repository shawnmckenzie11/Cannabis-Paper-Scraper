"""Tests for indexed database UI tab membership helpers."""
import json
import os
import tempfile
import unittest
from datetime import datetime

from paper_tab_flags import (
    LEGACY_TAB_SQL,
    compute_tab_flags,
    dashboard_tab_sql,
    legacy_tab_sql_for,
    recent_range_sql,
    tab_sql_for,
)
from db_manager import DatabaseManager


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

    def test_dashboard_tab_sql_covers_primary_tabs(self):
        """Dashboard tab SQL uses indexed columns for all primary tabs."""
        self.assertIn("tab_preclinical", dashboard_tab_sql("all_original"))
        self.assertIn("tab_clinical", dashboard_tab_sql("all_original"))
        self.assertEqual(dashboard_tab_sql("clinical"), "papers.tab_clinical = 1")
        self.assertEqual(dashboard_tab_sql("review"), "papers.tab_review = 1")
        self.assertIn("tab_tangential", dashboard_tab_sql("unclassified"))

    def test_legacy_tab_sql_includes_all_original_and_unclassified(self):
        """New dashboard tabs have legacy query-time SQL fragments."""
        self.assertIn("all_original", LEGACY_TAB_SQL)
        self.assertIn("unclassified", LEGACY_TAB_SQL)
        self.assertTrue(legacy_tab_sql_for("all_original"))
        self.assertTrue(legacy_tab_sql_for("unclassified"))

    def test_insert_paper_sets_preclinical_tab_flag(self):
        """Harvested original research must land on indexed dashboard tabs."""
        old_path = os.environ.get("DATABASE_PATH")
        old_url = os.environ.pop("DATABASE_URL", None)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        os.environ["DATABASE_PATH"] = tmp.name
        DatabaseManager._initialized = False
        DatabaseManager._tab_flags_ready_cache = None
        try:
            db = DatabaseManager(db_path=tmp.name)
            paper_id = db.insert_paper({
                "title": "Mouse THC study",
                "abstract": "We treated mice with THC.",
                "publication_type": "original research",
                "study_type": ["Animal Models (Mouse)"],
                "ingestion_status": "relevant",
                "pmid": "9000101",
                "date_harvested": datetime.now().strftime("%Y-%m-%d") + "T14:00:00",
            })
            conn = db.get_connection()
            row = conn.execute(
                "SELECT tab_preclinical, tab_clinical, tab_review FROM papers WHERE id = ?",
                (paper_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(int(row["tab_preclinical"]), 1)
            self.assertEqual(int(row["tab_clinical"]), 0)
            self.assertEqual(int(row["tab_review"]), 0)
            papers, total = db.search_papers(
                {"tab": "all_original", "recent_range": "today", "limit": 10, "offset": 0},
                include_total=True,
            )
            self.assertGreaterEqual(total, 1)
            self.assertTrue(any(int(p["id"]) == int(paper_id) for p in papers))

            orphan_id = db.insert_paper({
                "title": "Orphan mouse paper",
                "abstract": "Mice received THC.",
                "publication_type": "original research",
                "study_type": ["Animal Models (Mouse)"],
                "ingestion_status": "relevant",
                "pmid": "9000102",
                "date_harvested": datetime.now().strftime("%Y-%m-%d") + "T14:00:00",
            })
            conn = db.get_connection()
            conn.execute(
                "UPDATE papers SET tab_preclinical = 0, tab_clinical = 0, "
                "tab_unclassified_preclinical = 0, tab_tangential = 0, tab_review = 0 "
                "WHERE id = ?",
                (orphan_id,),
            )
            conn.commit()
            conn.close()
            repaired = db.sync_orphan_tab_flags_since(datetime.now().strftime("%Y-%m-%d"))
            self.assertGreaterEqual(repaired, 1)
            conn = db.get_connection()
            row = conn.execute(
                "SELECT tab_preclinical FROM papers WHERE id = ?",
                (orphan_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(int(row["tab_preclinical"]), 1)
        finally:
            if old_path is None:
                os.environ.pop("DATABASE_PATH", None)
            else:
                os.environ["DATABASE_PATH"] = old_path
            if old_url is not None:
                os.environ["DATABASE_URL"] = old_url
            DatabaseManager._initialized = False
            DatabaseManager._tab_flags_ready_cache = None
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        """Recency filter helper returns a SQL fragment and bound params."""
        clause, params = recent_range_sql("week")
        self.assertIn("date_harvested", clause)
        self.assertEqual(len(params), 1)


class TestDashboardSearchFilters(unittest.TestCase):
    """Validate tab + numeric filter clause assembly without touching paper rows."""

    def test_all_original_and_recent_range_stack(self):
        """Tab SQL and recency filters compose in one WHERE clause."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "tab": "all_original",
            "recent_range": "week",
        })
        joined = " ".join(clauses)
        if db._tab_flag_columns_exist():
            self.assertIn("tab_preclinical", joined)
        else:
            self.assertTrue("publication_type" in joined or "original research" in joined)
        self.assertTrue(any("date_harvested" in clause for clause in clauses))
        self.assertEqual(len(params), 1)

    def test_clinical_tab_uses_indexed_sql_when_ready(self):
        """Clinical tab filter prefers indexed tab_clinical when columns exist."""
        db = DatabaseManager()
        clauses, _ = db._build_filter_clauses({"tab": "clinical"})
        joined = " ".join(clauses)
        if db._tab_flag_columns_exist():
            self.assertIn("papers.tab_clinical = 1", joined)
        else:
            self.assertIn("clinical", joined.lower())

    def test_clinical_tab_with_sample_size_filter(self):
        """Clinical sub-node filters apply numeric range predicates."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "tab": "clinical",
            "sample_size_min": 10,
            "sample_size_max": 100,
        })
        self.assertIn("papers.sample_size >= ?", clauses)
        self.assertIn("papers.sample_size <= ?", clauses)
        self.assertEqual(params[-2:], [10.0, 100.0])

    def test_clinical_population_age_filter(self):
        """Study population age filter builds exact IN clause on population_age."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "tab": "clinical",
            "population_age": "pediatric,adult",
        })
        joined = " ".join(clauses)
        self.assertIn("papers.population_age", joined)
        self.assertIn("pediatric", params)
        self.assertIn("adult", params)

    def test_clinical_population_sex_filter(self):
        """Study population sex filter builds exact IN clause on population_sex."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "tab": "clinical",
            "population_sex": "male,female",
        })
        joined = " ".join(clauses)
        self.assertIn("papers.population_sex", joined)
        self.assertIn("male", params)
        self.assertIn("female", params)

    def test_preclinical_species_filter(self):
        """Species filter matches papers.species and study_type animal labels."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "tab": "preclinical",
            "species": "mouse,rat",
        })
        joined = " ".join(clauses)
        self.assertIn("json_each(papers.study_type)", joined)
        self.assertIn("Animal Models (Mouse)", params)
        self.assertIn("Animal Models (Rat)", params)

    def test_preclinical_species_filter_returns_mouse_papers(self):
        """Mouse species filter should match preclinical papers via study_type."""
        db = DatabaseManager()
        _, unfiltered_total = db.search_papers({"tab": "preclinical", "limit": 1}, include_total=True)
        _, filtered_total = db.search_papers(
            {"tab": "preclinical", "species": "mouse", "limit": 1},
            include_total=True,
        )
        self.assertGreater(unfiltered_total, 0)
        self.assertGreater(filtered_total, 0)
        self.assertLess(filtered_total, unfiltered_total)

    def test_has_pdf_and_full_text_filters(self):
        """PDF and full-text link filters apply optional SQL predicates."""
        db = DatabaseManager()
        pdf_clauses, _ = db._build_filter_clauses({"has_pdf": True})
        self.assertTrue(any("full_text_link" in clause and ".pdf" in clause for clause in pdf_clauses))

        fulltext_clauses, _ = db._build_filter_clauses({"has_full_text": True})
        self.assertTrue(any("pubmed.ncbi.nlm.nih.gov" in clause for clause in fulltext_clauses))


    def test_search_papers_include_total_returns_tuple(self):
        """search_papers(include_total=True) returns papers and total count."""
        db = DatabaseManager()
        results, total = db.search_papers({"tab": "clinical", "limit": 5}, include_total=True)
        self.assertIsInstance(results, list)
        self.assertIsInstance(total, int)
        if results:
            self.assertNotIn("abstract", results[0])
            self.assertIn("title", results[0])

    def test_publication_type_filter_sql(self):
        """Publication type sidebar filter builds IN clause (§5.2)."""
        db = DatabaseManager()
        clauses, params = db._build_filter_clauses({
            "publication_type": "review,meta-analysis",
        })
        joined = " ".join(clauses)
        self.assertIn("publication_type", joined)
        self.assertIn("review", params)
        self.assertIn("meta-analysis", params)

    def test_incomplete_tab_backfill_still_uses_indexed_sql(self):
        """When tab_* columns exist, search uses indexed SQL even if backfill metadata is false."""
        from unittest.mock import patch

        db = DatabaseManager()
        with patch.object(db, "_tab_flag_columns_exist", return_value=True), patch.object(
            db, "get_metadata", return_value="false"
        ):
            DatabaseManager._tab_flags_ready_cache = None
            self.assertFalse(db._tab_flags_are_ready())
            clauses, _ = db._build_filter_clauses({"tab": "clinical"})
            joined = " ".join(clauses)
            self.assertIn("papers.tab_clinical = 1", joined)
        DatabaseManager._tab_flags_ready_cache = None


if __name__ == "__main__":
    unittest.main()
