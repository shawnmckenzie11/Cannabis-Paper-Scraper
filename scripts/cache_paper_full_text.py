#!/usr/bin/env python3
"""Download and cache paper PDFs and resolved full text locally for faster Maude re-runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paper_text_cache
from maude_cues import resolve_calibration_output_dir


def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Cache paper PDFs and full text under scratch/paper_cache/ (gitignored).",
    )
    parser.add_argument(
        "--source",
        choices=("batches", "db", "all"),
        default="all",
        help="Paper set: calibration batch JSONs, database, or both (default: all).",
    )
    parser.add_argument(
        "--calibration-dir",
        type=Path,
        default=None,
        help="Calibration runs directory (default: scratch/calibration_runs or Fly /data).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Cache output directory (default: scratch/paper_cache).",
    )
    parser.add_argument(
        "--classifier-prefix",
        default="llm-pdf-reclassify-",
        help="DB filter for --source db/all (default: llm-pdf-reclassify-).",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        action="append",
        dest="paper_ids",
        help="Cache specific paper id(s) from the database.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit DB papers fetched.")
    parser.add_argument("--offset", type=int, default=0, help="Offset for DB paper query.")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-download even when a cache entry already exists.",
    )
    return parser


def collect_papers(args: argparse.Namespace) -> list:
    """Collects paper rows to cache based on CLI args."""
    papers_by_id: dict[int, dict] = {}

    if args.paper_ids:
        from db_manager import DatabaseManager

        db = DatabaseManager()
        for paper_id in args.paper_ids:
            conn = db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT id, title, pmid, doi, full_text_link FROM papers WHERE id = ?",
                    (paper_id,),
                )
                row = cursor.fetchone()
                if not row:
                    print(f"Warning: paper id {paper_id} not found in database", file=sys.stderr)
                    continue
                data = dict(row)
                papers_by_id[int(data["id"])] = {
                    "paper_id": int(data["id"]),
                    "title": data.get("title") or "",
                    "pmid": data.get("pmid") or "",
                    "doi": data.get("doi") or "",
                    "full_text_link": data.get("full_text_link") or "",
                }
            finally:
                conn.close()

    if args.source in ("batches", "all"):
        cal_dir = args.calibration_dir or resolve_calibration_output_dir()
        for paper in paper_text_cache.iter_calibration_batch_papers(cal_dir):
            papers_by_id[int(paper["paper_id"])] = paper

    if args.source in ("db", "all"):
        for paper in paper_text_cache.iter_db_papers_with_links(
            classifier_prefix=args.classifier_prefix,
            limit=args.limit,
            offset=args.offset,
        ):
            papers_by_id[int(paper["paper_id"])] = paper

    return list(papers_by_id.values())


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    papers = collect_papers(args)
    if not papers:
        print("No papers to cache.")
        return

    print(f"Caching {len(papers)} paper(s) → {paper_text_cache.resolve_cache_dir(args.cache_dir)}")
    summary = paper_text_cache.cache_papers(
        papers,
        force_refresh=args.force_refresh,
        cache_dir=args.cache_dir,
    )
    print(
        f"Done: cached={summary['cached']} skipped={summary['skipped']} "
        f"failed={summary['failed']} / {summary['requested']}"
    )
    if summary["failed"]:
        failed = [row for row in summary["results"] if row.get("status") == "failed"]
        print(json.dumps(failed[:10], indent=2))


if __name__ == "__main__":
    main()
