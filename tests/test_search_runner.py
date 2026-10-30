"""scripts/search_runner.py: label-blind runner output format (fake DB, fake queries)."""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
SCRIPT = os.path.join(ROOT, "scripts", "search_runner.py")

# Synthetic rows: fake PMIDs 99000001+.
FAKE_DB = {
    "cbd": ["99000003", "99000001", "99000002"],
    "thc": ["99000005"],
}


class TestSearchRunner(unittest.TestCase):
    def test_no_grades_input(self):
        import search_runner
        src = open(SCRIPT, encoding="utf-8").read()
        opts = [a.dest for a in search_runner.build_parser()._actions]
        for bad in ("grade", "label", "qrel", "relevance", "judg"):
            self.assertFalse(any(bad in o for o in opts), opts)
            self.assertIsNone(re.search(r"add_argument\([^)]*%s" % bad, src, re.I))
        self.assertEqual(search_runner.run.__code__.co_varnames[:6],
                         ("queries_path", "output_path", "k", "base_url", "meta_path", "search"))

    def test_output_format_via_test_client(self):
        import app as m
        import search_runner
        m.limiter.reset()
        db = MagicMock()
        db.get_metadata.return_value = None

        def sp(f, include_total=False):
            rows = [{"id": i, "pmid": p, "title": "Fake"} for i, p in
                    enumerate(FAKE_DB.get((f.get("query") or "").lower(), []))][: f.get("limit")]
            return (rows, len(rows)) if include_total else rows
        db.search_papers.side_effect = sp
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "q.jsonl")
            with open(q, "w") as fh:
                fh.write('{"query_id": "q1", "query": "cbd"}\n{"query_id": 2, "query": "thc"}\n'
                         '{"query_id": "q3", "query": "none"}\n')
            out, meta = os.path.join(d, "o.jsonl"), os.path.join(d, "meta.json")
            with patch.object(m, "DatabaseManager", return_value=db):
                search_runner.run(q, out, k=2, meta_path=meta)
            lines = [json.loads(l) for l in open(out)]
            self.assertEqual(lines, [
                {"query_id": "q1", "pmids": ["99000003", "99000001"]},
                {"query_id": "2", "pmids": ["99000005"]},
                {"query_id": "q3", "pmids": []},
            ])
            md = json.load(open(meta))
            self.assertEqual(md["k"], 2)
            self.assertIn("git_sha", md)
            self.assertIn("timestamp", md)
        m.limiter.reset()

    def test_id_field_filters_and_default_k(self):
        import app as m
        import search_runner
        m.limiter.reset()
        db = MagicMock()
        db.get_metadata.return_value = None
        seen = []

        def sp(f, include_total=False):
            seen.append(dict(f))
            rows = [{"id": i, "pmid": str(99000100 + i)} for i in range(30)][: f.get("limit")]
            return (rows, len(rows)) if include_total else rows
        db.search_papers.side_effect = sp
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "q.jsonl")
            with open(q, "w") as fh:
                fh.write(json.dumps({"id": "q16", "text": "cannabidiol", "filters": {"study_type": "RCT"}}) + "\n")
                fh.write(json.dumps({"id": "q20", "text": "cannabis cohort",
                                     "filters": {"study_type": "observational", "year_min": 2015, "year_max": 2026}}) + "\n")
                fh.write(json.dumps({"id": "q21", "text": "cannabidiol",
                                     "filters": {"exposure_method": "oral administration"}}) + "\n")
                fh.write(json.dumps({"id": "q01", "text": "cbd", "filters": None}) + "\n")
            out = os.path.join(d, "o.jsonl")
            with patch.object(m, "DatabaseManager", return_value=db):
                search_runner.main([q, "-o", out])
            lines = [json.loads(l) for l in open(out)]
        m.limiter.reset()
        self.assertEqual([l["query_id"] for l in lines], ["q16", "q20", "q21", "q01"])
        self.assertTrue(all(len(l["pmids"]) == 20 for l in lines))
        self.assertEqual(seen[0]["study_type"], "RCT")
        self.assertEqual((seen[1]["year_min"], seen[1]["year_max"]), (2015, 2026))
        self.assertEqual(seen[2]["exposure_method"], "oral administration")
        self.assertNotIn("study_type", seen[3])
        self.assertTrue(all(f["limit"] == 20 for f in seen))

    def test_unknown_filter_rejected(self):
        import search_runner
        with self.assertRaises(ValueError):
            search_runner.filters_to_params({"bogus": 1})

    def _write(self, d, rows):
        q = os.path.join(d, "q.jsonl")
        with open(q, "w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return q

    def test_bad_later_query_leaves_no_output_and_no_requests(self):
        import search_runner
        calls = []
        with tempfile.TemporaryDirectory() as d:
            q = self._write(d, [{"id": "q1", "text": "cbd", "filters": {"study_type": "RCT"}},
                                {"id": "q2", "text": "thc", "filters": {"bogus": "x"}}])
            out = os.path.join(d, "o.jsonl")
            with self.assertRaisesRegex(ValueError, "q2.*bogus"):
                search_runner.run(q, out, search=lambda t, k, f=None: calls.append(t) or {"papers": []})
            self.assertFalse(os.path.exists(out))
        self.assertEqual(calls, [])

    def test_non_dict_filters_clear_error(self):
        import search_runner
        for bad in (["study_type", "RCT"], "RCT", 5):
            with tempfile.TemporaryDirectory() as d:
                q = self._write(d, [{"id": "q9", "text": "cbd", "filters": bad}])
                with self.assertRaisesRegex(ValueError, "q9.*filters"):
                    search_runner.load_queries(q)

    def test_unknown_key_rejected_even_if_empty(self):
        import search_runner
        for val in (None, ""):
            with self.assertRaisesRegex(ValueError, "bogus"):
                search_runner.filters_to_params({"bogus": val})
        self.assertEqual(search_runner.filters_to_params({"study_type": None}), {})

    def _run_fail(self, search, k=20):
        import search_runner
        with tempfile.TemporaryDirectory() as d:
            q = self._write(d, [{"id": "q1", "text": "cbd"}, {"id": "q2", "text": "thc"}])
            out = os.path.join(d, "o.jsonl")
            with patch.object(search_runner, "make_test_client_search", return_value=search):
                rc = search_runner.main([q, "-o", out, "--k", str(k)])
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(out))
            self.assertEqual([f for f in os.listdir(d) if f != "q.jsonl"], [])

    def test_http_error_no_output_nonzero(self):
        from urllib.error import URLError

        def search(t, k, f=None):
            if t == "thc":
                raise URLError("timed out")
            return {"papers": [{"pmid": "99000001"}]}
        self._run_fail(search)

    def test_error_payload_fails(self):
        self._run_fail(lambda t, k, f=None: {"error": "boom", "papers": []})

    def test_missing_papers_fails(self):
        self._run_fail(lambda t, k, f=None: {"total_count": 0})

    def test_k_bounds(self):
        import search_runner
        self._run_fail(lambda t, k, f=None: {"papers": []}, k=0)
        self._run_fail(lambda t, k, f=None: {"papers": []}, k=-3)
        seen = []
        with tempfile.TemporaryDirectory() as d:
            q = self._write(d, [{"id": "q1", "text": "cbd"}])
            out, meta = os.path.join(d, "o.jsonl"), os.path.join(d, "m.json")
            search_runner.run(q, out, k=500, meta_path=meta,
                              search=lambda t, k, f=None: seen.append(k) or {"papers": []})
            self.assertEqual(seen, [100])
            self.assertEqual(json.load(open(meta))["k"], 100)

    def test_csv_input(self):
        import search_runner
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "q.csv")
            with open(q, "w") as fh:
                fh.write("query_id,query\na,cbd\n")
            out = os.path.join(d, "o.jsonl")
            search_runner.run(q, out, k=5, search=lambda t, k, f=None: {"papers": [{"pmid": p} for p in FAKE_DB[t]]})
            self.assertEqual(json.loads(open(out).read()), {"query_id": "a", "pmids": FAKE_DB["cbd"]})


if __name__ == "__main__":
    unittest.main()
