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
import maude_classifier
import classification_schema


REVIEW_PUBLICATION_TYPES = {"review", "case study"}


def infer_routing_subnode(mode: str, extracted: Dict[str, Any]) -> str:
    """Maps a classification result to a decision-tree sub-node id for dashboard traversal."""
    return classification_schema.infer_routing_subnode(mode, extracted)


CALIBRATION_FIELDS = [
    "ingestion_status", "study_type", "exposure_method", "cannabis_type", "publication_type",
    "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
    "strain_reported", "strain_normalized", "duration_days",
    "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
    "repeat_exposure_count", "exposure_regimen_bin",
    "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
    "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM",
]

# All stored classification fields compared in Maude vs LLM A/B batches.
MAUDE_AB_COMPARE_FIELDS: Tuple[str, ...] = tuple(
    dict.fromkeys([*CALIBRATION_FIELDS, "species"])
)


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


def calibration_field_equal(left: Any, right: Any) -> bool:
    """Returns True when two calibration field values are equivalent for A/B comparison."""
    empty = (None, "", [], {})
    if left in empty and right in empty:
        return True
    if left in empty and right == 0:
        return True
    if right in empty and left == 0:
        return True
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        try:
            left_num = None if left in empty else float(left)
            right_num = None if right in empty else float(right)
            return left_num == right_num
        except (TypeError, ValueError):
            pass
    return classification_schema.compare_field_values(left, right)


def compare_maude_llm_all_fields(
    maude: Dict[str, Any],
    llm: Dict[str, Any],
    title: str = "",
    abstract: str = "",
) -> Dict[str, Any]:
    """Compares all calibration fields plus species between Maude and stored LLM classifications."""
    left = classification_schema.normalize_classification_record(maude, title, abstract)
    right = classification_schema.normalize_classification_record(llm, title, abstract)
    disagreements: Dict[str, Dict[str, Any]] = {}
    agreed: Dict[str, Any] = {}
    for field in MAUDE_AB_COMPARE_FIELDS:
        maude_value = left.get(field)
        llm_value = right.get(field)
        if calibration_field_equal(maude_value, llm_value):
            agreed[field] = maude_value if maude_value not in (None, "", []) else llm_value
        else:
            disagreements[field] = {"maude": maude_value, "llm": llm_value}
    promotion_fields = ("publication_type", "study_type", "ingestion_status")
    promotion_disagreements = {key: value for key, value in disagreements.items() if key in promotion_fields}
    return {
        "fields": disagreements,
        "agreed_fields": agreed,
        "high_level_count": len(disagreements),
        "promotion_field_count": len(promotion_disagreements),
        "flagged_for_review": len(disagreements) > 0,
        "promotion_fields": list(promotion_disagreements.keys()),
        "compare_fields": list(MAUDE_AB_COMPARE_FIELDS),
    }


def maude_output_to_compare_block(maude_out: Dict[str, Any], rules_version: str) -> Dict[str, Any]:
    """Builds a normalized Maude payload for full-field A/B comparison."""
    block = {
        field: maude_out.get(field)
        for field in MAUDE_AB_COMPARE_FIELDS
        if field in maude_out or field in MAUDE_AB_COMPARE_FIELDS
    }
    block["classification_confidence"] = maude_out.get("classification_confidence")
    block["classifier_version"] = f"maude-{rules_version}"
    block["nodes_visited"] = (maude_out.get("_maude_meta") or {}).get("nodes_visited")
    return block


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
    calibration_label: str = "calibration",
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
        where_clauses.append(
            f"(classifier_version IS NULL OR classifier_version NOT LIKE 'llm-{calibration_label}-%')"
        )

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
    elif mode == "node1_routing":
        where_clauses.append(
            """(
                publication_type IN (
                    'review', 'systematic review', 'meta-analysis', 'editorial',
                    'comment', 'letter to the editor', 'perspectives paper', 'case study'
                )
                OR publication_type = 'original research'
                OR publication_type IS NULL
                OR publication_type = ''
            )"""
        )
    elif mode != "mixed":
        raise ValueError(f"Unknown calibration candidate mode: {mode}")

    params.append(fetch_limit)

    if mode == "node1_routing":
        order_clause = """
            CASE
                WHEN publication_type IN ('review', 'systematic review', 'meta-analysis', 'editorial', 'comment', 'letter to the editor', 'perspectives paper')
                THEN 0
                ELSE 1
            END ASC,
            COALESCE(classification_confidence, 0) ASC,
            citation_count DESC,
            date_harvested DESC,
            id DESC
        """
    else:
        order_clause = """
            COALESCE(classification_confidence, 0) ASC,
            citation_count DESC,
            date_harvested DESC,
            id DESC
        """

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
            {order_clause}
        LIMIT ?
    """

    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def select_native_abstract_candidates(
    fetch_limit: int,
    exclude_locked: bool = True,
    offset: int = 0,
    require_no_pdf_link: bool = False,
) -> List[Dict[str, Any]]:
    """Selects papers with native/heuristic abstract classification (non-LLM, non-Maude)."""
    db = DatabaseManager()
    conn = db.get_connection()
    if not db.is_postgres:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    where_clauses = [
        "(abstract IS NOT NULL AND abstract != '')",
        "(classifier_version IS NULL OR (classifier_version NOT LIKE 'llm-%' AND classifier_version NOT LIKE 'maude-%'))",
    ]
    if require_no_pdf_link:
        where_clauses.append("(full_text_link IS NULL OR full_text_link = '')")
    params: List[Any] = []
    if exclude_locked:
        where_clauses.append(
            "(expert_locked_fields IS NULL OR expert_locked_fields = '' OR expert_locked_fields = '[]')"
        )

    params.extend([fetch_limit, offset])
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
            date_harvested, ingestion_status, species
        FROM papers
        WHERE {' AND '.join(where_clauses)}
        ORDER BY citation_count DESC, date_harvested DESC, id ASC
        LIMIT ? OFFSET ?
    """
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def llm_extraction_to_compare_block(
    extracted: Dict[str, Any],
    title: str,
    abstract: str,
    classifier_version: str,
    *,
    full_fields: bool = True,
) -> Dict[str, Any]:
    """Builds a normalized LLM comparison block from a live Claude extraction payload."""
    normalized = classification_schema.normalize_classification_record(extracted, title, abstract)
    compare_fields = MAUDE_AB_COMPARE_FIELDS if full_fields else classification_schema.HIGH_LEVEL_COMPARE_FIELDS
    block = {field: normalized.get(field) for field in compare_fields}
    block["classification_confidence"] = (
        normalized.get("classification_confidence") or extracted.get("classification_confidence")
    )
    block["classifier_version"] = classifier_version
    return block


def native_row_to_compare_block(candidate: Dict[str, Any], title: str, abstract: str) -> Dict[str, Any]:
    """Builds a comparison block from stored native/heuristic classification fields."""
    return paper_row_to_llm_block(candidate, title, abstract, full_fields=True)


def select_llm_pdf_reclassify_candidates(
    fetch_limit: int,
    exclude_locked: bool = True,
    offset: int = 0,
    include_abstract_reclassify: bool = True,
    abstract_reclassify_only: bool = False,
) -> List[Dict[str, Any]]:
    """Selects Claude reclassified papers (PDF and/or abstract) for Maude A/B pairing."""
    db = DatabaseManager()
    conn = db.get_connection()
    if not db.is_postgres:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if abstract_reclassify_only:
        version_clauses = ["classifier_version LIKE 'llm-reclassify-%'"]
    elif include_abstract_reclassify:
        version_clauses = [
            "classifier_version LIKE 'llm-pdf-reclassify-%'",
            "classifier_version LIKE 'llm-reclassify-%'",
        ]
    else:
        version_clauses = ["classifier_version LIKE 'llm-pdf-reclassify-%'"]

    where_clauses = [
        "(abstract IS NOT NULL AND abstract != '')",
        f"({' OR '.join(version_clauses)})",
    ]
    params: List[Any] = []

    if exclude_locked:
        where_clauses.append(
            "(expert_locked_fields IS NULL OR expert_locked_fields = '' OR expert_locked_fields = '[]')"
        )

    params.extend([fetch_limit, offset])
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
            date_harvested, ingestion_status, species
        FROM papers
        WHERE {' AND '.join(where_clauses)}
        ORDER BY id ASC
        LIMIT ? OFFSET ?
    """

    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def paper_row_to_llm_block(
    candidate: Dict[str, Any],
    title: str,
    abstract: str,
    *,
    full_fields: bool = True,
) -> Dict[str, Any]:
    """Builds a normalized LLM comparison block from stored paper classification fields."""
    raw_record: Dict[str, Any] = {
        "publication_type": candidate.get("publication_type"),
        "study_type": parse_json_list(candidate.get("study_type")),
        "exposure_method": parse_json_list(candidate.get("exposure_method")),
        "cannabis_type": parse_json_list(candidate.get("cannabis_type")),
        "outcome_domain": parse_json_list(candidate.get("outcome_domain")),
        "ingestion_status": candidate.get("ingestion_status"),
        "species": candidate.get("species"),
        "classification_confidence": candidate.get("classification_confidence"),
    }
    if full_fields:
        for field in CALIBRATION_FIELDS:
            if field in raw_record:
                continue
            raw_record[field] = candidate.get(field)
    extracted = classification_schema.normalize_classification_record(raw_record, title, abstract)
    block = {
        field: extracted.get(field)
        for field in (MAUDE_AB_COMPARE_FIELDS if full_fields else classification_schema.HIGH_LEVEL_COMPARE_FIELDS)
    }
    block["classification_confidence"] = (
        extracted.get("classification_confidence") or candidate.get("classification_confidence")
    )
    block["classifier_version"] = candidate.get("classifier_version")
    return block


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
    calibration_label: str = "calibration",
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

        classifier_version = f"llm-{calibration_label}-{variant}-{rules_version}"
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


def resolve_calibration_output_dir(explicit: Optional[str] = None) -> Path:
    """Returns the directory for calibration JSON artifacts.

    On Fly.io production (DATABASE_PATH=/data/cannabis_papers.db), defaults to the
    persistent volume at /data/calibration_runs so artifacts survive machine restarts.
    """
    if explicit and explicit != "scratch/calibration_runs":
        return Path(explicit)
    env_dir = os.getenv("CALIBRATION_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    db_path = os.getenv("DATABASE_PATH", "")
    if db_path.startswith("/data/"):
        return Path("/data/calibration_runs")
    return Path(explicit or "scratch/calibration_runs")


def run_calibration(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Runs a bounded calibration cycle and writes JSON plus Markdown walkthrough artifacts."""
    if args.max_calls < 1 or args.max_calls > 100:
        raise ValueError("--max-calls must be between 1 and 100 for this learning pass.")
    if not args.dry_run and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required unless --dry-run is set.")

    variants = [variant.strip() for variant in args.variants.split(",") if variant.strip()]
    if not variants:
        variants = ["control"]

    calibration_label = getattr(args, "calibration_label", None) or (
        "node1-calibration" if args.mode == "node1_routing" else "calibration"
    )
    batch_prefix = "node1_calibration" if args.mode == "node1_routing" else "calibration"

    fetch_limit = max(args.fetch_limit, args.max_calls * max(2, len(variants)))
    candidates = select_candidates(
        mode=args.mode,
        fetch_limit=fetch_limit,
        confidence_max=args.confidence_max,
        require_full_text=args.require_full_text,
        exclude_locked=not args.include_locked,
        exclude_calibrated=not args.include_calibrated,
        calibration_label=calibration_label,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    batch_id = f"{batch_prefix}_{timestamp}"
    rules_version = get_rules_version()
    output_dir = resolve_calibration_output_dir(getattr(args, "output_dir", None))
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

            maude_out = maude_classifier.classify_paper(
                candidate.get("title") or "",
                candidate.get("abstract") or "",
                full_text=None,
                rules_version=rules_version,
            )
            extracted = classification_schema.normalize_classification_record(
                extracted,
                candidate.get("title") or "",
                candidate.get("abstract") or "",
            )
            disagreement = maude_classifier.compare_maude_llm(
                maude_out,
                extracted,
                candidate.get("title") or "",
                candidate.get("abstract") or "",
            )

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
                calibration_label=calibration_label,
            )
            updates_applied += applied

            routing_subnode = infer_routing_subnode(args.mode, extracted)
            record.update({
                "status": "updated" if applied else "no_update",
                "after_confidence": extracted.get("classification_confidence"),
                "after_classifier_version": f"llm-{calibration_label}-{variant}-{rules_version}",
                "after_publication_type": extracted.get("publication_type"),
                "after_study_type": extracted.get("study_type"),
                "routing_subnode": routing_subnode,
                "changes": changes,
                "llm_metrics": extracted.get("_llm_call_metrics", {}),
                "llm": {
                    "publication_type": extracted.get("publication_type"),
                    "study_type": extracted.get("study_type"),
                    "exposure_method": extracted.get("exposure_method"),
                    "cannabis_type": extracted.get("cannabis_type"),
                    "outcome_domain": extracted.get("outcome_domain"),
                    "ingestion_status": extracted.get("ingestion_status"),
                    "species": extracted.get("species"),
                    "classification_confidence": extracted.get("classification_confidence"),
                    "classifier_version": f"llm-{calibration_label}-{variant}-{rules_version}",
                },
                "maude": {
                    "publication_type": maude_out.get("publication_type"),
                    "study_type": maude_out.get("study_type"),
                    "exposure_method": maude_out.get("exposure_method"),
                    "cannabis_type": maude_out.get("cannabis_type"),
                    "outcome_domain": maude_out.get("outcome_domain"),
                    "ingestion_status": maude_out.get("ingestion_status"),
                    "species": maude_out.get("species"),
                    "classification_confidence": maude_out.get("classification_confidence"),
                    "classifier_version": f"maude-{rules_version}",
                    "nodes_visited": (maude_out.get("_maude_meta") or {}).get("nodes_visited"),
                },
                "disagreement": disagreement,
                "flagged_for_review": disagreement.get("flagged_for_review", False),
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
        "automation_node": "node1" if args.mode == "node1_routing" else None,
        "calibration_label": calibration_label,
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


def fetch_paper_text(db: DatabaseManager, paper_id: int) -> Tuple[str, str]:
    """Returns title and abstract for a paper id from the active database."""
    conn = db.get_connection()
    if not db.is_postgres:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT title, abstract FROM papers WHERE id = ?", (paper_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Paper id {paper_id} not found in database.")
        data = dict(row)
        return (data.get("title") or "", data.get("abstract") or "")
    finally:
        conn.close()


def refresh_maude_batch(source_path: Path, output_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """Re-runs Maude on an existing calibration batch; preserves stored LLM outputs."""
    if not source_path.exists():
        raise FileNotFoundError(f"Batch artifact not found: {source_path}")

    with open(source_path, encoding="utf-8") as handle:
        source_payload = json.load(handle)

    source_results = source_payload.get("results") or []
    if not source_results:
        raise ValueError(f"No results in batch artifact: {source_path}")

    db = DatabaseManager()
    rules_version = get_rules_version()
    resolved_output_dir = resolve_calibration_output_dir(str(output_dir) if output_dir else None)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    source_batch_id = source_payload.get("batch_id") or source_path.stem
    batch_prefix = "node1_calibration" if source_payload.get("mode") == "node1_routing" else "calibration"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    batch_id = f"{batch_prefix}_{timestamp}"
    json_path = resolved_output_dir / f"{batch_id}.json"
    walkthrough_path = resolved_output_dir / f"{batch_id}_walkthrough.md"

    refreshed_results: List[Dict[str, Any]] = []
    for record in source_results:
        paper_id = int(record["paper_id"])
        title, abstract = fetch_paper_text(db, paper_id)
        if not title and record.get("title"):
            title = str(record["title"])

        maude_out = maude_classifier.classify_paper(
            title,
            abstract,
            full_text=None,
            rules_version=rules_version,
        )
        llm_block = classification_schema.normalize_classification_record(
            record.get("llm") or {},
            title,
            abstract,
        )
        disagreement = maude_classifier.compare_maude_llm(maude_out, llm_block, title, abstract)

        refreshed = dict(record)
        refreshed.update({
            "title": title or record.get("title"),
            "abstract": abstract,
            "llm": {
                "publication_type": llm_block.get("publication_type"),
                "study_type": llm_block.get("study_type"),
                "exposure_method": llm_block.get("exposure_method"),
                "cannabis_type": llm_block.get("cannabis_type"),
                "outcome_domain": llm_block.get("outcome_domain"),
                "ingestion_status": llm_block.get("ingestion_status"),
                "species": llm_block.get("species"),
                "classification_confidence": llm_block.get("classification_confidence")
                or (record.get("llm") or {}).get("classification_confidence"),
                "classifier_version": (record.get("llm") or {}).get("classifier_version"),
            },
            "routing_subnode": infer_routing_subnode(
                source_payload.get("mode") or "node1_routing",
                llm_block,
            ),
            "maude": {
                "publication_type": maude_out.get("publication_type"),
                "study_type": maude_out.get("study_type"),
                "exposure_method": maude_out.get("exposure_method"),
                "cannabis_type": maude_out.get("cannabis_type"),
                "outcome_domain": maude_out.get("outcome_domain"),
                "ingestion_status": maude_out.get("ingestion_status"),
                "species": maude_out.get("species"),
                "classification_confidence": maude_out.get("classification_confidence"),
                "classifier_version": f"maude-{rules_version}",
                "nodes_visited": (maude_out.get("_maude_meta") or {}).get("nodes_visited"),
            },
            "disagreement": disagreement,
            "flagged_for_review": disagreement.get("flagged_for_review", False),
        })
        refreshed_results.append(refreshed)

    payload = dict(source_payload)
    payload.update({
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "maude_refresh_source_batch": source_batch_id,
        "maude_refresh_only": True,
        "calls_attempted": 0,
        "updates_applied": 0,
        "dry_run": True,
        "results": refreshed_results,
    })

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    write_walkthrough(payload, walkthrough_path)
    return json_path, walkthrough_path


def run_claude_maude_ab_native(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Runs live Claude abstract classification paired with Maude on native (non-LLM) papers without PDF."""
    max_calls = args.max_calls
    if max_calls < 1 or max_calls > 100:
        raise ValueError("--max-calls must be between 1 and 100 for native Claude+Maude A/B.")

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for live Claude+Maude native A/B.")

    offset = max(int(getattr(args, "offset", 0) or 0), 0)
    fetch_limit = max(args.fetch_limit, max_calls)
    candidates = select_native_abstract_candidates(
        fetch_limit=fetch_limit,
        exclude_locked=not args.include_locked,
        offset=offset,
        require_no_pdf_link=getattr(args, "require_no_pdf_link", False),
    )

    rules_version = get_rules_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    batch_id = f"native_claude_maude_ab_{timestamp}"
    output_dir = resolve_calibration_output_dir(getattr(args, "output_dir", None))
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{batch_id}.json"
    walkthrough_path = output_dir / f"{batch_id}_walkthrough.md"

    full_fields = getattr(args, "full_fields", True)
    use_full_extraction = getattr(args, "full_extraction", False)
    variant = (args.variants.split(",")[0].strip() if args.variants else "control") or "control"
    original_variant = os.environ.get("CLASSIFIER_PROMPT_VARIANT")

    results: List[Dict[str, Any]] = []
    paired = 0
    flagged = 0
    calls_attempted = 0
    field_stats: Dict[str, Dict[str, int]] = {
        field: {"agree": 0, "disagree": 0} for field in MAUDE_AB_COMPARE_FIELDS
    }

    try:
        os.environ["CLASSIFIER_PROMPT_VARIANT"] = variant
        for candidate in candidates:
            if paired >= max_calls:
                break

            paper_id = int(candidate["id"])
            title = candidate.get("title") or ""
            abstract = candidate.get("abstract") or ""
            native_block = native_row_to_compare_block(candidate, title, abstract)

            calls_attempted += 1
            extracted = classifier.process_paper_metadata(
                title,
                abstract,
                run_llm=True,
                runs=max(int(getattr(args, "runs", 1) or 1), 1),
                full_text=None,
            )
            if not extracted:
                results.append({
                    "paper_id": paper_id,
                    "pmid": candidate.get("pmid"),
                    "title": title,
                    "variant": variant,
                    "status": "claude_no_extraction",
                    "native": native_block,
                })
                continue

            claude_version = f"llm-native-ab-{variant}-{rules_version}"
            llm_block = llm_extraction_to_compare_block(
                extracted,
                title,
                abstract,
                claude_version,
                full_fields=full_fields,
            )

            maude_out = maude_classifier.classify_paper(
                title,
                abstract,
                full_text=None,
                rules_version=rules_version,
                abstract_only_extraction=not use_full_extraction,
            )
            if full_fields:
                disagreement = compare_maude_llm_all_fields(maude_out, llm_block, title, abstract)
                maude_block = maude_output_to_compare_block(maude_out, rules_version)
            else:
                disagreement = maude_classifier.compare_maude_llm(maude_out, llm_block, title, abstract)
                maude_block = maude_output_to_compare_block(maude_out, rules_version)

            paired += 1
            if disagreement.get("flagged_for_review"):
                flagged += 1
            for field in MAUDE_AB_COMPARE_FIELDS:
                if field in (disagreement.get("fields") or {}):
                    field_stats[field]["disagree"] += 1
                else:
                    field_stats[field]["agree"] += 1

            routing_subnode = infer_routing_subnode("node1_routing", llm_block)
            results.append({
                "paper_id": paper_id,
                "pmid": candidate.get("pmid"),
                "title": title,
                "variant": variant,
                "dry_run": True,
                "locked_fields": parse_json_list(candidate.get("expert_locked_fields")),
                "before_confidence": candidate.get("classification_confidence"),
                "before_classifier_version": candidate.get("classifier_version"),
                "status": "claude_maude_paired",
                "after_confidence": llm_block.get("classification_confidence"),
                "after_classifier_version": claude_version,
                "after_publication_type": llm_block.get("publication_type"),
                "after_study_type": llm_block.get("study_type"),
                "routing_subnode": routing_subnode,
                "changes": {},
                "native": native_block,
                "llm": llm_block,
                "maude": maude_block,
                "llm_metrics": extracted.get("_llm_call_metrics", {}),
                "disagreement": disagreement,
                "flagged_for_review": disagreement.get("flagged_for_review", False),
            })
    finally:
        if original_variant is None:
            os.environ.pop("CLASSIFIER_PROMPT_VARIANT", None)
        else:
            os.environ["CLASSIFIER_PROMPT_VARIANT"] = original_variant

    field_agreement = {
        field: {
            "agree": stats["agree"],
            "disagree": stats["disagree"],
            "agree_pct": round(stats["agree"] / paired * 100, 1) if paired else None,
        }
        for field, stats in field_stats.items()
    }

    payload = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "mode": "native_claude_maude_ab",
        "automation_node": "node1",
        "calibration_label": "native-claude-maude-ab",
        "variants": [variant],
        "max_calls": max_calls,
        "offset": offset,
        "calls_attempted": calls_attempted,
        "planned_candidates": paired,
        "updates_applied": 0,
        "dry_run": True,
        "abstract_only": True,
        "full_fields_compare": full_fields,
        "compare_fields": list(MAUDE_AB_COMPARE_FIELDS),
        "field_agreement": field_agreement,
        "maude_only": False,
        "native_abstract_no_pdf": not getattr(args, "require_no_pdf_link", False),
        "abstract_only_classification": True,
        "candidate_count": len(candidates),
        "paired_count": paired,
        "flagged_for_review_count": flagged,
        "results": results,
    }

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    write_walkthrough(payload, walkthrough_path)
    return json_path, walkthrough_path


def run_maude_ab_from_llm_pdf(args: argparse.Namespace) -> Tuple[Path, Path]:
    """Pairs stored llm-pdf-reclassify classifications with live Maude (no Claude calls or DB writes)."""
    max_calls = args.max_calls
    if max_calls < 1 or max_calls > 1000:
        raise ValueError("--max-calls must be between 1 and 1000 for Maude-only llm-pdf pairing.")

    offset = max(int(getattr(args, "offset", 0) or 0), 0)
    fetch_limit = max(args.fetch_limit, max_calls)
    abstract_reclassify_only = getattr(args, "abstract_reclassify_only", False)
    pdf_reclassify_only = getattr(args, "pdf_reclassify_only", False)
    if abstract_reclassify_only and pdf_reclassify_only:
        raise ValueError("Use only one of --abstract-reclassify-only or --pdf-reclassify-only.")
    candidates = select_llm_pdf_reclassify_candidates(
        fetch_limit=fetch_limit,
        exclude_locked=not args.include_locked,
        offset=offset,
        include_abstract_reclassify=not pdf_reclassify_only,
        abstract_reclassify_only=abstract_reclassify_only,
    )

    rules_version = get_rules_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    batch_id = f"llm_pdf_maude_ab_{timestamp}"
    output_dir = resolve_calibration_output_dir(getattr(args, "output_dir", None))
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{batch_id}.json"
    walkthrough_path = output_dir / f"{batch_id}_walkthrough.md"

    results: List[Dict[str, Any]] = []
    paired = 0
    flagged = 0
    field_stats: Dict[str, Dict[str, int]] = {
        field: {"agree": 0, "disagree": 0} for field in MAUDE_AB_COMPARE_FIELDS
    }
    full_fields = getattr(args, "full_fields", True)
    use_full_extraction = getattr(args, "full_extraction", True)

    for candidate in candidates:
        if paired >= max_calls:
            break

        paper_id = int(candidate["id"])
        title = candidate.get("title") or ""
        abstract = candidate.get("abstract") or ""
        llm_block = paper_row_to_llm_block(candidate, title, abstract, full_fields=full_fields)

        maude_out = maude_classifier.classify_paper(
            title,
            abstract,
            full_text=None,
            rules_version=rules_version,
            abstract_only_extraction=not use_full_extraction,
        )
        if full_fields:
            disagreement = compare_maude_llm_all_fields(maude_out, llm_block, title, abstract)
            maude_block = maude_output_to_compare_block(maude_out, rules_version)
        else:
            disagreement = maude_classifier.compare_maude_llm(maude_out, llm_block, title, abstract)
            maude_block = {
                "publication_type": maude_out.get("publication_type"),
                "study_type": maude_out.get("study_type"),
                "exposure_method": maude_out.get("exposure_method"),
                "cannabis_type": maude_out.get("cannabis_type"),
                "outcome_domain": maude_out.get("outcome_domain"),
                "ingestion_status": maude_out.get("ingestion_status"),
                "species": maude_out.get("species"),
                "classification_confidence": maude_out.get("classification_confidence"),
                "classifier_version": f"maude-{rules_version}",
                "nodes_visited": (maude_out.get("_maude_meta") or {}).get("nodes_visited"),
            }
        routing_subnode = infer_routing_subnode("node1_routing", llm_block)
        paired += 1
        if disagreement.get("flagged_for_review"):
            flagged += 1
        for field in MAUDE_AB_COMPARE_FIELDS:
            if field in (disagreement.get("fields") or {}):
                field_stats[field]["disagree"] += 1
            else:
                field_stats[field]["agree"] += 1

        classifier_version = candidate.get("classifier_version") or ""
        reclassify_variant = (
            "llm-reclassify"
            if str(classifier_version).startswith("llm-reclassify-")
            else "llm-pdf-reclassify"
        )

        results.append({
            "paper_id": paper_id,
            "pmid": candidate.get("pmid"),
            "title": title,
            "variant": reclassify_variant,
            "dry_run": True,
            "locked_fields": parse_json_list(candidate.get("expert_locked_fields")),
            "before_confidence": candidate.get("classification_confidence"),
            "before_classifier_version": candidate.get("classifier_version"),
            "status": "maude_paired",
            "after_confidence": llm_block.get("classification_confidence"),
            "after_classifier_version": candidate.get("classifier_version"),
            "after_publication_type": llm_block.get("publication_type"),
            "after_study_type": llm_block.get("study_type"),
            "routing_subnode": routing_subnode,
            "changes": {},
            "llm": llm_block,
            "maude": maude_block,
            "disagreement": disagreement,
            "flagged_for_review": disagreement.get("flagged_for_review", False),
        })

    field_agreement = {
        field: {
            "agree": stats["agree"],
            "disagree": stats["disagree"],
            "agree_pct": round(stats["agree"] / paired * 100, 1) if paired else None,
        }
        for field, stats in field_stats.items()
    }

    payload = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "mode": "llm_pdf_maude_ab",
        "automation_node": "node1",
        "calibration_label": "llm-reclassify-maude-ab" if abstract_reclassify_only else "llm-pdf-maude-ab",
        "variants": ["llm-reclassify"] if abstract_reclassify_only else (
            ["llm-pdf-reclassify"] if pdf_reclassify_only else ["llm-pdf-reclassify", "llm-reclassify"]
        ),
        "max_calls": max_calls,
        "offset": offset,
        "calls_attempted": 0,
        "planned_candidates": paired,
        "updates_applied": 0,
        "dry_run": True,
        "abstract_only": not use_full_extraction,
        "full_fields_compare": full_fields,
        "compare_fields": list(MAUDE_AB_COMPARE_FIELDS),
        "field_agreement": field_agreement,
        "maude_only": True,
        "maude_from_llm_pdf": True,
        "candidate_count": len(candidates),
        "paired_count": paired,
        "flagged_for_review_count": flagged,
        "results": results,
    }

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
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
        f"- Automation node: `{payload.get('automation_node') or 'n/a'}`\n",
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
    parser.add_argument("--max-calls", type=int, default=50, help="Maximum Claude classification attempts, capped at 100.")
    parser.add_argument("--fetch-limit", type=int, default=100, help="Candidate rows to inspect before applying the call cap.")
    parser.add_argument("--mode", choices=["preclinical_original", "low_confidence", "unclassified", "mixed", "node1_routing"], default="preclinical_original")
    parser.add_argument("--confidence-max", type=float, default=0.6, help="Confidence ceiling for low_confidence mode.")
    parser.add_argument("--variants", default="control,decision_checklist", help="Comma-separated prompt variants.")
    parser.add_argument("--runs", type=int, default=1, help="Self-consistency runs per classification.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for JSON and walkthrough artifacts (default: /data/calibration_runs on Fly, else scratch/calibration_runs).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Select candidates and write artifacts without Claude calls or DB updates.")
    parser.add_argument("--abstract-only", action="store_true", default=True, help="Use title and abstract only for budget-stable calibration.")
    parser.add_argument("--require-full-text", action="store_true", help="Select only papers with a full_text_link.")
    parser.add_argument("--include-locked", action="store_true", help="Include papers with expert-locked fields.")
    parser.add_argument("--include-calibrated", action="store_true", help="Include papers already labeled by llm-calibration runs.")
    parser.add_argument(
        "--refresh-maude-from-batch",
        metavar="BATCH_JSON",
        help="Re-run Maude on papers from an existing batch JSON (no LLM calls or DB writes).",
    )
    parser.add_argument(
        "--maude-from-llm-pdf",
        action="store_true",
        help="Pair stored llm-reclassify / llm-pdf-reclassify classifications with Maude (no Claude calls or DB writes).",
    )
    parser.add_argument(
        "--claude-maude-native",
        action="store_true",
        help="Run live Claude abstract classification vs Maude on native (non-LLM) papers (no DB writes).",
    )
    parser.add_argument(
        "--require-no-pdf-link",
        action="store_true",
        help="With --claude-maude-native, restrict to papers with no full_text_link in DB (default: any native paper, abstract-only run).",
    )
    parser.add_argument(
        "--pdf-reclassify-only",
        action="store_true",
        help="With --maude-from-llm-pdf, include only llm-pdf-reclassify papers (default: PDF + abstract reclassify).",
    )
    parser.add_argument(
        "--abstract-reclassify-only",
        action="store_true",
        help="With --maude-from-llm-pdf, include only llm-reclassify papers (exclude llm-pdf-reclassify).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Row offset when selecting llm-pdf-reclassify candidates (for chunked runs).",
    )
    parser.add_argument(
        "--full-fields",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Compare all calibration fields (default: true). Use --no-full-fields for high-level only.",
    )
    parser.add_argument(
        "--full-extraction",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run Maude with downstream extraction enabled (default: true).",
    )
    return parser


def main() -> None:
    """Runs the calibration command and prints artifact locations."""
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.refresh_maude_from_batch:
        json_path, walkthrough_path = refresh_maude_batch(
            Path(args.refresh_maude_from_batch),
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
    elif args.maude_from_llm_pdf:
        json_path, walkthrough_path = run_maude_ab_from_llm_pdf(args)
    elif args.claude_maude_native:
        json_path, walkthrough_path = run_claude_maude_ab_native(args)
    else:
        json_path, walkthrough_path = run_calibration(args)
    print(f"Calibration JSON: {json_path}")
    print(f"Walkthrough: {walkthrough_path}")


if __name__ == "__main__":
    main()
