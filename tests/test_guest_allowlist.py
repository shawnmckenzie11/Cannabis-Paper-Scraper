"""Read-only guest view (seed proposal §6.5): allowlisted GETs only, no save/export, no gold/expert/split fields."""
import os
import re
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SENSITIVE = re.compile(
    r"gold|expert|rationale|(^|_)split($|_)|split_label|verdict|confidence|reviewer|reference_value", re.I)
EXTRA_KEYS = ("verdict", "llm_confidence", "reviewer_email", "reference_value")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


# Fixture rule: made-up papers only. Never real PMIDs/DOIs from the split manifest.
FAKE_PMID = "00000001"
FAKE_DOI = "10.0000/fake.00000001"


def _fake_paper():
    return {
        "id": 1, "pmid": FAKE_PMID, "doi": FAKE_DOI, "title": "Fake paper", "date_harvested": None,
        "is_golden": True, "gold_badge": "gold", "expert_reason": "x",
        "expert_locked_fields": ["a"], "rationale": "why", "llm_rationale": "y",
        "split": "test", "eval_split": "test", "split_label": "test",
        "verdict": "include", "llm_confidence": 0.9, "reviewer_email": "r@x",
        "reference_value": 3,
        "nested": {"golden_status": 1, "keep": 2, "final_verdict": "x", "reviewer": "r"},
    }


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_PUBLIC_FIELDS = {
    "id", "pmid", "doi", "semantic_scholar_id", "title", "authors", "journal",
    "year", "abstract", "full_text_link", "study_type", "publication_type",
    "exposure_method", "thc_pct", "cbd_pct", "dose_mg", "puff_count", "strain_reported",
    "thc_mg_ml", "thc_mg_g", "thc_mg_kg", "thc_uM", "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "cbd_uM",
}

# Snapshot of schema.sql papers columns. A new column fails test_schema_snapshot
# so someone must decide whether it is public before it ships.
SCHEMA_PAPER_COLUMNS_SNAPSHOT = [
    "id", "pmid", "doi", "semantic_scholar_id", "title", "authors", "journal", "year",
    "abstract", "full_text_link", "study_type", "publication_type", "exposure_method",
    "thc_pct", "cbd_pct", "dose_mg", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
    "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM", "strain_reported",
    "strain_normalized", "duration_days", "inhaled_exposure_duration",
    "administration_frequency", "treatment_duration", "repeat_exposure_count",
    "exposure_regimen_bin", "sample_size", "outcome_domain", "open_access",
    "citation_count", "date_harvested", "publication_date", "summary",
    "expert_locked_fields", "classification_confidence", "classification_timestamp",
    "classifier_version", "ingestion_status", "species", "population_age",
    "population_sex", "inclusion_criteria", "exclusion_criteria",
]


def _schema_paper_columns():
    sql = open(os.path.join(ROOT, "schema.sql")).read()
    m = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?papers\s*\((.*?)\n\);", sql, re.S)
    cols = []
    for line in m.group(1).splitlines():
        mm = re.match(r"\s+([A-Za-z_][A-Za-z0-9_]*)\s+[A-Z]", line)
        if mm and mm.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
            cols.append(mm.group(1))
    return cols


def _full_fake_paper():
    p = {c: f"v_{c}" for c in _schema_paper_columns()}
    p["id"] = 1
    p["pmid"] = FAKE_PMID  # made-up identifiers only (Evaluation fixture rule)
    p["doi"] = FAKE_DOI
    p["semantic_scholar_id"] = "fake-s2-00000001"
    p.update({"verdict": "x", "confidence": 0.5, "reviewer": "r", "reference_value": 1,
              "split": "test", "gold": True, "strain_normalized": "s", "new_future_col": 1,
              "date_harvested": None})
    return p


class TestGuestAllowlist(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config["TESTING"] = True
        app_module.limiter.reset()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.app_module.limiter.reset()

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
        for k in EXTRA_KEYS:
            self.assertIn(k, data["paper"], k)
        self.assertIn("final_verdict", data["paper"]["nested"])

    def test_guest_extra_keys_stripped(self):
        with patch.object(self.app_module, "DatabaseManager", return_value=self._fake_db()):
            data = self.client.get("/api/papers/1").get_json()
        for k in EXTRA_KEYS:
            self.assertNotIn(k, data["paper"], k)
        self.assertNotIn("nested", data["paper"])

    def test_schema_snapshot(self):
        self.assertEqual(_schema_paper_columns(), SCHEMA_PAPER_COLUMNS_SNAPSHOT)

    def test_allowlist_constant_matches_methods_list(self):
        self.assertEqual(set(self.app_module.GUEST_PAPER_FIELDS), EXPECTED_PUBLIC_FIELDS)
        self.assertTrue(EXPECTED_PUBLIC_FIELDS <= set(_schema_paper_columns()))

    def _db_full(self):
        db = MagicMock()
        db.search_papers.side_effect = lambda f, include_total=False: (
            ([_full_fake_paper()], 1) if include_total else [_full_fake_paper()])
        db.get_paper.return_value = _full_fake_paper()
        db.get_metadata.return_value = None
        return db

    def test_guest_paper_keys_exactly_allowlist(self):
        with patch.object(self.app_module, "DatabaseManager", return_value=self._db_full()):
            paper = self.client.get("/api/papers/1").get_json()["paper"]
            self.assertEqual(set(paper), EXPECTED_PUBLIC_FIELDS)
            data = self.client.get("/api/search").get_json()
            self.assertEqual(set(data["papers"][0]), EXPECTED_PUBLIC_FIELDS)
            self.assertEqual(set(data), {"papers", "total_count", "page", "limit",
                                         "search_ms", "count_ms", "skipped_count"})

    def test_signed_in_sees_all_fields(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
        with patch.object(self.app_module, "DatabaseManager", return_value=self._db_full()):
            paper = self.client.get("/api/papers/1").get_json()["paper"]
        self.assertEqual(set(paper), set(_full_fake_paper()))

    def test_graph_network_nodes_filtered(self):
        # /api/graph/network is MVP-gated (403) in tests, so check the shaper directly.
        raw = {"nodes": [dict(_full_fake_paper(), degree=3)],
               "edges": [{"source": 1, "target": 2, "relationship": "cites", "confidence": 0.4}],
               "debug": "x"}
        data = self.app_module._guest_shape("api_graph_network", raw)
        self.assertEqual(set(data), {"nodes", "edges"})
        self.assertEqual(set(data["nodes"][0]), EXPECTED_PUBLIC_FIELDS | {"degree"})
        self.assertEqual(set(data["edges"][0]), {"source", "target", "relationship"})
        simple = self.app_module._guest_shape("api_papers_search_simple", [_full_fake_paper()])
        self.assertEqual(set(simple[0]), EXPECTED_PUBLIC_FIELDS)

    def test_guest_read_rate_limit_60_per_minute(self):
        with patch.dict(os.environ, {"RATE_LIMIT_GUEST_READ": "60 per minute"}), \
                patch.object(self.app_module, "DatabaseManager", return_value=self._fake_db()):
            codes = [self.client.get("/api/papers/1").status_code for _ in range(61)]
            self.assertEqual(codes[:60], [200] * 60)
            self.assertEqual(codes[60], 429)
            with self.client.session_transaction() as s:
                s["logged_in"] = True
            codes = [self.client.get("/api/papers/1").status_code for _ in range(70)]
            self.assertEqual(set(codes), {200})


if __name__ == "__main__":
    unittest.main()


EXTRAS = {"split_counts": {"test": 1}, "gold_count": 2, "verdict_breakdown": {"x": 1},
          "reviewer_breakdown": {"r": 1}, "new_future_key": 3}
V0_LINK_PAPER = {"title", "pmid", "doi", "year", "study_type", "exposure_method"}
LINK_KEYS = {"relationship", "paper_id", "external_id", "is_internal"}
GRAPH_STATS_KEYS = {"total_edges", "internal_edges", "external_edges",
                    "papers_with_references", "papers_with_citations", "edge_types"}
SECTION_KEYS = {"total_count", "methods_count", "results_count", "methods_pct",
                "results_pct", "sampled", "sample_limit", "sample_size"}
DASH_KEYS = {"default_tab", "tabs", "filter_profiles", "global_filters", "global_filter_params",
             "filter_section_registry", "schema_tier_51_fields", "analyze_subset_tabs",
             "taxonomy", "rules_config_version"}
TAB_KEYS = {"all_original", "preclinical", "clinical", "review", "unclassified"}


def _link_entry():
    e = {k: 1 for k in LINK_KEYS | V0_LINK_PAPER}
    e.update({"pmid": "00000002", "doi": "10.0000/fake.00000002", "title": "Fake linked paper"})
    e.update({"confidence": 0.9, "source": "s2", "cannabis_type": "x",
              "strain_normalized": "s", "new_future_key": 1, "gold": True})
    return e


class TestGuestRouteKeySets(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        app_module.app.config["TESTING"] = True
        app_module.limiter.reset()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.m.limiter.reset()

    def _cg(self):
        cg = MagicMock()
        cg.return_value.get_references.return_value = [_link_entry()]
        cg.return_value.get_cited_by.return_value = [_link_entry()]
        return cg

    def test_link_entries_only_v0_paper_fields(self):
        with patch.object(self.m, "CitationGraph", self._cg()), patch.object(self.m, "DatabaseManager"):
            for p in ("/api/papers/1/references", "/api/papers/1/cited-by"):
                r = self.client.get(p)
                self.assertEqual(r.status_code, 200, p)
                entry = r.get_json()[0]
                self.assertEqual(set(entry), LINK_KEYS | V0_LINK_PAPER, p)
                self.assertTrue((set(entry) - LINK_KEYS) <= set(self.m.GUEST_PAPER_FIELDS))

    def test_graph_stats_exact_keys_no_breakdowns(self):
        raw = {k: 1 for k in GRAPH_STATS_KEYS}
        raw["edge_types"] = {"cites": 3, "cited_by": 2, "gold": 1, "split_test": 1, "verdict_x": 1}
        raw.update(EXTRAS)
        out = self.m._guest_shape("api_graph_stats", raw)
        self.assertEqual(set(out), GRAPH_STATS_KEYS)
        self.assertEqual(out["edge_types"], {"cites": 3, "cited_by": 2})

    def test_section_stats_exact_keys(self):
        raw = dict({k: 1 for k in SECTION_KEYS}, **EXTRAS)
        db = MagicMock()
        db.search_papers_minimal_for_section_stats.return_value = []
        with patch.object(self.m, "DatabaseManager", return_value=db), \
                patch("section_stats.compute_section_stats", return_value=dict(raw)):
            r = self.client.get("/api/search/section-stats")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.get_json()), SECTION_KEYS)

    def test_tab_counts_exact_keys(self):
        db = MagicMock()
        db.get_tab_counts.return_value = dict({k: 1 for k in TAB_KEYS}, **EXTRAS)
        with patch.object(self.m, "DatabaseManager", return_value=db):
            data = self.client.get("/api/tab-counts").get_json()
            self.assertEqual(set(data), {"counts"})
            self.assertEqual(set(data["counts"]), TAB_KEYS)
            with self.client.session_transaction() as s:
                s["logged_in"] = True
            data = self.client.get("/api/tab-counts").get_json()
        self.assertTrue(set(EXTRAS) <= set(data["counts"]))

    def test_dashboard_config_exact_keys(self):
        raw = dict({k: [] for k in DASH_KEYS}, **EXTRAS)
        raw["taxonomy"] = {"species": ["rat"], "gold_labels": ["x"], "new_future_key": 1}
        with patch.object(self.m, "build_dashboard_ui_config", return_value=raw):
            data = self.client.get("/api/dashboard-config").get_json()
        self.assertEqual(set(data), DASH_KEYS)
        self.assertEqual(data["taxonomy"], {"species": ["rat"]})

    def test_real_dashboard_config_keys_match(self):
        from dashboard_ui_config import build_dashboard_ui_config
        self.assertEqual(set(build_dashboard_ui_config()), DASH_KEYS)

    def test_signed_in_sees_link_extras(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
        with patch.object(self.m, "CitationGraph", self._cg()), patch.object(self.m, "DatabaseManager"):
            entry = self.client.get("/api/papers/1/references").get_json()[0]
        self.assertEqual(entry, _link_entry())
