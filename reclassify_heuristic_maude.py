"""Apply Maude classification to legacy heuristic-1.0.0 papers in the live database."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import classification_schema
import extractor
import maude_classifier
from calibration_agent import get_rules_version, parse_json_list
from db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

NODE01_FIELDS: Tuple[str, ...] = ("ingestion_status", "publication_type")
DEFAULT_EVAL_SET = Path("scratch/calibration_runs/llm_reclassify_eval_set.json")
MIN_NODE01_AGREE_PCT = 95.0

MAUDE_UPDATE_COLUMNS: Tuple[str, ...] = (
    "ingestion_status",
    "publication_type",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
    "duration_days",
    "inhaled_exposure_duration",
    "administration_frequency",
    "treatment_duration",
    "exposure_regimen_bin",
    "repeat_exposure_count",
    "classification_confidence",
    "classification_timestamp",
    "classifier_version",
    "summary",
)


def serialize(field: str, value: Any) -> Any:
    """Serialize extracted values for DB writes."""
    if field in {"study_type", "exposure_method", "cannabis_type", "outcome_domain"}:
        return json.dumps(value) if value is not None else None
    return value


def measure_node01_accuracy(eval_path: Path) -> Dict[str, float]:
    """Returns Node 0/1 agreement rates from a stored llm-reclassify eval export."""
    payload = json.loads(eval_path.read_text(encoding="utf-8"))
    papers = payload.get("papers") or payload
    if not papers:
        raise ValueError(f"No papers found in eval set: {eval_path}")

    stats = {field: 0 for field in NODE01_FIELDS}
    both = 0
    for paper in papers:
        title = paper.get("title") or ""
        abstract = paper.get("abstract") or ""
        llm = paper.get("llm") or {}
        maude = maude_classifier.classify_paper(title, abstract)
        comparison = classification_schema.compare_classifiers(maude, llm, title, abstract)
        disagree = set((comparison.get("fields") or {}).keys())
        for field in NODE01_FIELDS:
            if field not in disagree:
                stats[field] += 1
        if not (disagree & set(NODE01_FIELDS)):
            both += 1

    total = len(papers)
    return {
        "paper_count": total,
        "ingestion_status_pct": round(stats["ingestion_status"] / total * 100, 1),
        "publication_type_pct": round(stats["publication_type"] / total * 100, 1),
        "both_pct": round(both / total * 100, 1),
    }


def measure_node01_accuracy_from_db(fetch_limit: int = 1000) -> Dict[str, float]:
    """Returns Node 0/1 agreement rates using llm-reclassify papers from the active database."""
    from calibration_agent import paper_row_to_llm_block, select_llm_pdf_reclassify_candidates

    candidates = select_llm_pdf_reclassify_candidates(
        fetch_limit=fetch_limit,
        include_abstract_reclassify=True,
        abstract_reclassify_only=True,
    )
    papers = [
        {
            "title": row.get("title") or "",
            "abstract": row.get("abstract") or "",
            "llm": paper_row_to_llm_block(row, row.get("title") or "", row.get("abstract") or ""),
        }
        for row in candidates
        if str(row.get("classifier_version", "")).startswith("llm-reclassify-")
    ]
    if not papers:
        raise ValueError("No llm-reclassify papers found in database for Node 0/1 gate.")

    stats = {field: 0 for field in NODE01_FIELDS}
    both = 0
    for paper in papers:
        title = paper["title"]
        abstract = paper["abstract"]
        llm = paper["llm"]
        maude = maude_classifier.classify_paper(title, abstract)
        comparison = classification_schema.compare_classifiers(maude, llm, title, abstract)
        disagree = set((comparison.get("fields") or {}).keys())
        for field in NODE01_FIELDS:
            if field not in disagree:
                stats[field] += 1
        if not (disagree & set(NODE01_FIELDS)):
            both += 1

    total = len(papers)
    return {
        "paper_count": total,
        "ingestion_status_pct": round(stats["ingestion_status"] / total * 100, 1),
        "publication_type_pct": round(stats["publication_type"] / total * 100, 1),
        "both_pct": round(both / total * 100, 1),
        "source": "database",
    }


def resolve_gate_metrics(eval_path: Optional[Path]) -> Dict[str, float]:
    """Loads Node 0/1 gate metrics from eval JSON or falls back to live DB llm-reclassify papers."""
    if eval_path and eval_path.exists():
        metrics = measure_node01_accuracy(eval_path)
        metrics["source"] = str(eval_path)
        return metrics
    logger.info("Eval set missing; measuring Node 0/1 gate from database llm-reclassify papers.")
    return measure_node01_accuracy_from_db()


def assert_node01_gate(metrics: Dict[str, float], min_pct: float = MIN_NODE01_AGREE_PCT) -> None:
    """Raises when Maude Node 0/1 accuracy is below the promotion threshold."""
    for field in NODE01_FIELDS:
        key = f"{field}_pct"
        if metrics[key] < min_pct:
            raise RuntimeError(
                f"Maude {field} agreement {metrics[key]}% is below {min_pct}% gate; aborting."
            )
    if metrics["both_pct"] < min_pct:
        raise RuntimeError(
            f"Maude combined Node 0/1 agreement {metrics['both_pct']}% is below {min_pct}% gate; aborting."
        )


def maude_record_for_paper(title: str, abstract: str, rules_version: str) -> Dict[str, Any]:
    """Runs Maude and returns DB-ready classification fields."""
    maude = maude_classifier.classify_paper(
        title,
        abstract,
        rules_version=rules_version,
        abstract_only_extraction=False,
    )
    record = classification_schema.normalize_classification_record(maude, title, abstract)
    record["classification_timestamp"] = datetime.now().isoformat()
    record["classifier_version"] = f"maude-{rules_version}"
    record["summary"] = extractor.generate_heuristic_summary(record)
    return record


def reclassify_heuristic_papers_with_maude(
    dry_run: bool = False,
    batch_size: int = 200,
    limit: Optional[int] = None,
    eval_path: Optional[Path] = None,
    skip_gate: bool = False,
    min_gate_pct: float = MIN_NODE01_AGREE_PCT,
) -> Dict[str, Any]:
    """Reclassifies heuristic-1.0.0 papers using Maude when Node 0/1 accuracy gate passes."""
    eval_path = eval_path or DEFAULT_EVAL_SET
    gate_metrics: Optional[Dict[str, float]] = None
    if not skip_gate:
        gate_metrics = resolve_gate_metrics(eval_path if eval_path.exists() else None)
        logger.info("Node 0/1 gate metrics: %s", gate_metrics)
        assert_node01_gate(gate_metrics, min_gate_pct)

    rules_version = get_rules_version()
    db = DatabaseManager()
    conn = db.get_connection()
    if not db.is_postgres:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
        SELECT id, title, abstract, expert_locked_fields, classifier_version
        FROM papers
        WHERE classifier_version = 'heuristic-1.0.0'
          AND abstract IS NOT NULL
          AND abstract != ''
        ORDER BY id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    cursor.execute(sql)
    papers = [dict(row) for row in cursor.fetchall()]
    total = len(papers)
    logger.info(
        "Starting Maude reclassification for %s heuristic-1.0.0 papers (dry_run=%s)",
        total,
        dry_run,
    )

    ingestion_counts: Counter = Counter()
    publication_counts: Counter = Counter()
    updated = 0
    start = time.time()

    for idx, paper in enumerate(papers, 1):
        locked = parse_json_list(paper.get("expert_locked_fields"))
        if not isinstance(locked, list):
            locked = []

        title = paper.get("title") or ""
        abstract = paper.get("abstract") or ""
        record = maude_record_for_paper(title, abstract, rules_version)
        ingestion_counts[record.get("ingestion_status") or "unknown"] += 1
        publication_counts[record.get("publication_type") or "null"] += 1

        if dry_run:
            continue

        set_parts: List[str] = []
        params: List[Any] = []
        for column in MAUDE_UPDATE_COLUMNS:
            if column in locked:
                continue
            set_parts.append(f"{column} = ?")
            params.append(serialize(column, record.get(column)))
        if set_parts:
            params.append(paper["id"])
            cursor.execute(
                f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            updated += 1

        if idx % batch_size == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed else 0.0
            eta_min = (total - idx) / rate / 60 if rate else 0.0
            logger.info(
                "Progress %s/%s updated=%s elapsed=%.0fs eta=%.1fm",
                idx,
                total,
                updated,
                elapsed,
                eta_min,
            )

    if not dry_run:
        conn.commit()
    conn.close()

    summary = {
        "papers_selected": total,
        "papers_updated": updated if not dry_run else 0,
        "dry_run": dry_run,
        "rules_version": rules_version,
        "classifier_version": f"maude-{rules_version}",
        "elapsed_minutes": round((time.time() - start) / 60, 1),
        "ingestion_status_counts": dict(ingestion_counts),
        "publication_type_counts": dict(publication_counts),
        "gate_metrics": gate_metrics,
    }
    logger.info("Maude heuristic reclassification complete: %s", summary)
    return summary


def main() -> None:
    """CLI entry point for Maude heuristic paper reclassification."""
    parser = argparse.ArgumentParser(
        description="Apply Maude to classify heuristic-1.0.0 papers when Node 0/1 gate passes.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute without DB writes.")
    parser.add_argument("--batch-size", type=int, default=200, help="Commit interval.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on papers processed.")
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=DEFAULT_EVAL_SET,
        help="llm-reclassify eval JSON used for the Node 0/1 promotion gate.",
    )
    parser.add_argument(
        "--skip-gate",
        action="store_true",
        help="Skip Node 0/1 accuracy gate (not recommended).",
    )
    parser.add_argument(
        "--min-gate-pct",
        type=float,
        default=MIN_NODE01_AGREE_PCT,
        help="Minimum Node 0/1 agreement percent required (default 95).",
    )
    args = parser.parse_args()
    reclassify_heuristic_papers_with_maude(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
        eval_path=args.eval_set,
        skip_gate=args.skip_gate,
        min_gate_pct=args.min_gate_pct,
    )


if __name__ == "__main__":
    main()
