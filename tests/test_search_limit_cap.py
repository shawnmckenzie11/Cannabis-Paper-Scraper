"""?limit= is capped at 100 on /api/search and /api/papers/search-simple for all users."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE = {"id": 1, "pmid": "00000001", "doi": "10.0000/fake.00000001", "title": "Fake paper", "year": 2000}


class TestSearchLimitCap(unittest.TestCase):
    def setUp(self):
        import app as m
        self.m = m
        m.app.config["TESTING"] = True
        m.limiter.reset()
        self.client = m.app.test_client()

    def tearDown(self):
        self.m.limiter.reset()

    def _login(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True

    def _search(self, limit):
        db = MagicMock()
        seen = []

        def sp(f, include_total=False):
            seen.append(f.get("limit"))
            rows = [dict(FAKE, id=i) for i in range(f.get("limit") or 0)]
            return (rows, 5000) if include_total else rows
        db.search_papers.side_effect = sp
        with patch.object(self.m, "DatabaseManager", return_value=db):
            r = self.client.get(f"/api/search?limit={limit}")
        self.assertEqual(r.status_code, 200)
        return r.get_json(), seen

    def _simple(self, limit):
        db = MagicMock()
        conn = db.get_connection.return_value
        conn.execute.return_value.fetchall.return_value = []
        with patch.object(self.m, "DatabaseManager", return_value=db):
            r = self.client.get(f"/api/papers/search-simple?limit={limit}")
        self.assertEqual(r.status_code, 200)
        return conn.execute.call_args[0][1][-1]

    def test_search_clamped(self):
        for login in (False, True):
            if login:
                self._login()
            data, seen = self._search(5000)
            self.assertTrue(seen and all(s == 100 for s in seen), seen)
            self.assertLessEqual(len(data["papers"]), 100)
            self.assertEqual(data["limit"], 100)

    def test_search_small_limit_unchanged(self):
        data, seen = self._search(5)
        self.assertEqual(seen[0], 5)

    def test_search_simple_clamped(self):
        self.assertEqual(self._simple(5000), 100)
        self._login()
        self.assertEqual(self._simple(5000), 100)
        self.assertEqual(self._simple(7), 7)


if __name__ == "__main__":
    unittest.main()
