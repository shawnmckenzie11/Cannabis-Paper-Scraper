"""Tests for local Postgres ↔ SQLite sync helpers."""

import os
import sqlite3
import tempfile
import unittest

from db_manager import DatabaseManager
from local_sync import (
    BASELINE_TABLE,
    DIRTY_TABLE,
    collect_delta_papers,
    ensure_sync_schema,
    intersect_table_columns,
    mark_papers_dirty,
    merged_update_sql,
    paper_row_to_extracted,
    push_update_sql,
    save_baseline_rows,
    tracked_row_differs,
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
        ensure_sync_schema(self.conn)
        existing_cols = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        for col_name in (
            "tab_preclinical",
            "tab_clinical",
            "tab_unclassified_preclinical",
            "tab_tangential",
            "tab_review",
        ):
            if col_name not in existing_cols:
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

    def test_collect_delta_papers_includes_dirty_without_baseline(self):
        """Dirty papers without a baseline snapshot are still eligible for push."""
        mark_papers_dirty(self.conn, [1])
        deltas = collect_delta_papers(self.conn)
        self.assertEqual(len(deltas), 1)
        baseline, current = deltas[0]
        self.assertIsNone(baseline.get("classifier_version"))
        self.assertEqual(current["classifier_version"], "maude-2.0.0")

    def test_tracked_row_differs_on_tab_flags(self):
        """Tab flag changes are detected even when classifier fields are unchanged."""
        paper = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        save_baseline_rows(self.conn, [paper], replace_all=True)
        self.conn.execute("UPDATE papers SET tab_clinical = 0 WHERE id = 1")
        self.conn.commit()
        current = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        baseline = dict(paper)
        self.assertTrue(tracked_row_differs(baseline, current))

    def test_push_update_sql_uses_stored_tab_flags(self):
        """Push SQL writes stored tab_* values from the local row."""
        paper = dict(
            self.conn.execute("SELECT * FROM papers WHERE id = 1").fetchone()
        )
        sql, params = push_update_sql(paper)
        self.assertIn("tab_clinical = ?", sql)
        self.assertIn(int(paper["tab_clinical"]), params[:-1])

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
        """Baseline schema helper creates the sync tables."""
        row = self.conn.execute(
            f"SELECT name FROM sqlite_master WHERE name = '{BASELINE_TABLE}'"
        ).fetchone()
        self.assertIsNotNone(row)
        dirty = self.conn.execute(
            f"SELECT name FROM sqlite_master WHERE name = '{DIRTY_TABLE}'"
        ).fetchone()
        self.assertIsNotNone(dirty)


if __name__ == "__main__":
    unittest.main()
