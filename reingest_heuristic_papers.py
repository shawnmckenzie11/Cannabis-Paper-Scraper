"""Re-run Maude classification on legacy heuristic papers (PDF → full text → abstract)."""

import argparse
import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime
from typing import Optional

import classifier
import maude_confidence
import paper_text_cache
from db_manager import DatabaseManager, _SQL_ORIGINAL_RESEARCH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
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
    "ingestion_status",
    "species",
    "summary",
    "classification_confidence",
    "classification_timestamp",
    "classifier_version",
]

TRACK_FIELDS = [
    "publication_type",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "classifier_version",
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


def _reingest_where_clause(
    *,
    only_heuristic: bool = True,
    maude_and_heuristic: bool = False,
) -> str:
    """Builds the SQL WHERE clause for Maude re-ingestion targets."""
    not_llm = (
        "(classifier_version NOT LIKE 'llm-reclassify-%' "
        "AND classifier_version NOT LIKE 'llm-pdf-%' "
        "AND classifier_version NOT LIKE 'llm-node%')"
    )
    if maude_and_heuristic:
        classifier_filter = (
            "(classifier_version LIKE 'maude-%' "
            "OR classifier_version LIKE 'heuristic%')"
        )
    elif only_heuristic:
        classifier_filter = "classifier_version = 'heuristic-1.0.0'"
    else:
        classifier_filter = (
            "classifier_version IS NULL "
            "OR classifier_version = 'heuristic-1.0.0' "
            "OR classifier_version LIKE 'heuristic-reclassify%'"
        )
    return f"{classifier_filter} AND {_SQL_ORIGINAL_RESEARCH} AND {not_llm}"


def reingest_heuristic_papers(
    dry_run: bool = False,
    batch_size: int = 25,
    limit: Optional[int] = None,
    only_heuristic: bool = True,
    maude_and_heuristic: bool = False,
) -> dict:
    """Re-classify legacy heuristic papers with the current Maude pipeline.

    Args:
        dry_run: When True, compute changes without writing to the database.
        batch_size: Commit interval for database writes.
        limit: Optional maximum number of papers to process.
        only_heuristic: When True, target heuristic-1.0.0 papers only.
        maude_and_heuristic: When True, target all maude-* and heuristic-* original
            research papers that were not LLM-classified (overrides only_heuristic).

    Returns:
        Summary statistics for the run.
    """
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    where_clause = _reingest_where_clause(
        only_heuristic=only_heuristic,
        maude_and_heuristic=maude_and_heuristic,
    )

    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    cur.execute(
        f"""
        SELECT id, pmid, doi, title, abstract, full_text_link, expert_locked_fields,
               study_type, exposure_method, cannabis_type, outcome_domain,
               publication_type, duration_days, classification_confidence, classifier_version
        FROM papers
        WHERE {where_clause}
        ORDER BY id
        {limit_sql}
        """
    )
    papers = cur.fetchall()
    total = len(papers)
    print(
        f"Starting Maude re-ingestion for {total} papers "
        f"(dry_run={dry_run}, limit={limit}) at {datetime.now().isoformat()}"
    )

    field_change_counts = Counter()
    source_counts = Counter()
    papers_changed = 0
    pdf_cache = {}
    start = time.time()

    for idx, row in enumerate(papers, 1):
        if not dry_run and idx % batch_size == 1 and idx > 1:
            conn.commit()
            conn.close()
            conn = db.get_connection()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

        paper = dict(row)
        locked = parse_json_field(paper.get("expert_locked_fields")) or []
        if not isinstance(locked, list):
            locked = []

        resolved_text, _ = paper_text_cache.resolve_paper_text(
            paper_id=paper["id"],
            full_text_link=paper.get("full_text_link"),
            pmid=paper.get("pmid"),
            doi=paper.get("doi"),
            memory_cache=pdf_cache,
        )
        extracted = classifier.process_paper_metadata(
            paper.get("title") or "",
            paper.get("abstract") or "",
            run_llm=False,
            full_text=resolved_text,
            full_text_link=paper.get("full_text_link"),
            pmid=paper.get("pmid"),
            doi=paper.get("doi"),
            pdf_cache=pdf_cache,
        )

        version = str(extracted.get("classifier_version") or "")
        if version.startswith("maude-pdf-"):
            source_counts["pdf"] += 1
        elif version.startswith("maude-fulltext-"):
            source_counts["fulltext"] += 1
        else:
            source_counts["abstract"] += 1

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
            db.sync_tab_flags_for_paper(paper["id"], conn=conn)

            if idx % batch_size == 0:
                conn.commit()
                elapsed = time.time() - start
                rate = idx / elapsed if elapsed else 0
                eta = (total - idx) / rate / 60 if rate else 0
                logger.info(
                    "Progress %s/%s changed=%s sources=%s elapsed=%.0fs eta=%.1fm",
                    idx,
                    total,
                    papers_changed,
                    dict(source_counts),
                    elapsed,
                    eta,
                )

    if not dry_run:
        conn.commit()
    conn.close()

    elapsed = time.time() - start
    summary = {
        "papers_processed": total,
        "papers_changed": papers_changed,
        "elapsed_minutes": round(elapsed / 60, 1),
        "source_counts": dict(source_counts),
        "field_change_counts": dict(field_change_counts),
        "dry_run": dry_run,
    }

    print(f"Maude re-ingestion complete: {summary}")
    return summary


def refresh_maude_confidence_scores(
    batch_size: int = 100,
    limit: Optional[int] = None,
) -> dict:
    """Recomputes classification_confidence for all maude-* papers from node alignment %."""
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    cur.execute(
        f"""
        SELECT id, title, abstract, publication_type, study_type, ingestion_status,
               classifier_version, classification_confidence
        FROM papers
        WHERE classifier_version LIKE 'maude-%'
        ORDER BY id
        {limit_sql}
        """
    )
    papers = cur.fetchall()
    updated = 0
    for idx, row in enumerate(papers, 1):
        paper = dict(row)
        block = {
            "publication_type": paper.get("publication_type"),
            "study_type": parse_json_field(paper.get("study_type")) or [],
            "ingestion_status": paper.get("ingestion_status"),
        }
        confidence = maude_confidence.confidence_for_classification(block)
        if paper.get("classification_confidence") != confidence:
            cur.execute(
                "UPDATE papers SET classification_confidence = ? WHERE id = ?",
                (confidence, paper["id"]),
            )
            updated += 1
        if idx % batch_size == 0:
            conn.commit()
    conn.commit()
    conn.close()
    summary = {"papers_scanned": len(papers), "papers_updated": updated}
    print(f"Maude confidence refresh complete: {summary}")
    return summary


def main():
    """CLI entry point for Maude re-ingestion of heuristic papers."""
    parser = argparse.ArgumentParser(
        description="Re-classify heuristic papers with Maude (PDF → full text → abstract)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute changes without writing to the database.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Commit interval for database writes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of papers to re-classify.",
    )
    parser.add_argument(
        "--all-native",
        action="store_true",
        help="Include NULL/heuristic-reclassify native papers, not only heuristic-1.0.0.",
    )
    parser.add_argument(
        "--maude-and-heuristic",
        action="store_true",
        help=(
            "Re-classify all maude-* and heuristic-* original research papers "
            "that were not LLM-classified (excludes reviews)."
        ),
    )
    parser.add_argument(
        "--refresh-maude-confidence",
        action="store_true",
        help="After re-ingestion, refresh classification_confidence on all maude-* papers.",
    )
    parser.add_argument(
        "--confidence-only",
        action="store_true",
        help="Only refresh maude-* classification_confidence from node alignment (no re-ingest).",
    )
    args = parser.parse_args()
    if args.confidence_only:
        refresh_maude_confidence_scores(batch_size=args.batch_size, limit=args.limit)
        return
    summary = reingest_heuristic_papers(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
        only_heuristic=not args.all_native and not args.maude_and_heuristic,
        maude_and_heuristic=args.maude_and_heuristic,
    )
    if args.refresh_maude_confidence or summary.get("papers_processed", 0) > 0:
        refresh_maude_confidence_scores(batch_size=args.batch_size)


if __name__ == "__main__":
    main()
