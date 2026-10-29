"""Guest UI (ships with #84): JS guest field list matches the server allowlist,
guest guards exist in index.html, and seeded SQLite guest JSON stays within the allowlist.

All seeded rows are synthetic: fake PMIDs (99000001+) and 10.99999/fake DOIs.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
INDEX = os.path.join(ROOT, "templates", "index.html")


def _html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _js_array(name):
    m = re.search(r"const %s = \[(.*?)\];" % name, _html(), re.S)
    assert m, name
    return re.findall(r'"([^"]+)"', m.group(1))


def _js_column_fields():
    m = re.search(r"const COLUMN_SOURCE_FIELDS = \{(.*?)\};", _html(), re.S)
    assert m
    return {k: re.findall(r'"([^"]+)"', v)
            for k, v in re.findall(r"(\w+):\s*\[([^\]]*)\]", m.group(1))}


def _fn_body(name):
    s = _html()
    i = s.index("function %s(" % name)
    return s[i:i + 2500]


class TestGuestFieldListMatchesServer(unittest.TestCase):
    def test_js_guest_fields_equal_server_allowlist(self):
        import app as app_module
        js = _js_array("GUEST_PUBLIC_PAPER_FIELDS")
        self.assertEqual(len(js), len(set(js)))
        self.assertEqual(set(js), set(app_module.GUEST_PAPER_FIELDS))

    def test_guest_visible_columns_only_read_public_fields(self):
        public = set(_js_array("GUEST_PUBLIC_PAPER_FIELDS"))
        cols = _js_column_fields()
        visible = {c for c, f in cols.items() if set(f) <= public}
        hidden = set(cols) - visible
        for c in ("citations", "cannabis_type", "outcome_domain", "duration",
                  "treatment_duration", "admin_frequency", "strain_normalized",
                  "population_age", "population_sex"):
            self.assertIn(c, hidden, c)
        for c in ("year", "study_type", "exposure_method", "publication_type",
                  "links", "dose_mg", "strain_reported"):
            self.assertIn(c, visible, c)

    def test_every_table_column_has_source_fields(self):
        m = re.search(r"const DEFAULT_VISIBLE_COLUMNS = \{(.*?)\};", _html(), re.S)
        cols = set(re.findall(r"(\w+):", m.group(1)))
        self.assertEqual(cols, set(_js_column_fields()))

    def test_column_relevance_and_chooser_gate_on_guest(self):
        self.assertIn("if (!isGuestColumnAllowed(col)) return false;", _fn_body("isColumnRelevant"))
        self.assertIn("isGuestColumnAllowed(col)", _fn_body("applyColumnEditorProfile"))
        self.assertIn("applyGuestDrawerGating();", _html())


class TestGuestGuardsInTemplate(unittest.TestCase):
    def test_analyze_prompts_login_for_guests(self):
        for fn in ("startAnalyzeFilteredJob", "analyzeFilteredSubset"):
            body = _fn_body(fn)
            self.assertRegex(body, r"if \(!IS_LOGGED_IN\) \{\s*showAuthModal\(\);\s*return;", fn)
        body = _fn_body("startAnalyzeFilteredJob")
        self.assertIn("status === 401 && data && data.login_required", body + _html())

    def test_my_analyses_guest_prompt_and_error_handling(self):
        body = _fn_body("loadAnalysesList")
        self.assertIn("showGuestAnalysesSignInPrompt()", body)
        self.assertIn("if (!res.ok)", body)
        self.assertIn('guest-analysis-preview-active', _fn_body("showGuestAnalysesSignInPrompt"))

    def test_scheduler_status_skipped_for_guests(self):
        self.assertRegex(_html(), r"if \(IS_LOGGED_IN\) \{\s*updateSchedulerStatus\(\);\s*setInterval\(updateSchedulerStatus, 60000\);")

    def test_search_429_shows_message(self):
        s = _html()
        self.assertIn("res.status === 429", s)
        self.assertIn("Sign in for higher limits.", s)

    def test_auth_modal_focus_and_escape(self):
        self.assertIn(".focus()", _fn_body("showAuthModal"))
        self.assertIn('e.key === "Escape"', _html())
        self.assertIn('id="auth-required-modal" class="modal-overlay" role="dialog" aria-modal="true"', _html())


def _schema_columns():
    sql = open(os.path.join(ROOT, "schema.sql")).read()
    m = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?papers\s*\((.*?)\n\);", sql, re.S)
    out = []
    for line in m.group(1).splitlines():
        mm = re.match(r"\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Z]+)", line)
        if mm and mm.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
            out.append((mm.group(1), mm.group(2)))
    return out


def _synthetic_row(i):
    row = {}
    for col, typ in _schema_columns():
        if col == "id":
            continue
        if typ in ("INTEGER", "REAL", "BOOLEAN", "NUMERIC", "FLOAT"):
            row[col] = i
        else:
            row[col] = f"FAKE-{col}-{i}"
    row.update({
        "pmid": str(99000000 + i), "doi": f"10.99999/fake.{i:04d}",
        "semantic_scholar_id": f"fake-s2-{i:08d}", "title": f"Synthetic test paper {i}",
        "authors": json.dumps(["Fake Author"]), "year": 2020, "date_harvested": None,
        "study_type": json.dumps(["Clinical"]), "exposure_method": json.dumps(["oral"]),
        "cannabis_type": json.dumps(["FAKE"]), "outcome_domain": json.dumps(["FAKE"]),
        "expert_locked_fields": json.dumps(["title"]), "publication_date": "2020-01-01",
        "classification_timestamp": "2020-01-01T00:00:00",
    })
    return row


class TestSeededGuestJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("DATABASE_URL", None)
        import app as app_module
        import db_manager
        cls.app_module = app_module
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "guest_ui.db")
        real = db_manager.DatabaseManager
        db = real(path)
        cls.ids = [db.insert_paper(_synthetic_row(i)) for i in (1, 2, 3)]
        cls.factory = lambda *a, **k: real(path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.app_module.app.config["TESTING"] = True
        self.app_module.limiter.reset()
        self.client = self.app_module.app.test_client()
        self.allowed = set(self.app_module.GUEST_PAPER_FIELDS)

    def _get(self, path):
        with patch.object(self.app_module, "DatabaseManager", side_effect=self.factory):
            r = self.client.get(path)
        return r.status_code, r.get_json(silent=True)

    def test_search_keys_subset(self):
        code, data = self._get("/api/search")
        self.assertEqual(code, 200)
        self.assertTrue(data["papers"], "seeded rows should be returned")
        for p in data["papers"]:
            self.assertLessEqual(set(p), self.allowed)
        self.assertLessEqual(set(data), set(self.app_module.GUEST_SEARCH_ENVELOPE))

    def test_paper_keys_subset(self):
        code, data = self._get(f"/api/papers/{self.ids[0]}")
        self.assertEqual(code, 200)
        self.assertEqual(set(data), {"paper"})
        self.assertLessEqual(set(data["paper"]), self.allowed)
        self.assertIn("title", data["paper"])

    def test_search_simple_keys_subset(self):
        code, data = self._get("/api/papers/search-simple?q=Synthetic")
        self.assertEqual(code, 200)
        for p in data or []:
            self.assertLessEqual(set(p), self.allowed)

    def test_link_routes_keys_subset(self):
        allowed = self.allowed | set(self.app_module.GUEST_LINK_KEYS)
        for suffix in ("references", "cited-by"):
            code, data = self._get(f"/api/papers/{self.ids[0]}/{suffix}")
            self.assertEqual(code, 200, suffix)
            for e in data or []:
                self.assertLessEqual(set(e), allowed, suffix)

    def test_aggregate_routes_keys_subset(self):
        m = self.app_module
        checks = {
            "/api/graph/stats": m.GUEST_GRAPH_STATS_KEYS,
            "/api/search/section-stats": m.GUEST_SECTION_STATS_KEYS,
            "/api/tab-counts": {"counts"},
            "/api/dashboard-config": m.GUEST_DASHBOARD_CONFIG_KEYS,
        }
        for path, keys in checks.items():
            code, data = self._get(path)
            if code == 403:  # graph routes are @mvp_gate-locked for everyone
                self.assertEqual(set(data), {"error"}, path)
                continue
            self.assertEqual(code, 200, path)
            self.assertIsInstance(data, dict, path)
            self.assertLessEqual(set(data), set(keys), path)

    def test_graph_network_keys_subset(self):
        code, data = self._get("/api/graph/network")
        if code == 403:  # @mvp_gate-locked for everyone
            self.assertEqual(set(data), {"error"})
            return
        self.assertEqual(code, 200)
        for n in data.get("nodes", []):
            self.assertLessEqual(set(n), set(self.app_module.GUEST_GRAPH_NODE_FIELDS))


if __name__ == "__main__":
    unittest.main()
