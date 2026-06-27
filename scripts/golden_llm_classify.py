#!/usr/bin/env python3
"""Batch LLM classification for golden endpoint candidate papers."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibration_metrics
import classifier
import content_tiers
import golden_confirmed_store
import golden_dataset_paths
import paper_text_cache
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)

VALID_COLUMNS = {
    "study_type", "exposure_method", "cannabis_type", "publication_type",
    "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
    "strain_reported", "strain_normalized", "duration_days",
    "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
    "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
    "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM",
    "population_age", "population_sex", "inclusion_criteria", "exclusion_criteria",
    "species",
}

GOLDEN_LLM_CONFIDENCE = 0.9


def _rules_version() -> str:
    """Loads rules_config.json version string."""
    rules_path = ROOT / "rules_config.json"
    if rules_path.exists():
        try:
            with open(rules_path, encoding="utf-8") as handle:
                return str(json.load(handle).get("version") or "1.0.0")
        except Exception:
            pass
    return "1.0.0"


def _parse_locked_fields(raw: Any) -> List[str]:
    """Parses expert_locked_fields from DB row values."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass
    return []


def _resolve_text_for_paper(paper: Dict[str, Any], cache_dir: Optional[Path]) -> tuple[str, str]:
    """Returns (full_text_or_none, text_source label) for classification."""
    paper_id = paper.get("id")
    if paper_id is not None:
        cached_text, source = paper_text_cache.lookup_cached_text_for_paper(int(paper_id))
        if cached_text:
            return cached_text, "pdf_cache"
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    return None, "title_abstract"


def golden_classifier_version(endpoint_id: str, rules_version: str) -> str:
    """Returns the classifier_version stamp for golden LLM runs."""
    return f"llm-golden-{endpoint_id}-{rules_version}"


def classify_papers_for_endpoint(
    paper_ids: Sequence[int],
    endpoint_id: str,
    *,
    sqlite_path: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    confidence: float = GOLDEN_LLM_CONFIDENCE,
) -> Dict[str, Any]:
    """Runs Claude LLM classification on paper IDs and updates local SQLite."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for golden LLM classification.")

    endpoint = golden_dataset_paths.endpoint_by_id(endpoint_id)
    if endpoint is None:
        raise ValueError(f"Unknown endpoint_id: {endpoint_id}")

    scope_fields = golden_dataset_paths.scope_fields_for_endpoint(endpoint)
    rules_version = _rules_version()
    classifier_version = golden_classifier_version(endpoint_id, rules_version)

    db = DatabaseManager(db_path=sqlite_path) if sqlite_path else DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    results: List[Dict[str, Any]] = []
    updated_ids: List[int] = []

    try:
        for paper_id in paper_ids:
            cursor.execute("SELECT * FROM papers WHERE id = ?", (int(paper_id),))
            row = cursor.fetchone()
            if not row:
                logger.warning("Paper id %s not found in SQLite; skipping.", paper_id)
                continue
            paper = dict(row)
            title = paper.get("title") or ""
            abstract = paper.get("abstract") or ""
            locked_fields = _parse_locked_fields(paper.get("expert_locked_fields"))

            full_text, text_source = _resolve_text_for_paper(paper, cache_dir)
            try:
                extracted = classifier.process_paper_metadata(
                    title,
                    abstract,
                    run_llm=True,
                    full_text=full_text,
                )
            except Exception as exc:
                logger.error("LLM classify failed for paper %s: %s", paper_id, exc)
                continue

            if not extracted:
                logger.warning("No LLM output for paper %s; skipping.", paper_id)
                continue

            update_data: Dict[str, Any] = {}
            for key, value in extracted.items():
                if key in VALID_COLUMNS and key not in locked_fields:
                    update_data[key] = value

            set_clauses = []
            params: List[Any] = []
            for key, value in update_data.items():
                set_clauses.append(f"{key} = ?")
                if isinstance(value, (list, dict)):
                    params.append(json.dumps(value))
                else:
                    params.append(value)

            set_clauses.append("classifier_version = ?")
            params.append(classifier_version)
            set_clauses.append("classification_timestamp = ?")
            params.append(datetime.now().isoformat())
            set_clauses.append("classification_confidence = ?")
            params.append(confidence)
            params.append(int(paper_id))

            cursor.execute(
                f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )

            cursor.execute("SELECT * FROM papers WHERE id = ?", (int(paper_id),))
            updated_row = dict(cursor.fetchone())
            char_count = sum(
                1
                for field in scope_fields
                if calibration_metrics.field_is_populated(updated_row.get(field))
            )
            ground_truth = golden_confirmed_store.build_ground_truth_from_row(
                updated_row,
                scope_fields,
            )
            tier_row = {
                **ground_truth,
                "classifier_version": classifier_version,
            }
            content_tier = content_tiers.infer_content_tier(tier_row)
            result = {
                "paper_id": int(paper_id),
                "pmid": updated_row.get("pmid"),
                "title": updated_row.get("title"),
                "endpoint_id": endpoint_id,
                "scope_subnode": endpoint.scope_subnode,
                "scope_key": endpoint.scope_key,
                "scope_fields": list(scope_fields),
                "classifier_version": classifier_version,
                "classification_confidence": confidence,
                "text_source": text_source,
                "content_tier": content_tier,
                "characteristics_identified_count": char_count,
                "characteristics_identified": {
                    field: ground_truth[field]
                    for field in scope_fields
                    if field in ground_truth
                },
                "ground_truth": ground_truth,
            }
            results.append(result)
            updated_ids.append(int(paper_id))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "endpoint_id": endpoint_id,
        "scope_subnode": endpoint.scope_subnode,
        "rules_version": rules_version,
        "classifier_version": classifier_version,
        "classification_confidence": confidence,
        "paper_ids_requested": list(paper_ids),
        "papers_updated": updated_ids,
        "results": results,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Golden endpoint LLM classification.")
    parser.add_argument("--endpoint-id", required=True, help="Tree path endpoint id.")
    parser.add_argument(
        "--paper-id",
        type=int,
        action="append",
        dest="paper_ids",
        help="Paper id(s) to classify.",
    )
    parser.add_argument("--sqlite-path", default=None, help="Local SQLite database path.")
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Paper text cache directory (default: scratch/paper_cache).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path for llm_results.json.",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()
    if not args.paper_ids:
        raise SystemExit("At least one --paper-id is required.")

    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    summary = classify_papers_for_endpoint(
        args.paper_ids,
        args.endpoint_id,
        sqlite_path=args.sqlite_path,
        cache_dir=cache_dir,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, ensure_ascii=False)
        logger.info("Wrote LLM results to %s", out_path)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
