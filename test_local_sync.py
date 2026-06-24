"""Tests for local Postgres ↔ SQLite sync helpers."""

import os
import sqlite3
import tempfile
import unittest

from db_manager import DatabaseManager
from local_sync import (
    BASELINE_TABLE,
    collect_delta_papers,
    ensure_baseline_schema,
    intersect_table_columns,
    merged_update_sql,
    paper_row_to_extracted,
    save_baseline_rows,
)


class LocalSyncTests(unittest.TestCase):
    """Baseline snapshot and delta detection helpers."""

    def setUp(self):
        """Create a temporary SQLite database with one paper row."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        saved_url = os.environ.pop("DATABASE_URL", None)
        try:
            db = DatabaseManager(db_path=self.db_path)
            db.init_db()
        finally:
            if saved_url is not None:
                os.environ["DATABASE_URL"] = saved_url

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        ensure_baseline_schema(self.conn)
        for col_name in (
            "tab_preclinical",
            "tab_clinical",
            "tab_unclassified_preclinical",
            "tab_tangential",
            "tab_review",
        ):
            self.conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} INTEGER DEFAULT 0")
        self.conn.commit()
        self.conn.execute(
            """
            INSERT INTO papers (
                id, title, date_harvested, study_type, exposure_method, cannabis_type,
                outcome_domain, publication_type, classifier_version, expert_locked_fields,
                tab_preclinical, tab_clinical, tab_unclassified_preclinical, tab_tangential, tab_review
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Test paper",
                "2026-01-01",
                '["Clinical Trial"]',
                '["oral"]',
                '["THC"]',
                '["pain"]',
                "original research",
                "maude-2.0.0",
                "[]",
                0,
                1,
                0,
                0,
                0,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        """Close sqlite connection and remove temp directory."""
        self.conn.close()
        self.tmpdir.cleanup()

    def test_intersect_table_columns_preserves_sqlite_order(self):
        """Shared columns keep SQLite ordering."""
        sqlite_cols = ["id", "title", "missing_local"]
        pg_cols = {"id", "title", "extra_pg"}
        self.assertEqual(
            intersect_table_columns(pg_cols, sqlite_cols),
            ["id", "title"],
        )

    def test_collect_delta_papers_detects_classifier_change(self):
        """Delta collection finds rows whose classifier_version changed."""
        paper = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        save_baseline_rows(self.conn, [paper], replace_all=True)

        self.conn.execute(
            "UPDATE papers SET classifier_version = ? WHERE id = 1",
            ("maude-2.6.0",),
        )
        self.conn.commit()

        deltas = collect_delta_papers(self.conn)
        self.assertEqual(len(deltas), 1)
        baseline, current = deltas[0]
        self.assertEqual(baseline["classifier_version"], "maude-2.0.0")
        self.assertEqual(current["classifier_version"], "maude-2.6.0")

    def test_collect_delta_papers_skips_unchanged_rows(self):
        """Unchanged rows after pull baseline produce no deltas."""
        paper = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        save_baseline_rows(self.conn, [paper], replace_all=True)
        self.assertEqual(collect_delta_papers(self.conn), [])

    def test_merged_update_sql_uses_sqlite_placeholders(self):
        """Merged UPDATE SQL stays compatible with DatabaseManager placeholder rewriting."""
        paper = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        extracted = paper_row_to_extracted(paper)
        extracted["classifier_version"] = "maude-2.6.0"
        sql, params = merged_update_sql(paper, extracted)
        self.assertIn("study_type = ?", sql)
        self.assertIn("tab_clinical = ?", sql)
        self.assertEqual(params[-1], 1)

    def test_baseline_table_created(self):
        """Baseline schema helper creates the sync table."""
        row = self.conn.execute(
            f"SELECT name FROM sqlite_master WHERE name = '{BASELINE_TABLE}'"
        ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
