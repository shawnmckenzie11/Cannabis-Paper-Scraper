"""Re-run full heuristic classification on all non-LLM papers in the catalog."""

import argparse
import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime

import classifier
from db_manager import DatabaseManager

logging.basicConfig(
    level=logging.WARNING,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s",
)
logger = logging.getLogger(__name__)

UPDATE_COLUMNS = [
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "duration_days",
    "inhaled_exposure_duration",
    "administration_frequency",
    "treatment_duration",
    "sample_size",
    "thc_pct",
    "cbd_pct",
    "dose_mg",
    "strain_reported",
    "strain_normalized",
    "publication_type",
    "summary",
    "classification_confidence",
    "classification_timestamp",
    "classifier_version",
]

TRACK_FIELDS = [
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "duration_days",
    "classification_confidence",
]


def parse_json_field(val):
    """Parse a JSON-encoded DB field into native Python values."""
    if val is None:
        return None
    if isinstance(val, (list, dict, int, float)):
        return val
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("[") or val.startswith("{"):
            try:
                return json.loads(val)
            except Exception:
                pass
        return val
    return val


def norm(val):
    """Normalize list/JSON DB values for change comparison."""
    parsed = parse_json_field(val)
    if isinstance(parsed, list):
        return sorted(str(x) for x in parsed)
    return parsed


def serialize(field, val):
    """Serialize extracted values for DB writes."""
    if field in {"study_type", "exposure_method", "cannabis_type", "outcome_domain"}:
        return json.dumps(val) if val is not None else None
    return val


def reingest_heuristic_papers(
    dry_run: bool = False,
    batch_size: int = 100,
    only_pending: bool = False,
    max_papers: int | None = None,
) -> dict:
    """Re-run heuristic classification for all non-LLM, non-Maude papers.

    Args:
        dry_run: When True, compute changes without writing to the database.
        batch_size: Commit interval for database writes.
        only_pending: When True, only reprocess legacy heuristic-reclassify records.
        max_papers: Optional upper bound for the number of selected papers.

    Returns:
        Summary statistics for the run.
    """
    if max_papers is not None and max_papers < 1:
        raise ValueError("max_papers must be a positive integer when provided")

    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if only_pending:
        where_clause = "classifier_version LIKE 'heuristic-reclassify%'"
    else:
        where_clause = (
            "classifier_version IS NULL "
            "OR classifier_version = 'heuristic-1.0.0' "
            "OR classifier_version LIKE 'heuristic-reclassify%'"
        )

    limit_clause = "LIMIT ?" if max_papers is not None else ""
    params = (max_papers,) if max_papers is not None else ()

    cur.execute(
        f"""
        SELECT id, title, abstract, expert_locked_fields,
               study_type, exposure_method, cannabis_type, outcome_domain,
               duration_days, classification_confidence, classifier_version
        FROM papers
        WHERE {where_clause}
        ORDER BY id
        {limit_clause}
        """,
        params,
    )
    papers = cur.fetchall()
    total = len(papers)
    print(
        f"Starting heuristic re-ingestion for {total} papers "
        f"(dry_run={dry_run}, only_pending={only_pending}) at {datetime.now().isoformat()}"
    )

    field_change_counts = Counter()
    papers_changed = 0
    confidence_before_high = 0
    confidence_after_high = 0
    start = time.time()

    for idx, row in enumerate(papers, 1):
        if not dry_run and idx % 500 == 1 and idx > 1:
            conn.commit()
            conn.close()
            conn = db.get_connection()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

        paper = dict(row)
        locked = parse_json_field(paper.get("expert_locked_fields")) or []
        if not isinstance(locked, list):
            locked = []

        extracted = classifier.process_paper_metadata(
            paper.get("title") or "",
            paper.get("abstract") or "",
            run_llm=False,
        )

        if (paper.get("classification_confidence") or 0) >= 0.85:
            confidence_before_high += 1
        if (extracted.get("classification_confidence") or 0) >= 0.85:
            confidence_after_high += 1

        paper_changed = False
        for field in TRACK_FIELDS:
            if field in locked:
                continue
            if norm(paper.get(field)) != norm(extracted.get(field)):
                field_change_counts[field] += 1
                paper_changed = True
        if paper_changed:
            papers_changed += 1

        if not dry_run:
            set_parts = []
            params = []
            for col in UPDATE_COLUMNS:
                if col in locked:
                    continue
                set_parts.append(f"{col} = ?")
                params.append(serialize(col, extracted.get(col)))
            params.append(paper["id"])
            cur.execute(f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?", params)

            if idx % batch_size == 0:
                conn.commit()
                elapsed = time.time() - start
                rate = idx / elapsed if elapsed else 0
                eta = (total - idx) / rate / 60 if rate else 0
                print(
                    f"  [{idx}/{total}] changed={papers_changed} "
                    f"elapsed={elapsed:.0f}s eta={eta:.1f}m"
                )

    if not dry_run:
        conn.commit()
    conn.close()

    elapsed = time.time() - start
    summary = {
        "papers_processed": total,
        "papers_changed": papers_changed,
        "elapsed_minutes": round(elapsed / 60, 1),
        "auto_accept_before": confidence_before_high,
        "auto_accept_after": confidence_after_high,
        "field_change_counts": dict(field_change_counts),
        "dry_run": dry_run,
    }

    print(f"Heuristic re-ingestion complete: {summary}")
    return summary


def main():
    """CLI entry point for heuristic paper re-ingestion."""
    parser = argparse.ArgumentParser(
        description="Re-run heuristic classification for all non-LLM papers."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute changes without writing to the database.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Commit interval for database writes.",
    )
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Only reprocess legacy heuristic-reclassify records.",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Maximum number of matching papers to reprocess in this run.",
    )
    args = parser.parse_args()
    reingest_heuristic_papers(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        only_pending=args.only_pending,
        max_papers=args.max_papers,
    )


if __name__ == "__main__":
    main()
