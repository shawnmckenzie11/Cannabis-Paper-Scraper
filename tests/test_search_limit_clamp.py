"""Limit clamping on /api/search and /api/papers/search-simple. Fake data and made-up IDs only."""
import os
import sqlite3
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BAD_LIMITS = ("-1", "0", "abc", "999999")
N_ROWS = 250  # more than MAX_SEARCH_LIMIT


def _fake_db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT, year INTEGER, authors TEXT)")
    conn.executemany("INSERT INTO papers VALUES (?, ?, ?, ?)",
                     [(900000 + i, f"Fake paper {i}", 2000 + i % 20, "Fake A") for i in range(N_ROWS)])
    return conn


class TestSearchLimitClamp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        app_module.app.config["TESTING"] = True
        app_module.limiter.reset()
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as s:
            s["logged_in"] = True

    def tearDown(self):
        self.m.limiter.reset()

    def test_api_search_limit_clamped(self):
        def fake_search(filters, include_total=False):
            rows = [{"id": 900000 + i, "title": "Fake", "date_harvested": None}
                    for i in range(min(filters["limit"], N_ROWS))]
            return (rows, N_ROWS) if include_total else rows
        db = MagicMock()
        db.search_papers.side_effect = fake_search
        db.get_metadata.return_value = None
        for raw in BAD_LIMITS:
            with self.subTest(limit=raw), patch.object(self.m, "DatabaseManager", return_value=db):
                r = self.client.get(f"/api/search?limit={raw}")
                self.assertNotEqual(r.status_code, 500)
                self.assertEqual(r.status_code, 200)
                data = r.get_json()
                self.assertGreaterEqual(data["limit"], 1)
                self.assertLessEqual(data["limit"], self.m.MAX_SEARCH_LIMIT)
                self.assertGreaterEqual(len(data["papers"]), 1)
                self.assertLessEqual(len(data["papers"]), self.m.MAX_SEARCH_LIMIT)
                if raw == "abc":
                    self.assertEqual(data["limit"], 50)

    def test_api_papers_search_simple_limit_clamped(self):
        for raw in BAD_LIMITS:
            db = MagicMock()
            db.get_connection.return_value = _fake_db_conn()
            with self.subTest(limit=raw), patch.object(self.m, "DatabaseManager", return_value=db):
                r = self.client.get(f"/api/papers/search-simple?limit={raw}")
                self.assertNotEqual(r.status_code, 500)
                self.assertEqual(r.status_code, 200)
                data = r.get_json()
                self.assertGreaterEqual(len(data), 1)
                self.assertLessEqual(len(data), self.m.MAX_SEARCH_LIMIT)
                if raw == "abc":
                    self.assertEqual(len(data), 50)
                if raw == "999999":
                    self.assertEqual(len(data), self.m.MAX_SEARCH_LIMIT)


if __name__ == "__main__":
    unittest.main()
