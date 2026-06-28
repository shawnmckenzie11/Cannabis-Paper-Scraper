"""Similarity cohort validation: endpoint tree-path papers before vs after reingest."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import golden_dataset_paths
from db_manager import DatabaseManager
from local_sync import empty_baseline_snapshot, load_baseline_row
from patch_blast_radius import ROUTING_FIELDS, REPORT_ROOT
from reingest_heuristic_papers import _fetch_target_papers, norm, parse_json_field

DEFAULT_SQLITE_PATH = "cannabis_papers.db"


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _paper_dict_for_match(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a paper or baseline row for endpoint matching."""
    paper: Dict[str, Any] = {}
    for field in ROUTING_FIELDS:
        paper[field] = parse_json_field(row.get(field))
    return paper


def _routing_fields_differ(baseline: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """Return True when any routing field differs between baseline and current."""
    for field in ROUTING_FIELDS:
        if norm(baseline.get(field)) != norm(current.get(field)):
            return True
    return False


def validate_similarity_cohort(
    endpoint_id: str,
    *,
    sqlite_path: str = DEFAULT_SQLITE_PATH,
    scope_subnode: Optional[str] = None,
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """
    Compare endpoint cohort routing before (pull baseline) vs after (current SQLite row).

    Cohort pool = subnode papers that match the endpoint on baseline OR current classification.
    """
    endpoint = golden_dataset_paths.endpoint_by_id(endpoint_id)
    if endpoint is None:
        raise ValueError(f"Unknown endpoint_id: {endpoint_id}")

    subnode = scope_subnode or endpoint.scope_subnode
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    db = DatabaseManager(sqlite_path)
    papers = _fetch_target_papers(
        db,
        pass_mode="fast",
        only_heuristic=False,
        maude_and_heuristic=True,
        scope_subnode=subnode,
        limit=None,
    )

    cohort_ids: Set[int] = set()
    match_before = 0
    match_after = 0
    cohort_routing_changed = 0
    subnode_routing_changed = 0
    sample_changed: List[int] = []

    for paper in papers:
        paper_id = int(paper["id"])
        baseline = load_baseline_row(conn, paper_id) or empty_baseline_snapshot()
        baseline_paper = dict(paper)
        for field in ROUTING_FIELDS:
            if field in baseline:
                baseline_paper[field] = baseline.get(field)

        before_match = golden_dataset_paths.paper_matches_endpoint(
            _paper_dict_for_match(baseline_paper),
            endpoint,
        )
        after_match = golden_dataset_paths.paper_matches_endpoint(
            _paper_dict_for_match(paper),
            endpoint,
        )
        in_cohort = before_match or after_match
        if in_cohort:
            cohort_ids.add(paper_id)
        if before_match:
            match_before += 1
        if after_match:
            match_after += 1

        routing_changed = _routing_fields_differ(baseline_paper, paper)
        if routing_changed:
            subnode_routing_changed += 1
            if in_cohort:
                cohort_routing_changed += 1
                if len(sample_changed) < sample_limit:
                    sample_changed.append(paper_id)

    conn.close()
    return {
        "endpoint_id": endpoint_id,
        "scope_subnode": subnode,
        "cohort_pool_size": len(cohort_ids),
        "subnode_papers_scanned": len(papers),
        "cohort_routing_match_before": match_before,
        "cohort_routing_match_after": match_after,
        "cohort_papers_routing_changed": cohort_routing_changed,
        "subnode_papers_routing_changed": subnode_routing_changed,
        "cohort_routing_delta": match_after - match_before,
        "sample_changed_paper_ids": sample_changed,
        "generated_at": utc_now_iso(),
    }


def render_cohort_html(payload: Dict[str, Any]) -> str:
    """Format cohort validation HTML appendix."""
    samples = payload.get("sample_changed_paper_ids") or []
    sample_items = "".join(f"<li>{pid}</li>" for pid in samples) or "<li>—</li>"
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Cohort validation — {escape(payload.get('endpoint_id', ''))}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:720px}"
        "ul{padding-left:20px}</style></head><body>"
        f"<h1>Similarity cohort validation</h1>"
        f"<p><strong>Endpoint:</strong> {escape(str(payload.get('endpoint_id')))}<br>"
        f"<strong>Scope subnode:</strong> {escape(str(payload.get('scope_subnode')))}<br>"
        f"<strong>Subnode papers scanned:</strong> {payload.get('subnode_papers_scanned')}<br>"
        f"<strong>Cohort pool size:</strong> {payload.get('cohort_pool_size')}<br>"
        f"<strong>Routing match before:</strong> {payload.get('cohort_routing_match_before')}<br>"
        f"<strong>Routing match after:</strong> {payload.get('cohort_routing_match_after')}<br>"
        f"<strong>Cohort routing Δ:</strong> {payload.get('cohort_routing_delta')}<br>"
        f"<strong>Cohort papers routing changed:</strong> {payload.get('cohort_papers_routing_changed')}<br>"
        f"<strong>Subnode papers routing changed:</strong> {payload.get('subnode_papers_routing_changed')}"
        f"</p>"
        f"<h2>Sample changed paper IDs</h2><ul>{sample_items}</ul>"
        "</body></html>"
    )


def write_cohort_reports(
    payload: Dict[str, Any],
    *,
    loop_type: str,
    patch_id: str,
) -> Dict[str, str]:
    """Write cohort JSON and HTML under the patch report directory."""
    report_dir = REPORT_ROOT / loop_type / patch_id
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "cohort_validation.json"
    html_path = report_dir / "cohort_validation.html"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(render_cohort_html(payload), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


def run_and_write(
    endpoint_id: str,
    *,
    loop_type: str,
    patch_id: str,
    sqlite_path: str = DEFAULT_SQLITE_PATH,
    scope_subnode: Optional[str] = None,
) -> Dict[str, Any]:
    """Run cohort validation and write reports; return payload with report paths."""
    payload = validate_similarity_cohort(
        endpoint_id,
        sqlite_path=sqlite_path,
        scope_subnode=scope_subnode,
    )
    paths = write_cohort_reports(payload, loop_type=loop_type, patch_id=patch_id)
    payload["report_paths"] = paths
    return payload


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Similarity cohort validation for a golden endpoint.")
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--loop-type", default="golden_b")
    parser.add_argument("--patch-id", required=True)
    parser.add_argument("--sqlite-path", default=DEFAULT_SQLITE_PATH)
    parser.add_argument("--scope-subnode", default=None)
    return parser


def main() -> None:
    """CLI entrypoint."""
    args = build_parser().parse_args()
    payload = run_and_write(
        args.endpoint_id,
        loop_type=args.loop_type,
        patch_id=args.patch_id,
        sqlite_path=args.sqlite_path,
        scope_subnode=args.scope_subnode,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
