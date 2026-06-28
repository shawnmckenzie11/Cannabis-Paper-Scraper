"""Guards against Maude reclassification wiping rich prior extractions."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import extractor

EXTRACTABLE_PROPERTY_FIELDS: Tuple[str, ...] = (
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
    "strain_reported",
    "sample_size",
    "inhaled_exposure_duration",
    "thc_pct",
    "cbd_pct",
    "thc_mg_ml",
    "cbd_mg_ml",
    "duration_days",
    "administration_frequency",
    "thc_mg_kg",
    "cbd_mg_kg",
    "thc_mg_g",
    "cbd_mg_g",
    "dose_mg",
    "treatment_duration",
)

_LIST_FIELDS = frozenset({
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
})

_PRECLINICAL_TITLE_PATTERNS = (
    r"(?i)\bin vitro\b",
    r"(?i)\bcell line\b",
    r"(?i)\bcultured cells?\b",
    r"(?i)\bbreast cancer cells?\b",
    r"(?i)\btriple.?negative\b",
    r"(?i)\bin mice\b",
    r"(?i)\bin rats?\b",
    r"(?i)\bsmoke exposure\b",
    r"(?i)\bc57bl/?6\b",
    r"(?i)\bsprague.?dawley\b",
    r"(?i)\bμg/ml\b",
    r"(?i)\bµg/ml\b",
    r"(?i)\bug/ml\b",
    r"(?i)\bincubated\b",
    r"(?i)\bin vivo\b",
    r"(?i)\bin silico\b",
)


def tier_aware_fast_enabled() -> bool:
    """Return True when fast pass should skip pdf/full-text tier papers."""
    return os.getenv("REINGEST_TIER_AWARE_FAST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _parse_field_value(value: Any) -> Any:
    """Parse JSON-encoded DB values into native Python types."""
    if value is None:
        return None
    if isinstance(value, (list, dict, int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        return stripped
    return value


def is_field_empty(value: Any) -> bool:
    """Return True when an extractable field has no meaningful content."""
    parsed = _parse_field_value(value)
    if parsed is None:
        return True
    if isinstance(parsed, str):
        lowered = parsed.strip().lower()
        return lowered in {"", "unknown", "unspecified", "—", "-"}
    if isinstance(parsed, list):
        if not parsed:
            return True
        return all(
            str(item).strip().lower() in {"", "unknown", "unspecified"}
            for item in parsed
        )
    return False


def count_extractable_properties(record: Dict[str, Any]) -> int:
    """Count non-empty extractable classification properties on a paper record."""
    count = 0
    for field in EXTRACTABLE_PROPERTY_FIELDS:
        if not is_field_empty(record.get(field)):
            count += 1
    return count


def title_has_explicit_study_cues(title: str, abstract: str = "") -> bool:
    """Return True when title/abstract carry explicit study-design signals."""
    blob = f"{title} {abstract or ''}"
    if any(re.search(pattern, blob) for pattern in _PRECLINICAL_TITLE_PATTERNS):
        return True
    if extractor.keyword_match(blob.lower(), list(extractor.INVITRO_CONTEXT_CUES)):
        return True
    if extractor.keyword_match(blob.lower(), list(extractor.INVIVO_PRIMARY_CUES)):
        return True
    return False


def _study_type_labels(record: Dict[str, Any]) -> List[str]:
    """Return normalized study_type labels from a record."""
    parsed = _parse_field_value(record.get("study_type"))
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def prior_has_multi_label_study_type(prior: Dict[str, Any]) -> bool:
    """Return True when prior study_type has two or more labels."""
    return len(_study_type_labels(prior)) >= 2


def classifier_tier_rank(classifier_version: Optional[str]) -> int:
    """Rank classifier tiers: abstract < pdf < fulltext."""
    version = str(classifier_version or "").lower()
    if version.startswith("maude-ft-") or version.startswith("maude-fulltext-"):
        return 2
    if version.startswith("maude-pdf-"):
        return 1
    if version.startswith("maude-"):
        return 0
    return -1


def _fulltext_tier_versions(rules_version: str) -> Tuple[str, ...]:
    """Return pdf/fulltext classifier_version labels for the active rules version."""
    import calibration_pdf

    _abstract_v, pdf_v, ft_v = calibration_pdf.maude_tier_classifier_versions(rules_version)
    legacy_ft = calibration_pdf.legacy_fulltext_classifier_version(rules_version)
    return (pdf_v, ft_v, legacy_ft)


def should_skip_fast_pass_for_tier(paper: Dict[str, Any], rules_version: str) -> bool:
    """Return True when fast pass should be skipped for a pdf/full-text tier paper."""
    if not tier_aware_fast_enabled():
        return False
    version = str(paper.get("classifier_version") or "")
    return version in set(_fulltext_tier_versions(rules_version))


def _material_property_loss(prior: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """Return True when new classification loses at least two extractable properties."""
    prior_count = count_extractable_properties(prior)
    new_count = count_extractable_properties(new)
    return prior_count >= 2 and new_count < prior_count - 1


def would_regress_classification(
    prior: Dict[str, Any],
    new: Dict[str, Any],
    title: str,
    abstract: str = "",
) -> Tuple[bool, List[str]]:
    """Return whether a write would materially regress classification richness."""
    reasons: List[str] = []
    prior_count = count_extractable_properties(prior)
    new_count = count_extractable_properties(new)

    if prior_count <= 1:
        return False, reasons
    if new_count >= prior_count:
        return False, reasons

    if not _material_property_loss(prior, new):
        return False, reasons

    has_cues = title_has_explicit_study_cues(title, abstract)
    multi_label = prior_has_multi_label_study_type(prior)
    if not has_cues and not multi_label:
        prior_tier = classifier_tier_rank(str(prior.get("classifier_version") or ""))
        if prior_tier <= 0:
            return False, reasons

    if has_cues:
        reasons.append("explicit_title_cues")
    if multi_label:
        reasons.append("multi_label_study_type")
    if classifier_tier_rank(str(new.get("classifier_version") or "")) < classifier_tier_rank(
        str(prior.get("classifier_version") or "")
    ):
        reasons.append("classifier_tier_downgrade")
    reasons.append(f"property_loss:{prior_count}->{new_count}")
    return True, reasons


def _merge_list_field(prior_val: Any, new_val: Any) -> Any:
    """Union list fields, preserving prior labels when new is empty."""
    prior_list = _parse_field_value(prior_val)
    new_list = _parse_field_value(new_val)
    if not isinstance(prior_list, list):
        prior_list = [prior_list] if prior_list not in (None, "") else []
    if not isinstance(new_list, list):
        new_list = [new_list] if new_list not in (None, "") else []

    if is_field_empty(new_val) and prior_list:
        return list(prior_list)
    merged: List[Any] = []
    for item in list(new_list) + list(prior_list):
        text = str(item).strip()
        if text and text not in merged:
            merged.append(item if isinstance(item, str) else text)
    return merged or new_list or prior_list


def merge_regression_safe(
    prior: Dict[str, Any],
    new: Dict[str, Any],
    *,
    title: str = "",
    abstract: str = "",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge new classification with prior record to prevent material regression.

    Returns:
        Tuple of (merged_payload, meta) where meta includes ``merged`` and ``reasons``.
    """
    blocked, reasons = would_regress_classification(prior, new, title, abstract)
    meta: Dict[str, Any] = {"merged": False, "reasons": reasons}
    if not blocked:
        return dict(new), meta

    merged = dict(new)
    for field in EXTRACTABLE_PROPERTY_FIELDS:
        if field in _LIST_FIELDS:
            if is_field_empty(new.get(field)) and not is_field_empty(prior.get(field)):
                merged[field] = _merge_list_field(prior.get(field), new.get(field))
        elif is_field_empty(new.get(field)) and not is_field_empty(prior.get(field)):
            merged[field] = prior.get(field)

    prior_labels = set(_study_type_labels(prior))
    new_labels = set(_study_type_labels(new))
    if prior_labels and (len(new_labels) < len(prior_labels) or is_field_empty(new.get("study_type"))):
        union_labels: List[str] = []
        for label in list(_study_type_labels(new)) + list(_study_type_labels(prior)):
            if label not in union_labels:
                union_labels.append(label)
        if union_labels:
            merged["study_type"] = union_labels

    prior_tier = classifier_tier_rank(str(prior.get("classifier_version") or ""))
    new_tier = classifier_tier_rank(str(new.get("classifier_version") or ""))
    preserved_prior_fields = any(
        not is_field_empty(prior.get(field)) and is_field_empty(new.get(field))
        for field in EXTRACTABLE_PROPERTY_FIELDS
    )
    if preserved_prior_fields and new_tier < prior_tier:
        merged["classifier_version"] = prior.get("classifier_version")

    try:
        merged["summary"] = extractor.generate_heuristic_summary(merged)
    except Exception:
        if prior.get("summary") and is_field_empty(new.get("summary")):
            merged["summary"] = prior.get("summary")

    meta["merged"] = True
    meta["prior_property_count"] = count_extractable_properties(prior)
    meta["new_property_count"] = count_extractable_properties(new)
    meta["merged_property_count"] = count_extractable_properties(merged)
    return merged, meta
