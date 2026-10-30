#!/usr/bin/env python3
"""Run a batch of queries against /api/search and write ranked PMIDs per query.

Input: a queries file, JSONL ({"id" or "query_id": ..., "text"/"query": ...,
"filters": {...} or null}) or CSV with columns id/query_id and query/text
(optional "filters" column holding a JSON object).
Filters are mapped to /api/search params (see FILTER_PARAM_MAP).
Output: JSONL, one line per query: {"query_id": ..., "pmids": [ranked PMIDs]}.
Optional --meta writes a run metadata JSON (git sha, timestamp, k, ...).

By design this script takes NO relevance grades/labels input; it only produces
rankings so they can be scored separately (blind to labels).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_KEYS = ("query", "text", "query_text")
ID_KEYS = ("query_id", "id")
# query-set filter key -> /api/search query param (app._build_dashboard_filters_from_request)
FILTER_PARAM_MAP = {
    "exposure_method": "method",
    "study_type": "study_type",
    "year_min": "year_min",
    "year_max": "year_max",
}


def load_queries(path):
    rows = []
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    else:
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    out = []
    for r in rows:
        text = next((r[k] for k in TEXT_KEYS if r.get(k) not in (None, "")), None)
        qid = next((r[k] for k in ID_KEYS if r.get(k) not in (None, "")), None)
        if qid is None or text is None:
            raise ValueError("query row needs query_id/id and query text: %r" % (r,))
        filters = r.get("filters")
        if isinstance(filters, str):
            filters = json.loads(filters) if filters.strip() else None
        out.append((str(qid), str(text), filters or {}))
    return out


def filters_to_params(filters):
    """Map a query-set filters dict to /api/search params. Unknown keys fail loudly."""
    params = {}
    for key, val in (filters or {}).items():
        if val is None or val == "":
            continue
        if key not in FILTER_PARAM_MAP:
            raise ValueError("unsupported filter %r (known: %s)" % (key, sorted(FILTER_PARAM_MAP)))
        params[FILTER_PARAM_MAP[key]] = val
    return params


def extract_pmids(payload, k):
    """/api/search returns {"papers": [{..., "pmid": ...}, ...], "total_count": ...}."""
    pmids = []
    for p in (payload or {}).get("papers") or []:
        pmid = p.get("pmid")
        if pmid not in (None, ""):
            pmids.append(str(pmid))
    return pmids[:k]


def _params(text, k, filters=None):
    params = filters_to_params(filters)
    params.update({"query": text, "limit": k, "page": 1, "skip_count": "1", "known_total": "0"})
    return params


def make_test_client_search():
    sys.path.insert(0, ROOT)
    import app as appmod
    appmod.app.config["TESTING"] = True
    client = appmod.app.test_client()

    def search(text, k, filters=None):
        r = client.get("/api/search?" + urlencode(_params(text, k, filters)))
        if r.status_code != 200:
            raise RuntimeError("/api/search %s: %s" % (r.status_code, r.get_data(as_text=True)[:200]))
        return r.get_json()
    return search


def make_http_search(base_url):
    def search(text, k, filters=None):
        url = base_url.rstrip("/") + "/api/search?" + urlencode(_params(text, k, filters))
        with urlopen(url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return search


def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


def run(queries_path, output_path, k=20, base_url=None, meta_path=None, search=None):
    queries = load_queries(queries_path)
    if search is None:
        search = make_http_search(base_url) if base_url else make_test_client_search()
    with open(output_path, "w", encoding="utf-8") as out:
        for qid, text, filters in queries:
            pmids = extract_pmids(search(text, k, filters), k)
            out.write(json.dumps({"query_id": qid, "pmids": pmids}) + "\n")
    if meta_path:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "git_sha": git_sha(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "k": k,
                "queries_file": os.path.basename(queries_path),
                "n_queries": len(queries),
                "target": base_url or "flask-test-client",
            }, f, indent=2)
    return len(queries)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("queries", help="queries file (.jsonl or .csv with query_id, query)")
    ap.add_argument("-o", "--output", required=True, help="output JSONL path")
    ap.add_argument("--k", type=int, default=20, help="top-k PMIDs per query (default 20, server caps at 100)")
    ap.add_argument("--base-url", help="local dev server URL; default uses the Flask test client")
    ap.add_argument("--meta", help="optional run metadata JSON path")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    n = run(a.queries, a.output, k=a.k, base_url=a.base_url, meta_path=a.meta)
    print("wrote %d queries to %s" % (n, a.output))


if __name__ == "__main__":
    main()
