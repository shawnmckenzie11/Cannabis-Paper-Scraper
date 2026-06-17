# calibration_agent.py
import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db_manager import DatabaseManager
import classifier


CALIBRATION_FIELDS = [
    "study_type", "exposure_method", "cannabis_type", "publication_type",
    "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
    "strain_reported", "strain_normalized", "duration_days",
    "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
    "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
    "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM",
]


def parse_json_list(value: Any) -> List[str]:
    """Parses a database JSON-list field into a Python list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def serialize_db_value(value: Any) -> Any:
    """Serializes list and dict values for storage in text-backed DB fields."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if value == "":
        return None
    return value


def comparable_value(value: Any) -> Any:
    """Normalizes a value enough to compare before and after classifications."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return sorted(str(item) for item in parsed)
            except Exception:
                pass
        return stripped
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


def get_rules_version() -> str:
    """Returns the active rules configuration version."""
    return classifier.load_rules_config().get("version", "1.0.0")


def select_candidates(
    mode: str,
    fetch_limit: int,
    confidence_max: float,
    require_full_text: bool,
    exclude_locked: bool,
    exclude_calibrated: bool,
) -> List[Dict[str, Any]]:
    """Selects candidate papers for a bounded calibration run."""
    db = DatabaseManager()
    conn = db.get_connection()
    if not db.is_postgres:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    where_clauses = [
        "(abstract IS NOT NULL AND abstract != '')",
    ]
    params: List[Any] = []

    if exclude_locked:
        where_clauses.append(
            "(expert_locked_fields IS NULL OR expert_locked_fields = '' OR expert_locked_fields = '[]')"
        )

    if require_full_text:
        where_clauses.append("(full_text_link IS NOT NULL AND full_text_link != '')")

    if exclude_calibrated:
        where_clauses.append("(classifier_version IS NULL OR classifier_version NOT LIKE 'llm-calibration-%')")

    if mode == "low_confidence":
        where_clauses.append("classification_confidence IS NOT NULL")
        where_clauses.append("classification_confidence <= ?")
        params.append(confidence_max)
    elif mode == "unclassified":
        where_clauses.append("(classifier_version IS NULL OR classifier_version NOT LIKE 'llm-%')")
    elif mode == "preclinical_original":
        where_clauses.append("publication_type = 'original research'")
        where_clauses.append(
            "(study_type LIKE '%Animal%' OR study_type LIKE '%Cell%' OR study_type LIKE '%vitro%')"
        )
    elif mode != "mixed":
        raise ValueError(f"Unknown calibration candidate mode: {mode}")

    params.append(fetch_limit)
    sql = f"""
        SELECT
            id, pmid, doi, title, abstract, full_text_link, study_type,
            exposure_method, cannabis_type, publication_type, outcome_domain,
            thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
            duration_days, inhaled_exposure_duration, administration_frequency,
            treatment_duration, sample_size, puff_count, thc_mg_ml, thc_mg_g,
            thc_mg_kg, cbd_mg_ml, cbd_mg_g, cbd_mg_kg, thc_uM, cbd_uM,
            classification_confidence, classification_timestamp,
            classifier_version, expert_locked_fields, citation_count,
            date_harvested
        FROM papers
        WHERE {' AND '.join(where_clauses)}
        ORDER BY
            COALESCE(classification_confidence, 0) ASC,
            citation_count DESC,
            date_harvested DESC,
            id DESC
        LIMIT ?
    """

    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def build_change_summary(before: Dict[str, Any], after: Dict[str, Any], locked_fields: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Builds a field-level diff between stored and newly extracted classifications."""
    changes: Dict[str, Dict[str, Any]] = {}
    for field in CALIBRATION_FIELDS:
        if field in locked_fields or field not in after:
            continue
        old_value = before.get(field)
        new_value = after.get(field)
        if comparable_value(old_value) != comparable_value(new_value):
            changes[field] = {
                "old": old_value,
                "new": new_value,
            }
    return changes


def apply_classification_update(
    db: DatabaseManager,
    paper_id: int,
    extracted: Dict[str, Any],
    locked_fields: Sequence[str],
    variant: str,
    batch_id: str,
    rules_version: str,
    dry_run: bool,
) -> int:
    """Applies unlocked classifier fields and logs call metrics for a calibration paper."""
    update_data = {
        field: extracted[field]
        for field in CALIBRATION_FIELDS
        if field in extracted and field not in locked_fields
    }
    if not update_data or dry_run:
        return 0

    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        set_clauses = []
        params: List[Any] = []
        for field, value in update_data.items():
            set_clauses.append(f"{field} = ?")
            params.append(serialize_db_value(value))

        classifier_version = f"llm-calibration-{variant}-{rules_version}"
        set_clauses.extend([
            "classifier_version = ?",
            "classification_timestamp = ?",
        ])
        params.extend([classifier_version, datetime.now().isoformat()])

        if "classification_confidence" in extracted:
            set_clauses.append("classification_confidence = ?")
            params.append(extracted["classification_confidence"])

        params.append(paper_id)
        cursor.execute(f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?", params)

        metrics = extracted.get("_llm_call_metrics")
        if metrics:
            metrics = dict(metrics)
            metrics["classifier_version"] = classifier_version
            db.log_llm_call(paper_id=paper_id, metrics=metrics, batch_id=batch_id, cursor=cursor)

        conn.commit()
        return 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_calibration(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Runs a bounded calibration cycle and writes JSON plus Markdown walkthrough artifacts."""
    if args.max_calls < 1 or args.max_calls > 50:
        raise ValueError("--max-calls must be between 1 and 50 for this learning pass.")
    if not args.dry_run and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required unless --dry-run is set.")

    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]
    if not variants:
        variants = ["control"]

    fetch_limit = max(args.fetch_limit, args.max_calls * max(2, len(variants)))
    candidates = select_candidates(
        mode=args.mode,
        fetch_limit=fetch_limit,
        confidence_max=args.confidence_max,
        require_full_text=args.require_full_text,
        exclude_locked=not args.include_locked,
        exclude_calibrated=not args.include_calibrated,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"calibration_{timestamp}"
    rules_version = get_rules_version()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{batch_id}.json"
    walkthrough_path = output_dir / f"{batch_id}_walkthrough.md"

    db = DatabaseManager()
    results: List[Dict[str, Any]] = []
    original_variant = os.environ.get("CLASSIFIER_PROMPT_VARIANT")
    calls_attempted = 0
    planned_candidates = 0
    updates_applied = 0

    try:
        for candidate in candidates:
            budget_index = planned_candidates if args.dry_run else calls_attempted
            if budget_index >= args.max_calls:
                break

            paper_id = int(candidate["id"])
            variant = variants[budget_index % len(variants)]
            locked_fields = parse_json_list(candidate.get("expert_locked_fields"))

            record: Dict[str, Any] = {
                "paper_id": paper_id,
                "pmid": candidate.get("pmid"),
                "title": candidate.get("title"),
                "variant": variant,
                "dry_run": args.dry_run,
                "locked_fields": locked_fields,
                "before_confidence": candidate.get("classification_confidence"),
                "before_classifier_version": candidate.get("classifier_version"),
            }

            if args.dry_run:
                record["status"] = "candidate_only"
                results.append(record)
                planned_candidates += 1
                continue

            os.environ["CLASSIFIER_PROMPT_VARIANT"] = variant
            calls_attempted += 1
            extracted = classifier.process_paper_metadata(
                candidate.get("title") or "",
                candidate.get("abstract") or "",
                run_llm=True,
                runs=args.runs,
                full_text=None if args.abstract_only else None,
            )

            if not extracted:
                record["status"] = "no_extraction"
                results.append(record)
                continue

            changes = build_change_summary(candidate, extracted, locked_fields)
            applied = apply_classification_update(
                db=db,
                paper_id=paper_id,
                extracted=extracted,
                locked_fields=locked_fields,
                variant=variant,
                batch_id=batch_id,
                rules_version=rules_version,
                dry_run=args.dry_run,
            )
            updates_applied += applied

            record.update({
                "status": "updated" if applied else "no_update",
                "after_confidence": extracted.get("classification_confidence"),
                "after_classifier_version": f"llm-calibration-{variant}-{rules_version}",
                "changes": changes,
                "llm_metrics": extracted.get("_llm_call_metrics", {}),
            })
            results.append(record)
    finally:
        if original_variant is None:
            os.environ.pop("CLASSIFIER_PROMPT_VARIANT", None)
        else:
            os.environ["CLASSIFIER_PROMPT_VARIANT"] = original_variant

    payload = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "mode": args.mode,
        "variants": variants,
        "max_calls": args.max_calls,
        "calls_attempted": calls_attempted,
        "planned_candidates": planned_candidates,
        "updates_applied": updates_applied,
        "dry_run": args.dry_run,
        "abstract_only": args.abstract_only,
        "candidate_count": len(candidates),
        "results": results,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    write_walkthrough(payload, walkthrough_path)
    return json_path, walkthrough_path


def write_walkthrough(payload: Dict[str, Any], path: Path) -> None:
    """Writes a Markdown walkthrough for the bounded calibration cycle."""
    variant_counts: Dict[str, int] = {}
    update_counts: Dict[str, int] = {}
    variant_confidences: Dict[str, List[float]] = {}
    variant_costs: Dict[str, float] = {}
    changed_fields: Dict[str, int] = {}
    total_cost = 0.0

    for result in payload["results"]:
        variant = result.get("variant", "unknown")
        variant_counts[variant] = variant_counts.get(variant, 0) + 1
        if result.get("after_confidence") is not None:
            variant_confidences.setdefault(variant, []).append(float(result["after_confidence"]))
        if result.get("status") == "updated":
            update_counts[variant] = update_counts.get(variant, 0) + 1
        for field in (result.get("changes") or {}).keys():
            changed_fields[field] = changed_fields.get(field, 0) + 1
        metrics = result.get("llm_metrics") or {}
        cost = float(metrics.get("cost") or 0.0)
        total_cost += cost
        variant_costs[variant] = variant_costs.get(variant, 0.0) + cost

    attempt_line = (
        f"- Planned candidates: `{payload.get('planned_candidates', 0)}` / `{payload['max_calls']}`\n"
        if payload["dry_run"]
        else f"- Claude classification attempts used: `{payload['calls_attempted']}` / `{payload['max_calls']}`\n"
    )

    lines = [
        "# Calibration Walkthrough\n\n",
        f"- Batch ID: `{payload['batch_id']}`\n",
        f"- Created: `{payload['created_at']}`\n",
        f"- Rules version: `{payload['rules_version']}`\n",
        f"- Candidate mode: `{payload['mode']}`\n",
        f"- Dry run: `{payload['dry_run']}`\n",
        f"- Abstract only: `{payload['abstract_only']}`\n",
        attempt_line,
        f"- Updates applied: `{payload['updates_applied']}`\n",
        f"- Estimated API cost: `${total_cost:.4f}`\n\n",
        "## Variant Allocation\n\n",
    ]

    for variant, count in sorted(variant_counts.items()):
        confidences = variant_confidences.get(variant) or []
        avg_confidence = sum(confidences) / len(confidences) if confidences else None
        confidence_text = f", avg confidence {avg_confidence:.3f}" if avg_confidence is not None else ""
        lines.append(
            f"- `{variant}`: {count} papers, {update_counts.get(variant, 0)} updates"
            f"{confidence_text}, cost ${variant_costs.get(variant, 0.0):.4f}\n"
        )

    lines.append("\n## Most Changed Fields\n\n")
    if changed_fields:
        for field, count in sorted(changed_fields.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- `{field}`: {count} changed papers\n")
    else:
        lines.append("- No field changes recorded.\n")

    lines.extend([
        "\n## Next Agent Actions\n\n",
        "1. Review the JSON artifact for papers with large changes in high-level fields.\n",
        "2. Use `/api/classification/queue` to surface low-confidence outputs for expert review.\n",
        "3. Apply expert-approved corrections through `/api/papers/<paper_id>/edit-classification`.\n",
        "4. Run `/api/classification/run-eval` after correction volume reaches the configured threshold.\n",
    ])

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds the command-line parser for the calibration runner."""
    parser = argparse.ArgumentParser(description="Run a bounded Claude calibration cycle.")
    parser.add_argument("--max-calls", type=int, default=50, help="Maximum Claude classification attempts, capped at 50.")
    parser.add_argument("--fetch-limit", type=int, default=100, help="Candidate rows to inspect before applying the call cap.")
    parser.add_argument("--mode", choices=["preclinical_original", "low_confidence", "unclassified", "mixed"], default="preclinical_original")
    parser.add_argument("--confidence-max", type=float, default=0.6, help="Confidence ceiling for low_confidence mode.")
    parser.add_argument("--variants", default="control,decision_checklist", help="Comma-separated prompt variants.")
    parser.add_argument("--runs", type=int, default=1, help="Self-consistency runs per classification.")
    parser.add_argument("--output-dir", default="scratch/calibration_runs", help="Directory for JSON and walkthrough artifacts.")
    parser.add_argument("--dry-run", action="store_true", help="Select candidates and write artifacts without Claude calls or DB updates.")
    parser.add_argument("--abstract-only", action="store_true", default=True, help="Use title and abstract only for budget-stable calibration.")
    parser.add_argument("--require-full-text", action="store_true", help="Select only papers with a full_text_link.")
    parser.add_argument("--include-locked", action="store_true", help="Include papers with expert-locked fields.")
    parser.add_argument("--include-calibrated", action="store_true", help="Include papers already labeled by llm-calibration runs.")
    return parser


def main() -> None:
    """Runs the calibration command and prints artifact locations."""
    parser = build_arg_parser()
    args = parser.parse_args()
    json_path, walkthrough_path = run_calibration(args)
    print(f"Calibration JSON: {json_path}")
    print(f"Walkthrough: {walkthrough_path}")


if __name__ == "__main__":
    main()
