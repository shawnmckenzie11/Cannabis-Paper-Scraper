#!/usr/bin/env python3
"""Backfill NULL THC/CBD concentration fields from cached paper text.

Safe for RL resumption:
- fill-NULL-only (never overwrites existing values)
- skips llm-reclassify papers by default
- local SQLite only; does not push to Postgres unless you run push separately
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import extractor
import paper_text_cache
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)

CONCENTRATION_COLUMNS: Sequence[str] = (
    "thc_pct",
    "cbd_pct",
    "thc_mg_ml",
    "thc_mg_g",
    "thc_mg_kg",
    "thc_uM",
    "cbd_mg_ml",
    "cbd_mg_g",
    "cbd_mg_kg",
    "cbd_uM",
)


def _resolve_paper_text(paper: Dict[str, Any]) -> str:
    """Return the best available text blob for concentration extraction."""
    paper_id = int(paper["id"])
    cached_text, _ = paper_text_cache.lookup_cached_text_for_paper(paper_id)
    if cached_text and cached_text.strip():
        return cached_text
    title = paper.get("title") or ""
    abstract = paper.get("abstract") or ""
    return f"{title}\n\n{abstract}".strip()


def _null_columns(paper: Dict[str, Any]) -> List[str]:
    """Return concentration columns that are currently NULL on this paper row."""
    return [col for col in CONCENTRATION_COLUMNS if paper.get(col) is None]


def _extract_for_null_columns(text: str, null_cols: Sequence[str]) -> Dict[str, float]:
    """Extract only fields that are NULL and have a hit in text."""
    thc = extractor.extract_thc_concentrations(text)
    cbd = extractor.extract_cbd_concentrations(text)
    merged = {**thc, **cbd}
    return {col: merged[col] for col in null_cols if merged.get(col) is not None}


def _select_papers(
    db: DatabaseManager,
    *,
    limit: Optional[int],
    paper_ids: Optional[Sequence[int]],
    all_cannabis: bool,
    include_llm: bool,
) -> List[Dict[str, Any]]:
    """Load candidate papers with at least one NULL concentration column."""
    conn = db.get_connection()
    try:
        clauses = [
            "(" + " OR ".join(f"{col} IS NULL" for col in CONCENTRATION_COLUMNS) + ")",
        ]
        params: List[Any] = []
        if not include_llm:
            clauses.append(
                "(classifier_version NOT LIKE 'llm-reclassify-%' "
                "AND classifier_version NOT LIKE 'llm-pdf-%' "
                "AND classifier_version NOT LIKE 'llm-node%')"
            )
        if all_cannabis:
            clauses.append("(ingestion_status IS NULL OR ingestion_status != 'not_cannabis_related')")
        if paper_ids:
            placeholders = ",".join("?" for _ in paper_ids)
            clauses.append(f"id IN ({placeholders})")
            params.extend(int(pid) for pid in paper_ids)

        sql = (
            "SELECT id, title, abstract, ingestion_status, classifier_version, "
            + ", ".join(CONCENTRATION_COLUMNS)
            + " FROM papers WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def backpopulate_cannabinoid_concentrations(
    *,
    dry_run: bool = False,
    limit: Optional[int] = None,
    paper_ids: Optional[Sequence[int]] = None,
    all_cannabis: bool = True,
    include_llm: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Fill NULL concentration columns from text; returns summary stats."""
    db = DatabaseManager()
    papers = _select_papers(
        db,
        limit=limit,
        paper_ids=paper_ids,
        all_cannabis=all_cannabis,
        include_llm=include_llm,
    )
    stats: Dict[str, Any] = {
        "papers_scanned": len(papers),
        "papers_updated": 0,
        "fields_filled": {col: 0 for col in CONCENTRATION_COLUMNS},
        "samples": [],
    }
    if not papers:
        logger.info("No papers with NULL concentration fields found.")
        return stats

    conn = db.get_connection()
    try:
        for paper in papers:
            null_cols = _null_columns(paper)
            if not null_cols:
                continue
            text = _resolve_paper_text(paper)
            if not extractor.text_has_concentration_signals(text):
                continue
            updates = _extract_for_null_columns(text, null_cols)
            if not updates:
                continue

            if verbose and len(stats["samples"]) < 10:
                stats["samples"].append({"paper_id": paper["id"], **updates})

            if dry_run:
                stats["papers_updated"] += 1
                for col in updates:
                    stats["fields_filled"][col] += 1
                continue

            set_parts = [f"{col} = ?" for col in updates]
            params = list(updates.values()) + [paper["id"]]
            conn.execute(
                f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            if not db.is_postgres:
                try:
                    from local_sync import mark_papers_dirty

                    mark_papers_dirty(conn, [int(paper["id"])])
                except Exception:
                    logger.debug("Could not mark paper %s dirty", paper["id"])
            stats["papers_updated"] += 1
            for col in updates:
                stats["fields_filled"][col] += 1

        if not dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return stats


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill NULL THC/CBD concentration fields (fill-NULL-only, local SQLite).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report hits without writing.")
    parser.add_argument("--limit", type=int, default=None, help="Max papers to scan.")
    parser.add_argument("--paper-ids", type=str, default=None, help="Comma-separated paper IDs.")
    parser.add_argument(
        "--include-llm",
        action="store_true",
        help="Include llm-reclassify papers (default: skip to preserve LLM labels).",
    )
    parser.add_argument(
        "--all-cannabis",
        action="store_true",
        default=True,
        help="Only cannabis-related papers (default: True).",
    )
    parser.add_argument("--verbose", action="store_true", help="Log sample updates.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    paper_ids = None
    if args.paper_ids:
        paper_ids = [int(x.strip()) for x in args.paper_ids.split(",") if x.strip()]

    stats = backpopulate_cannabinoid_concentrations(
        dry_run=args.dry_run,
        limit=args.limit,
        paper_ids=paper_ids,
        all_cannabis=args.all_cannabis,
        include_llm=args.include_llm,
        verbose=args.verbose,
    )
    logger.info("papers_scanned=%s papers_updated=%s", stats["papers_scanned"], stats["papers_updated"])
    for col, count in stats["fields_filled"].items():
        if count:
            logger.info("  %s: %s", col, count)
    if stats.get("samples"):
        logger.info("samples: %s", stats["samples"])


if __name__ == "__main__":
    main()
