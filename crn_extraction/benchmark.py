"""Main-text completeness benchmark harness (train+dev papers only, never test).

Reads PMIDs ONLY from the allowlisted Evaluation files /workspace/crn-eval/dev_pmids.txt and
/workspace/crn-eval/legacy32_pmids.txt; any other path is refused. --out may not be inside the repo. Supplements are never fetched:
items whose detail the main text points to a supplement get could_not_check source_not_fetched.
Output: per-item status counts + reason counts. No tier is computed or scored here.

Usage:
  python -m crn_extraction.benchmark --fixtures DIR        # fake fixtures, no network
  python -m crn_extraction.benchmark --fetch               # real run, needs dev_pmids.txt
"""
import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys

from crn_extraction import extractor, sources

DEV_PMIDS = pathlib.Path("/workspace/crn-eval/dev_pmids.txt")
LEGACY32_PMIDS = pathlib.Path("/workspace/crn-eval/legacy32_pmids.txt")
# Only these PMID lists are ever read (Evaluation-owned, PMID-only files). Everything else is refused.
ALLOWED_PMID_FILES = (DEV_PMIDS, LEGACY32_PMIDS)
FIXTURES_ROOT = extractor.ROOT / "tests" / "fixtures"


class Refused(RuntimeError):
    pass


def _resolved(path):
    return pathlib.Path(os.path.abspath(os.path.expanduser(str(path))))


def guard_pmids_path(path):
    p = pathlib.Path(os.path.realpath(_resolved(path)))  # resolves symlinks and '..'
    if p not in {pathlib.Path(os.path.realpath(_resolved(a))) for a in ALLOWED_PMID_FILES}:
        raise Refused(f"refused PMID input (allowlist: {', '.join(map(str, ALLOWED_PMID_FILES))}): {path}")
    return p


def guard_out_path(path):
    p = pathlib.Path(os.path.realpath(_resolved(path)))
    root = pathlib.Path(os.path.realpath(extractor.ROOT))
    if p == root or root in p.parents:
        raise Refused(f"refused --out inside the repo: {path}")
    return p


def read_pmids(path=DEV_PMIDS):
    path = guard_pmids_path(path)
    if not path.exists():
        return None
    out = []
    for line in path.read_text().splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            if not t.isdigit():
                raise Refused(f"non-PMID line in {path.name}: only one PMID per line is accepted")
            out.append(t)
    return out


def run(papers, max_papers=None, cfg=None):
    """papers: list of paper dicts (see extractor.extract). max_papers: budget cap; papers past it
    get could_not_check budget_cap and are listed in excluded with reason budget_cap."""
    cfg = cfg or extractor.load_config()
    records, excluded, q7, invalid = [], [], [], {}
    item_status = collections.defaultdict(collections.Counter)
    reasons = collections.Counter()
    nrr = collections.Counter()
    by_type = collections.Counter()
    for i, paper in enumerate(papers):
        paper = dict(paper)
        if max_papers is not None and i >= max_papers:
            paper["status"] = "budget_cap"
        res = extractor.extract(paper, cfg)
        if res["excluded"]:
            q7.append({"paper_pmid": paper["pmid"], "reason": res["excluded"]})
            continue
        rec = res["record"]
        errs = extractor.validate(rec)
        if errs:
            invalid[paper["pmid"]] = errs
        if paper.get("status", "ok") in ("budget_cap", "source_not_available"):
            excluded.append({"paper_pmid": paper["pmid"], "reason": paper["status"].replace("source_not_available", "source_unavailable")})
        by_type[res["study_type"] or "unknown"] += 1
        records.append({"record_id": rec["record_id"], "paper_pmid": paper["pmid"]})
        for key, r in rec["reporting"].items():
            if not isinstance(r, dict):
                continue
            item_status[key][r["status"]] += 1
            if r.get("check_failure_reason"):
                reasons[r["check_failure_reason"]] += 1
            if r.get("not_reported_reason"):
                nrr[r["not_reported_reason"]] += 1
        yield_rec = rec
        records[-1]["_record"] = yield_rec
    return {
        "papers": len(papers), "records": len(records), "study_types": dict(by_type),
        "item_status": {k: dict(v) for k, v in sorted(item_status.items())},
        "check_failure_reason": dict(reasons), "not_reported_reason": dict(nrr),
        "excluded": excluded, "excluded_not_original_research": q7, "schema_invalid": invalid,
        "_records": records,
    }


def manifest(summary, run_id, split="dev"):
    """Contract extraction_run_manifest (mode scored; model_ids {} because no model is used)."""
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=extractor.ROOT, text=True).strip()
    except Exception as e:  # noqa: BLE001
        print(f"git rev-parse failed ({type(e).__name__})", file=sys.stderr)
        sha = "unknown"
    return {"run_id": run_id, "mode": "scored", "split": split, "contract_version": extractor.CONTRACT_VERSION,
            "git_ref": sha, "config_hash": extractor.config_hash(), "prompt_hashes": {}, "model_ids": {},
            "records": [{"record_id": r["record_id"], "paper_pmid": r["paper_pmid"]} for r in summary["_records"]],
            "excluded": summary["excluded"]}


def load_fixtures(d):
    d = pathlib.Path(os.path.realpath(_resolved(d)))
    if not (d == FIXTURES_ROOT or FIXTURES_ROOT in d.parents):
        raise Refused(f"fixtures must live under {FIXTURES_ROOT}: {d}")
    return [json.loads(p.read_text()) for p in sorted(pathlib.Path(d).glob("*.json"))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--pmids", default=str(DEV_PMIDS))
    ap.add_argument("--max-papers", type=int, default=None)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    out_path = guard_out_path(a.out) if a.out else None
    if a.fixtures:
        papers = load_fixtures(a.fixtures)
        run_id = "fixtures"
    else:
        pmids = read_pmids(a.pmids)
        if pmids is None:
            print(json.dumps({"status": "pending", "reason": f"{a.pmids} does not exist; real benchmark not run"}))
            return 2
        if not a.fetch:
            print("pass --fetch to fetch PMC OA main text for", len(pmids), "PMIDs")
            return 2
        got = sources.fetch_main_text(pmids)
        papers = [dict(paper_id=int(p), pmid=p, **got[p]) for p in pmids]
        run_id = "dev-main-text"
    s = run(papers, max_papers=a.max_papers)
    out = {k: v for k, v in s.items() if k != "_records"}
    out["manifest"] = manifest(s, run_id)
    text = json.dumps(out, indent=2)
    if out_path:
        out_path.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
