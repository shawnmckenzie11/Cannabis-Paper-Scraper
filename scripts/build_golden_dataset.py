#!/usr/bin/env python3
"""Build a golden dataset of papers covering every decision-tree path endpoint."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import golden_candidate_scoring
import golden_dataset_paths
import paper_text_cache
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("scratch/golden_dataset/tree_path_golden.json")
DEFAULT_REPORT = Path("scratch/golden_dataset/tree_path_golden_report.json")
TOP_N = 10

# Columns needed for characteristic scoring beyond TABLE_LIST_COLUMNS.
_EXTRA_COLUMNS = (
    "papers.abstract",
    "papers.summary",
    "papers.inclusion_criteria",
    "papers.exclusion_criteria",
)

_SQL_HAS_PDF_LINK = (
    "(papers.full_text_link IS NOT NULL AND TRIM(papers.full_text_link) != '' "
    "AND LOWER(papers.full_text_link) LIKE '%.pdf')"
)


def _parse_paper_rows(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Normalizes DB rows into paper dicts with parsed JSON list fields."""
    papers: List[Dict[str, Any]] = []
    for row in rows:
        res = dict(row)
        for json_field in [
            "authors",
            "outcome_domain",
            "study_type",
            "exposure_method",
            "cannabis_type",
            "expert_locked_fields",
        ]:
            if res.get(json_field):
                try:
                    val = str(res[json_field]).strip()
                    if val.startswith("[") and val.endswith("]"):
                        res[json_field] = json.loads(res[json_field])
                except Exception:
                    pass
        papers.append(res)
    return papers


def _load_papers(db: DatabaseManager, where_clause: str) -> List[Dict[str, Any]]:
    """Loads papers matching a SQL WHERE fragment."""
    from db_manager import TABLE_LIST_COLUMNS

    conn = db.get_connection()
    cursor = conn.cursor()
    list_cols = list(TABLE_LIST_COLUMNS) + list(_EXTRA_COLUMNS)
    sql = f"SELECT {', '.join(list_cols)} FROM papers WHERE {where_clause}"
    cursor.execute(sql)
    rows = cursor.fetchall()
    conn.close()
    return _parse_paper_rows(rows)


def _load_pdf_papers(db: DatabaseManager) -> List[Dict[str, Any]]:
    """Loads searchable PDF papers, excluding review and tangential literature."""
    papers = _load_papers(db, _SQL_HAS_PDF_LINK)
    return [
        paper
        for paper in papers
        if golden_candidate_scoring.is_searchable_golden_candidate(paper)
    ]



def _is_pdf_paper(paper: Dict[str, Any]) -> bool:
    """Returns True when full_text_link points to a PDF."""
    link = str(paper.get("full_text_link") or "").strip().lower()
    return link.endswith(".pdf")


def _resolve_paper_text(paper: Dict[str, Any], cache_dir: Optional[Path]) -> str:
    """Returns cached PDF text, or title + abstract as fallback."""
    paper_id = paper.get("id")
    if paper_id is not None:
        entry = paper_text_cache.read_cached_entry(int(paper_id), cache_dir)
        if entry and entry.get("text"):
            return str(entry["text"])
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    return f"{title}\n\n{abstract}".strip()



def _rank_candidates(
    candidates: Sequence[Tuple[int, Dict[str, Any]]],
) -> List[Tuple[int, Dict[str, Any]]]:
    """Sorts scored candidates by characteristic count, confidence, and citations."""
    return sorted(
        candidates,
        key=lambda item: (
            -item[0],
            -(item[1].get("classification_confidence") or 0),
            -(item[1].get("citation_count") or 0),
            str(item[1].get("title") or ""),
        ),
    )


def _format_selected_paper(
    paper: Dict[str, Any],
    char_count: int,
    endpoint: golden_dataset_paths.TreePathEndpoint,
    selection_tier: str,
    cache_dir: Optional[Path],
) -> Dict[str, Any]:
    """Builds one export row for a selected golden-dataset paper."""
    scored_fields = golden_candidate_scoring.scored_fields_for_endpoint(endpoint)
    populated = golden_candidate_scoring.populated_scored_fields(paper, endpoint)
    gate_status = golden_candidate_scoring.gate_status_for_export(paper, endpoint)
    return {
        "paper_id": paper.get("id"),
        "pmid": paper.get("pmid"),
        "doi": paper.get("doi"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "full_text_link": paper.get("full_text_link"),
        "has_pdf_link": _is_pdf_paper(paper),
        "classifier_version": paper.get("classifier_version"),
        "selection_tier": selection_tier,
        "characteristics_identified_count": char_count,
        "characteristics_in_scope": len(scored_fields),
        "golden_gates_met": gate_status.get("golden_gates_met"),
        "gate_status": gate_status,
        "characteristics_identified": populated,
        "ground_truth": dict(populated),
        "text": _resolve_paper_text(paper, cache_dir),
    }


def _select_from_pool(
    pool: Sequence[Dict[str, Any]],
    endpoint: golden_dataset_paths.TreePathEndpoint,
    match_fn: Callable[[Dict[str, Any], golden_dataset_paths.TreePathEndpoint], bool],
    selection_tier: str,
    top_n: int,
    selected_ids: Set[int],
    selected_keys: Set[str],
    cache_dir: Optional[Path],
) -> List[Dict[str, Any]]:
    """Adds up to top_n papers from pool using match_fn, skipping already-selected ids."""
    if len(selected_ids) >= top_n:
        return []

    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for paper in pool:
        paper_id = paper.get("id")
        if paper_id is None or paper_id in selected_ids:
            continue
        canonical_key = golden_dataset_paths.canonical_bibliographic_key(paper)
        if canonical_key in selected_keys:
            continue
        if golden_dataset_paths.is_review_paper(paper):
            continue
        if not match_fn(paper, endpoint):
            continue
        if not golden_candidate_scoring.golden_gates_pass(paper, endpoint):
            continue
        char_count = golden_candidate_scoring.characteristic_count(paper, endpoint)
        candidates.append((char_count, paper))

    deduped: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for char_count, paper in candidates:
        key = golden_dataset_paths.canonical_bibliographic_key(paper)
        existing = deduped.get(key)
        if existing is None or golden_dataset_paths.prefer_golden_candidate(
            paper, existing[1]
        ):
            deduped[key] = (char_count, paper)
    candidates = list(deduped.values())

    added: List[Dict[str, Any]] = []
    for char_count, paper in _rank_candidates(candidates):
        if len(selected_ids) >= top_n:
            break
        paper_id = int(paper["id"])
        selected_ids.add(paper_id)
        selected_keys.add(golden_dataset_paths.canonical_bibliographic_key(paper))
        added.append(
            _format_selected_paper(paper, char_count, endpoint, selection_tier, cache_dir)
        )
    return added


def _select_papers_for_endpoint(
    pdf_papers: Sequence[Dict[str, Any]],
    endpoint: golden_dataset_paths.TreePathEndpoint,
    top_n: int,
    cache_dir: Optional[Path],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Selects top-N PDF papers using tiered matching:
    1) PDF + classification fields
    2) PDF + title/abstract keyword cues
    """
    selected_ids: Set[int] = set()
    selected_keys: Set[str] = set()
    tier_counts = {
        "pdf_classification": 0,
        "pdf_keywords": 0,
    }

    tiers: List[Tuple[str, Sequence[Dict[str, Any]], Callable[..., bool]]] = [
        ("pdf_classification", pdf_papers, golden_dataset_paths.paper_matches_endpoint),
        ("pdf_keywords", pdf_papers, golden_dataset_paths.paper_matches_endpoint_keywords),
    ]

    selected: List[Dict[str, Any]] = []
    for tier_name, pool, match_fn in tiers:
        if len(selected_ids) >= top_n:
            break
        batch = _select_from_pool(
            pool,
            endpoint,
            match_fn,
            tier_name,
            top_n,
            selected_ids,
            selected_keys,
            cache_dir,
        )
        tier_counts[tier_name] = len(batch)
        selected.extend(batch)

    return selected, tier_counts


def _count_eligible_pool(
    pool: Sequence[Dict[str, Any]],
    endpoint: golden_dataset_paths.TreePathEndpoint,
    match_fn: Callable[[Dict[str, Any], golden_dataset_paths.TreePathEndpoint], bool],
) -> int:
    """Counts PDF papers that match an endpoint and pass selection gates."""
    return sum(
        1
        for paper in pool
        if golden_candidate_scoring.is_searchable_golden_candidate(paper)
        and match_fn(paper, endpoint)
        and golden_candidate_scoring.golden_gates_pass(paper, endpoint)
    )


def build_golden_dataset(
    top_n: int = TOP_N,
    cache_dir: Optional[Path] = None,
    db: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """Builds the full tree-path golden dataset payload (original research only)."""
    db = db or DatabaseManager()
    pdf_papers = _load_pdf_papers(db)
    endpoints = golden_dataset_paths.non_review_tree_path_endpoints()

    endpoint_results: List[Dict[str, Any]] = []
    total_selected = 0
    endpoints_with_papers = 0
    endpoints_empty = 0
    endpoints_full = 0

    for endpoint in endpoints:
        selected, tier_counts = _select_papers_for_endpoint(
            pdf_papers,
            endpoint,
            top_n,
            cache_dir,
        )
        pool_pdf_classification = _count_eligible_pool(
            pdf_papers, endpoint, golden_dataset_paths.paper_matches_endpoint
        )
        pool_pdf_keywords = _count_eligible_pool(
            pdf_papers, endpoint, golden_dataset_paths.paper_matches_endpoint_keywords
        )

        if selected:
            endpoints_with_papers += 1
        else:
            endpoints_empty += 1
        if len(selected) >= top_n:
            endpoints_full += 1
        total_selected += len(selected)

        scope_fields = golden_dataset_paths.scope_fields_for_endpoint(endpoint)
        required_fields = golden_candidate_scoring.required_gate_fields_for_endpoint(endpoint)
        scored_fields = golden_candidate_scoring.scored_fields_for_endpoint(endpoint)

        endpoint_results.append(
            {
                "endpoint_id": endpoint.id,
                "label": endpoint.label,
                "branch": endpoint.branch,
                "study_types": list(endpoint.study_types),
                "exposure_methods": list(endpoint.exposure_methods),
                "scope_key": endpoint.scope_key,
                "scope_fields": list(scope_fields),
                "required_gate_fields": list(required_fields),
                "scored_fields": list(scored_fields),
                "keyword_terms": {
                    "study_type": golden_dataset_paths.endpoint_keyword_terms(endpoint)[0],
                    "exposure_method": golden_dataset_paths.endpoint_keyword_terms(endpoint)[1],
                },
                "pool_size_pdf_classification": pool_pdf_classification,
                "pool_size_pdf_keywords": pool_pdf_keywords,
                "selection_tier_counts": tier_counts,
                "selected_count": len(selected),
                "papers": selected,
            }
        )

    return {
        "created_at": datetime.now().isoformat(),
        "description": (
            "Golden dataset: PDF-only original-research tree paths; top 10 papers per "
            "endpoint by characteristic count. Clinical requires population_age and "
            "population_sex. Selection tiers: PDF classification → PDF keywords."
        ),
        "pdf_paper_pool_size": len(pdf_papers),
        "endpoint_count": len(endpoints),
        "endpoints_with_papers": endpoints_with_papers,
        "endpoints_without_papers": endpoints_empty,
        "endpoints_at_target_count": endpoints_full,
        "total_paper_selections": total_selected,
        "top_n_per_endpoint": top_n,
        "endpoints": endpoint_results,
    }


def _summary_report(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a compact report for quick inspection."""
    rows: List[Dict[str, Any]] = []
    sorted_endpoints = golden_dataset_paths.sort_endpoints_by_pdf_class_pool(
        dataset.get("endpoints") or [],
        pdf_class_target=int(dataset.get("top_n_per_endpoint") or 10),
    )
    for endpoint in sorted_endpoints:
        rows.append(
            {
                "endpoint_id": endpoint.get("endpoint_id"),
                "label": endpoint.get("label"),
                "pool_pdf_classification": endpoint.get("pool_size_pdf_classification"),
                "pool_pdf_keywords": endpoint.get("pool_size_pdf_keywords"),
                "selected": endpoint.get("selected_count"),
                "selection_tier_counts": endpoint.get("selection_tier_counts"),
                "top_characteristic_count": (
                    endpoint["papers"][0]["characteristics_identified_count"]
                    if endpoint.get("papers")
                    else None
                ),
            }
        )
    return {
        "created_at": dataset.get("created_at"),
        "pdf_paper_pool_size": dataset.get("pdf_paper_pool_size"),
        "endpoint_count": dataset.get("endpoint_count"),
        "endpoints_with_papers": dataset.get("endpoints_with_papers"),
        "endpoints_without_papers": dataset.get("endpoints_without_papers"),
        "endpoints_at_target_count": dataset.get("endpoints_at_target_count"),
        "endpoints": rows,
    }


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Build tree-path golden dataset from local DB.")
    parser.add_argument("--top-n", type=int, default=TOP_N, help="Papers per endpoint (default: 10)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Summary report JSON path")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Paper text cache directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cache_dir = args.cache_dir or paper_text_cache.resolve_cache_dir()
    dataset = build_golden_dataset(top_n=args.top_n, cache_dir=cache_dir)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, default=str)

    report = _summary_report(dataset)
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)

    logger.info(
        "Wrote %s (%d endpoints, %d PDF papers, %d/%d endpoints at target, %d slots filled)",
        args.output,
        dataset["endpoint_count"],
        dataset["pdf_paper_pool_size"],
        dataset["endpoints_at_target_count"],
        dataset["endpoint_count"],
        dataset["total_paper_selections"],
    )
    logger.info("Summary report: %s", args.report)


if __name__ == "__main__":
    main()
