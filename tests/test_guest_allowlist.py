"""Read-only guest view (seed proposal §6.5): allowlisted GETs only, no save/export, no gold/expert/split fields."""
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SENSITIVE = re.compile(r"gold|expert|rationale|(^|_)split($|_)|split_label", re.I)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _fake_paper():
    return {
        "id": 1, "title": "T", "date_harvested": None,
        "is_golden": True, "gold_badge": "gold", "expert_reason": "x",
        "expert_locked_fields": ["a"], "rationale": "why", "llm_rationale": "y",
        "split": "test", "eval_split": "test", "split_label": "test",
        "nested": {"golden_status": 1, "keep": 2},
    }


class TestGuestAllowlist(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def _fake_db(self):
        db = MagicMock()
        db.search_papers.side_effect = lambda f, include_total=False: (
            ([_fake_paper()], 1) if include_total else [_fake_paper()])
        db.get_paper.return_value = _fake_paper()
        db.get_metadata.return_value = None
        return db

    def test_allowlist_contents(self):
        a = self.app_module._is_guest_allowlisted
        for p in ("/", "/api/search", "/api/papers/5", "/api/papers/5/references",
                  "/api/papers/5/cited-by", "/api/graph/network", "/api/papers/search-simple"):
            self.assertTrue(a(p), p)
        for p in ("/api/analyses/1/export-csv", "/api/analyses", "/settings",
                  "/api/analyze", "/api/papers/delete", "/calibration/dashboard"):
            self.assertFalse(a(p), p)

    def test_guest_get_allowlisted_page_200(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_guest_get_allowlisted_api_not_gated(self):
        with patch.object(self.app_module, "DatabaseManager", return_value=self._fake_db()):
            for p in ("/api/search", "/api/papers/1"):
                r = self.client.get(p)
                self.assertEqual(r.status_code, 200, p)

    def test_guest_post_to_allowlisted_api_rejected(self):
        for p in ("/api/search", "/api/papers/1", "/"):
            r = self.client.post(p, json={})
            self.assertIn(r.status_code, (401, 403, 302), p)
        r = self.client.post("/api/search", json={})
        self.assertIn(r.status_code, (401, 403))

    def test_guest_non_get_anywhere_rejected(self):
        for method, p in (("post", "/api/analyze"), ("put", "/api/user/dashboard-preferences"),
                          ("delete", "/api/analyses/1"), ("post", "/api/papers/delete")):
            r = getattr(self.client, method)(p, json={})
            self.assertEqual(r.status_code, 401, (method, p))

    def test_guest_export_requires_login(self):
        r = self.client.get("/api/analyses/1/export-csv")
        self.assertIn(r.status_code, (401, 302))

    def test_save_guest_removed(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
        r = self.client.post("/api/analyses/save-guest", json={})
        self.assertEqual(r.status_code, 404)

    def test_guest_json_has_no_sensitive_keys(self):
        with patch.object(self.app_module, "DatabaseManager", return_value=self._fake_db()):
            for p in ("/api/search", "/api/papers/1"):
                data = self.client.get(p).get_json()
                bad = [k for k in _keys(data) if SENSITIVE.search(k)]
                self.assertEqual(bad, [], p)
                self.assertTrue(list(_keys(data)))

    def test_logged_in_user_still_sees_fields(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
        with patch.object(self.app_module, "DatabaseManager", return_value=self._fake_db()):
            data = self.client.get("/api/papers/1").get_json()
        self.assertIn("is_golden", data["paper"])


if __name__ == "__main__":
    unittest.main()
