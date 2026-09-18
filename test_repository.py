"""Tests for the thin SQLAlchemy Core repository layer."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from repository.dialect import DialectSQL
from repository.engine import reset_engine_cache, sqlalchemy_url, using_postgres
from repository.filters import build_filter_clauses
from repository.session import qmark_to_named


class DialectSQLTests(unittest.TestCase):
    """Dialect fragments must be generated at the source, not rewritten later."""

    def test_sqlite_json_membership_keeps_json_each(self):
        """SQLite filters still use json_each so existing dashboard tests hold."""
        dialect = DialectSQL("sqlite")
        clauses, params = build_filter_clauses(
            {"study_type": "RCT", "outcome": "anxiety"},
            dialect=dialect,
            tab_columns_exist=True,
        )
        joined = " ".join(clauses)
        self.assertIn("json_each(papers.study_type)", joined)
        self.assertIn("json_each(papers.outcome_domain)", joined)
        self.assertNotIn("jsonb_array_elements_text", joined)
        self.assertIn("RCT", params)
        self.assertIn("anxiety", params)

    def test_postgres_json_membership_has_no_json_each(self):
        """Postgres filters emit jsonb helpers instead of SQLite json_each."""
        dialect = DialectSQL("postgresql")
        clauses, _params = build_filter_clauses(
            {"study_type": "RCT", "outcome": "anxiety", "query": "thc"},
            dialect=dialect,
            tab_columns_exist=True,
        )
        joined = " ".join(clauses)
        self.assertNotIn("json_each", joined)
        self.assertNotIn("json_valid", joined)
        self.assertNotIn("papers_fts", joined)
        self.assertIn("jsonb_array_elements_text", joined)
        self.assertIn("websearch_to_tsquery", joined)

    def test_postgres_species_filter_avoids_json_each(self):
        """Species UI filters must not rely on the deprecated rewriter."""
        dialect = DialectSQL("postgresql")
        clauses, params = build_filter_clauses(
            {"species": "mouse"},
            dialect=dialect,
            tab_columns_exist=True,
        )
        joined = " ".join(clauses)
        self.assertNotIn("json_each", joined)
        self.assertIn("jsonb_array_elements_text", joined)
        self.assertIn("Animal Models (Mouse)", params)

    def test_indexed_tab_sql_is_backend_neutral(self):
        """Indexed tab_* predicates are identical on both dialects."""
        for name in ("sqlite", "postgresql"):
            dialect = DialectSQL(name)
            clauses, _ = build_filter_clauses(
                {"tab": "clinical"},
                dialect=dialect,
                tab_columns_exist=True,
            )
            self.assertIn("papers.tab_clinical = 1", " ".join(clauses))


class SessionBindTests(unittest.TestCase):
    """Placeholder adaptation must not rewrite dialect SQL."""

    def test_qmark_to_named_preserves_sql_except_placeholders(self):
        """Only '?' binds are rewritten; JSON/FTS text is left alone."""
        sql, binds = qmark_to_named(
            "SELECT id FROM papers WHERE year >= ? AND title = ?",
            (2018, "THC"),
        )
        self.assertEqual(sql, "SELECT id FROM papers WHERE year >= :p0 AND title = :p1")
        self.assertEqual(binds, {"p0": 2018, "p1": "THC"})

    def test_qmark_count_mismatch_raises(self):
        """Mismatched binds fail loudly instead of silent rewrite."""
        with self.assertRaises(ValueError):
            qmark_to_named("SELECT id FROM papers WHERE year = ?", (1, 2))


class EngineConfigTests(unittest.TestCase):
    """Postgres URL vs local SQLite path selection."""

    def test_using_postgres_from_database_url(self):
        """DATABASE_URL starting with postgres selects production."""
        self.assertTrue(using_postgres("postgresql://user@host/db"))
        self.assertTrue(using_postgres("postgres://user@host/db"))
        self.assertFalse(using_postgres(""))
        self.assertFalse(using_postgres(None))

    def test_sqlalchemy_url_rewrites_postgres_scheme(self):
        """SQLAlchemy 2 requires postgresql+psycopg2, not postgres://."""
        url = sqlalchemy_url(database_url="postgres://user:pass@host:5432/app")
        self.assertTrue(url.startswith("postgresql+psycopg2://"))

    def test_sqlalchemy_url_uses_sqlite_path_when_no_database_url(self):
        """Local/dev uses sqlite:/// plus DATABASE_PATH or the given path."""
        url = sqlalchemy_url(db_path="/tmp/dev.db", database_url="")
        self.assertEqual(url, "sqlite:////tmp/dev.db")


class PapersRepositorySqliteTests(unittest.TestCase):
    """End-to-end insert/search on a throwaway SQLite file."""

    def setUp(self):
        reset_engine_cache()
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        from db_manager import DatabaseManager

        DatabaseManager._initialized = False
        DatabaseManager._tab_flags_ready_cache = None
        DatabaseManager._tab_columns_exist_cache = None
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        reset_engine_cache()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_facade_insert_search_does_not_use_postgres_shim(self):
        """Catalog I/O goes through SQLAlchemy, not PostgresCursorWrapper."""
        from db_manager import PostgresCursorWrapper

        with patch.object(PostgresCursorWrapper, "execute") as shim_execute:
            paper_id = self.db.insert_paper(
                {
                    "pmid": "999001",
                    "doi": "10.1001/repo.layer",
                    "title": "Repository layer THC anxiety trial",
                    "authors": ["Ada Lovelace"],
                    "journal": "Journal of Tests",
                    "year": 2026,
                    "abstract": "Vaporized Bedrocan for anxiety.",
                    "study_type": "RCT",
                    "exposure_method": "vaporized",
                    "outcome_domain": ["anxiety"],
                    "open_access": 1,
                    "citation_count": 3,
                    "publication_type": "original research",
                }
            )
            saved = self.db.get_paper(paper_id)
            results = self.db.search_papers({"query": "Bedrocan anxiety"})
            count = self.db.count_papers({"study_type": "RCT"})
            shim_execute.assert_not_called()

        self.assertEqual(saved["pmid"], "999001")
        self.assertEqual(saved["outcome_domain"], ["anxiety"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], paper_id)
        self.assertEqual(count, 1)

    def test_harvest_module_imports_repository(self):
        """Harvest paper I/O is bound to the repository API."""
        root = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(root, "harvest.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("from repository import get_papers_repository", source)
        self.assertNotIn("from db_manager import DatabaseManager", source)


if __name__ == "__main__":
    unittest.main()
