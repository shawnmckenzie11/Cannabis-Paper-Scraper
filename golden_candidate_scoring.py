"""Golden dataset candidate gates, characteristic counts, and ranking."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import calibration_metrics
import golden_dataset_paths

CLINICAL_REQUIRED_GATE_FIELDS = ("population_age", "population_sex")

THC_PCT_FIELDS = ("thc_pct",)
CBD_PCT_FIELDS = ("cbd_pct",)
THC_CONCENTRATION_FIELDS = ("thc_pct", "thc_mg_ml", "thc_mg_g", "thc_mg_kg", "thc_uM")
CBD_CONCENTRATION_FIELDS = ("cbd_pct", "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "cbd_uM")

CLINICAL_SCORED_FIELDS = (
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
    "duration_days",
    "population_age",
    "population_sex",
    *THC_CONCENTRATION_FIELDS,
    *CBD_CONCENTRATION_FIELDS,
)

PRECLINICAL_SCORED_FIELDS = (
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
    "sample_size",
    "duration_days",
    "puff_count",
    "inhaled_exposure_duration",
    "repeat_exposure_count",
    "exposure_regimen_bin",
    "strain_reported",
    "administration_frequency",
    *THC_CONCENTRATION_FIELDS,
    *CBD_CONCENTRATION_FIELDS,
)


def field_populated(paper: Dict[str, Any], field: str) -> bool:
    """Return True when a paper field has a non-empty extracted value."""
    return calibration_metrics.field_is_populated(paper.get(field))


def required_gate_fields_for_endpoint(
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> List[str]:
    """Return hard-required fields that must be populated before clinical selection."""
    if endpoint.branch == "clinical":
        return list(CLINICAL_REQUIRED_GATE_FIELDS)
    return []


def scored_fields_for_endpoint(
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> List[str]:
    """Return fields that contribute to the characteristic count for an endpoint branch."""
    if endpoint.branch == "clinical":
        return list(CLINICAL_SCORED_FIELDS)
    if endpoint.branch in ("in_vivo", "in_vitro"):
        return list(PRECLINICAL_SCORED_FIELDS)
    return list(CLINICAL_SCORED_FIELDS)


def characteristic_count(
    paper: Dict[str, Any],
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> int:
    """Count populated scored fields (one per field; list fields count once)."""
    fields = scored_fields_for_endpoint(endpoint)
    return sum(1 for field in fields if field_populated(paper, field))


SEARCHABLE_INGESTION_STATUSES = frozenset({"relevant"})
NON_SEARCHABLE_INGESTION_STATUSES = frozenset(
    {"tangential", "irrelevant", "not_cannabis_related"}
)


def is_searchable_golden_candidate(paper: Dict[str, Any]) -> bool:
    """Return True when a paper appears in dashboard search (not tangential/review)."""
    if golden_dataset_paths.is_review_paper(paper):
        return False
    status = str(paper.get("ingestion_status") or "").strip().lower()
    if status in NON_SEARCHABLE_INGESTION_STATUSES:
        return False
    return True


def golden_gates_pass(
    paper: Dict[str, Any],
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> bool:
    """Return True when a paper satisfies branch-specific selection gates."""
    if not is_searchable_golden_candidate(paper):
        return False
    if endpoint.branch == "clinical":
        return all(field_populated(paper, field) for field in CLINICAL_REQUIRED_GATE_FIELDS)
    return True


def golden_sort_key(
    paper: Dict[str, Any],
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> Tuple[int, float, int, str]:
    """Return a sort tuple: characteristic count, confidence, citations, title."""
    count = characteristic_count(paper, endpoint)
    confidence = float(paper.get("classification_confidence") or 0)
    citations = int(paper.get("citation_count") or 0)
    title = str(paper.get("title") or "")
    return (count, confidence, citations, title)


def populated_scored_fields(
    paper: Dict[str, Any],
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> Dict[str, Any]:
    """Return populated scored fields for export."""
    populated: Dict[str, Any] = {}
    for field in scored_fields_for_endpoint(endpoint):
        if field_populated(paper, field):
            populated[field] = paper.get(field)
    return populated


def gate_status_for_export(
    paper: Dict[str, Any],
    endpoint: golden_dataset_paths.TreePathEndpoint,
) -> Dict[str, bool]:
    """Return per-gate booleans for HTML/JSON transparency."""
    status: Dict[str, bool] = {
        "golden_gates_met": golden_gates_pass(paper, endpoint),
        "gate_searchable": is_searchable_golden_candidate(paper),
    }
    if endpoint.branch == "clinical":
        for field in CLINICAL_REQUIRED_GATE_FIELDS:
            status[f"gate_{field}"] = field_populated(paper, field)
    return status
