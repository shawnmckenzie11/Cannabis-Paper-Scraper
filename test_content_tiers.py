# test_content_tiers.py
"""Tests for content tier inference and SQL filters."""

import os
import tempfile
import unittest

import content_tiers
from db_manager import DatabaseManager


class ContentTierTests(unittest.TestCase):
    """Validates content tier helpers."""

    def test_infer_pdf_extracted(self):
        """PDF reclassify classifier_version maps to pdf_extracted tier."""
        tier = content_tiers.infer_content_tier({
            "classifier_version": "llm-pdf-reclassify-1.0.1",
            "full_text_link": "https://example.com/paper.pdf",
        })
        self.assertEqual(tier, content_tiers.CONTENT_TIER_PDF_EXTRACTED)

    def test_infer_abstract_reclassify(self):
        """Abstract reclassify version maps to abstract_reclassify tier."""
        tier = content_tiers.infer_content_tier({
            "classifier_version": "llm-reclassify-1.0.1",
        })
        self.assertEqual(tier, content_tiers.CONTENT_TIER_ABSTRACT_RECLASSIFY)

    def test_fields_in_scope_for_abstract_tier_drop_methods_fields(self):
        """Abstract tiers exclude Methods-heavy fields from RL scope."""
        scope = content_tiers.fields_in_scope_for_tier(
            "node2b",
            content_tiers.CONTENT_TIER_ABSTRACT_RECLASSIFY,
        )
        self.assertIn("study_type", scope)
        self.assertNotIn("dose_mg", scope)
        self.assertNotIn("thc_mg_kg", scope)

    def test_pdf_extracted_sql_clause(self):
        """PDF extracted tier SQL matches llm-pdf-reclassify prefix."""
        clause, params = content_tiers.content_tier_sql_clause(
            content_tiers.CONTENT_TIER_PDF_EXTRACTED,
        )
        self.assertIn("classifier_version LIKE", clause)
        self.assertEqual(params, ["llm-pdf-reclassify-%"])


class SearchPapersIncludeTotalTests(unittest.TestCase):
    """Validates search_papers include_total API used by the UI."""

    def setUp(self):
        """Uses an isolated SQLite database for filter tests."""
        self._old_db_path = os.environ.get("DATABASE_PATH")
        self._old_db_url = os.environ.pop("DATABASE_URL", None)
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        os.environ["DATABASE_PATH"] = self._tmp.name
        DatabaseManager._initialized = False
        self.db = DatabaseManager(db_path=self._tmp.name)

    def tearDown(self):
        """Restores the previous DATABASE_PATH."""
        if self._old_db_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._old_db_path
        if self._old_db_url is not None:
            os.environ["DATABASE_URL"] = self._old_db_url
        DatabaseManager._initialized = False
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_search_papers_include_total_returns_tuple(self):
        """search_papers(include_total=True) returns papers and total count."""
        self.db.insert_paper({
            "title": "Cannabis study",
            "abstract": "THC administration in rats.",
            "classifier_version": "llm-pdf-reclassify-1.0.1",
            "publication_type": "original research",
            "study_type": '["Animal Models (Rat)"]',
            "pmid": "9000001",
        })
        self.db.insert_paper({
            "title": "Review",
            "abstract": "Review of cannabis.",
            "classifier_version": "llm-reclassify-1.0.1",
            "publication_type": "review",
            "study_type": '["review"]',
            "pmid": "9000002",
        })

        results, total = self.db.search_papers(
            {"classification_level": "claude_pdf", "limit": 10, "offset": 0},
            include_total=True,
        )
        self.assertEqual(total, 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(str(results[0]["classifier_version"]).startswith("llm-pdf-reclassify"))


if __name__ == "__main__":
    unittest.main()
