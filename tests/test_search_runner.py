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

    def test_csv_input(self):
        import search_runner
        with tempfile.TemporaryDirectory() as d:
            q = os.path.join(d, "q.csv")
            with open(q, "w") as fh:
                fh.write("query_id,query\na,cbd\n")
            out = os.path.join(d, "o.jsonl")
            search_runner.run(q, out, k=5, search=lambda t, k: {"papers": [{"pmid": p} for p in FAKE_DB[t]]})
            self.assertEqual(json.loads(open(out).read()), {"query_id": "a", "pmids": FAKE_DB["cbd"]})


if __name__ == "__main__":
    unittest.main()
