"""Shared publication_type / study_type / ingestion_status taxonomy for LLM and Maude."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import extractor

PUBLICATION_TYPES: Tuple[str, ...] = ("original research", "review", "case study")

REVIEW_STUDY_SUBTYPES: Tuple[str, ...] = (
    "review",
    "systematic review",
    "meta-analysis",
    "editorial",
    "comment",
    "letter to the editor",
    "perspectives paper",
)

INGESTION_STATUS_VALUES: Tuple[str, ...] = (
    "relevant",
    "tangential",
    "irrelevant",
    "not_cannabis_related",
)

GRANULAR_REVIEW_LABELS: Tuple[str, ...] = REVIEW_STUDY_SUBTYPES + ("not cannabis-related",)

HIGH_LEVEL_COMPARE_FIELDS: Tuple[str, ...] = (
    "ingestion_status",
    "publication_type",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
)

DURATION_FIELD_NAMES: Tuple[str, ...] = (
    "duration_days",
    "inhaled_exposure_duration",
    "administration_frequency",
    "treatment_duration",
    "repeat_exposure_count",
    "exposure_regimen_bin",
)

IN_VIVO_SMOKE_EXPOSURES: Tuple[str, ...] = (
    "nose only smoke/vapor",
    "whole body. smoke/vapor",
)

IN_VIVO_SYSTEMIC_EXPOSURES: Tuple[str, ...] = (
    "injection cannabinoids",
    "oral administration",
    "sub-lingual",
    "intranasal",
    "intratracheal",
)

IN_VITRO_CONDITIONED_MEDIA = "smoke/vapor conditioned media"
IN_VITRO_DIRECT_SMOKE = "exposure of cells to smoke/vapor"
IN_VITRO_DISSOLVED = "cannabinoids dissolved in media"


def _clean_text(value: Any) -> str:
    """Returns a normalized lowercase string for label comparisons."""
    return str(value or "").strip().lower()


def granular_label_to_coarse_publication(granular: str) -> Optional[str]:
    """Maps legacy granular publication labels to coarse Node 1 types."""
    label = _clean_text(granular)
    if not label or label in {"not cannabis-related", "not_cannabis_related"}:
        return None
    if label == "case study":
        return "case study"
    if label in REVIEW_STUDY_SUBTYPES or label in {"commentary", "perspectives"}:
        return "review"
    if label == "original research":
        return "original research"
    return "original research"


def granular_label_to_review_subtype(granular: str) -> Optional[str]:
    """Maps legacy granular labels to review study_type subtypes."""
    label = _clean_text(granular)
    if label == "commentary":
        return "comment"
    if label == "perspectives":
        return "perspectives paper"
    if label in REVIEW_STUDY_SUBTYPES:
        return label
    return None


def infer_review_study_subtype(title: str, abstract: str) -> Optional[str]:
    """Infers review study_type subtype from title/abstract cues."""
    granular = extractor.infer_granular_publication_label(title, abstract)
    return granular_label_to_review_subtype(granular)


def infer_ingestion_status(title: str, abstract: str, publication_type: Optional[str] = None) -> str:
    """Assigns Node 0 ingestion_status from relevance and tangential markers."""
    is_related, reason = extractor.is_cannabis_related(title, abstract)
    if not is_related:
        if "GPR" in reason or "LPI" in reason or "negative pattern" in reason:
            return "not_cannabis_related"
        return "irrelevant"
    pub = _clean_text(publication_type)
    if pub in {"not cannabis-related", "not_cannabis_related"}:
        return "not_cannabis_related"
    combined = f"{title} {abstract}".lower()
    tangential_markers = ("hemp fiber", "agricultural yield", "textile", "legal", "policy")
    if any(marker in combined for marker in tangential_markers):
        return "tangential"
    return "relevant"


def normalize_exposure_method_list(values: Any) -> List[str]:
    """Normalizes exposure_method to a deduplicated list of strings."""
    if values is None:
        return []
    if isinstance(values, str):
        stripped = values.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                import json

                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    values = parsed
                else:
                    values = [stripped]
            except Exception:
                values = [stripped]
        else:
            values = [stripped] if stripped else []
    if not isinstance(values, list):
        return []
    ordered: List[str] = []
    seen: set = set()
    for item in values:
        label = str(item).strip()
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        ordered.append(label)
    return ordered


def normalize_study_type_list(values: Any) -> List[str]:
    """Normalizes study_type to a deduplicated list of strings."""
    if values is None:
        return []
    if isinstance(values, str):
        stripped = values.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                import json

                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    values = parsed
                else:
                    values = [stripped]
            except Exception:
                values = [stripped]
        else:
            values = [stripped] if stripped else []
    if not isinstance(values, list):
        return []
    ordered: List[str] = []
    seen: set = set()
    for item in values:
        label = str(item).strip()
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        ordered.append(label)
    return ordered


def normalize_classification_record(
    record: Dict[str, Any],
    title: str = "",
    abstract: str = "",
) -> Dict[str, Any]:
    """Normalizes publication_type, study_type, and ingestion_status on a classifier payload."""
    normalized = dict(record)
    raw_pub = normalized.get("publication_type")
    pub = _clean_text(raw_pub)
    review_subtype = granular_label_to_review_subtype(pub) if pub else None
    coarse_pub = granular_label_to_coarse_publication(pub) if pub else None

    study_types = normalize_study_type_list(normalized.get("study_type"))
    promoted_subtypes: List[str] = []
    remaining_design_types: List[str] = []
    for item in study_types:
        subtype = granular_label_to_review_subtype(item)
        if subtype:
            promoted_subtypes.append(subtype)
        else:
            remaining_design_types.append(item)
    if review_subtype and review_subtype not in promoted_subtypes:
        promoted_subtypes.insert(0, review_subtype)

    if coarse_pub == "review":
        normalized["publication_type"] = "review"
        normalized["study_type"] = promoted_subtypes or ["review"]
    elif coarse_pub == "case study":
        normalized["publication_type"] = "case study"
        normalized["study_type"] = ["case study"]
    elif coarse_pub == "original research":
        normalized["publication_type"] = "original research"
        normalized["study_type"] = remaining_design_types or study_types
    elif pub in {"not cannabis-related", "not_cannabis_related"}:
        normalized["publication_type"] = None
        normalized["study_type"] = []
    elif coarse_pub:
        normalized["publication_type"] = coarse_pub
        normalized["study_type"] = study_types
    else:
        normalized["publication_type"] = coarse_pub
        normalized["study_type"] = study_types

    ingestion = normalized.get("ingestion_status")
    if ingestion:
        normalized["ingestion_status"] = str(ingestion).strip()
    else:
        normalized["ingestion_status"] = infer_ingestion_status(
            title,
            abstract,
            normalized.get("publication_type"),
        )
    if normalized["ingestion_status"] == "not_cannabis_related":
        normalized["publication_type"] = None
        normalized["study_type"] = []
        normalized["exposure_method"] = []
        normalized["cannabis_type"] = []
    elif normalized.get("publication_type") == "original research":
        normalized = apply_duration_by_exposure_method(normalized)
    return normalized


def _clear_duration_fields(record: Dict[str, Any], keep: Sequence[str]) -> None:
    """Nulls duration fields on a record except those listed in keep."""
    keep_set = {field for field in keep}
    for field in DURATION_FIELD_NAMES:
        if field not in keep_set:
            record[field] = None


def _resolve_invitro_duration_route(exposures: Sequence[str]) -> Optional[str]:
    """Returns the in vitro duration routing bucket for the primary exposure method."""
    return extractor.resolve_invitro_duration_route(list(exposures))


def apply_duration_by_exposure_method(record: Dict[str, Any]) -> Dict[str, Any]:
    """Routes duration fields by exposure_method per the decision-tree in vivo/in vitro appendix."""
    routed = dict(record)
    study_types = normalize_study_type_list(routed.get("study_type"))
    exposures = normalize_exposure_method_list(routed.get("exposure_method"))

    is_clinical = any(item.startswith("Clinical (") for item in study_types)
    is_invivo = any(item.startswith("Animal Models (") for item in study_types)
    is_invitro = any(
        "cell culture" in item.lower() or "vitro" in item.lower() or "organoid" in item.lower()
        for item in study_types
    )

    if is_clinical:
        _clear_duration_fields(
            routed,
            keep=("duration_days", "inhaled_exposure_duration", "administration_frequency"),
        )
        return routed

    exposure_set = set(exposures)

    if is_invitro:
        route = _resolve_invitro_duration_route(exposures)
        if route == "conditioned_media":
            _clear_duration_fields(routed, keep=("treatment_duration",))
        elif route == "direct_smoke":
            _clear_duration_fields(routed, keep=("inhaled_exposure_duration", "repeat_exposure_count"))
        elif route == "dissolved":
            _clear_duration_fields(routed, keep=("treatment_duration",))
        return routed

    if is_invivo:
        smoke_hits = [item for item in IN_VIVO_SMOKE_EXPOSURES if item in exposure_set]
        systemic_hits = [item for item in IN_VIVO_SYSTEMIC_EXPOSURES if item in exposure_set]
        if smoke_hits:
            routed["exposure_regimen_bin"] = extractor.infer_exposure_regimen_bin(
                routed.get("duration_days"),
                routed.get("repeat_exposure_count"),
            )
            _clear_duration_fields(
                routed,
                keep=(
                    "inhaled_exposure_duration",
                    "administration_frequency",
                    "duration_days",
                    "repeat_exposure_count",
                    "exposure_regimen_bin",
                ),
            )
        elif systemic_hits:
            _clear_duration_fields(routed, keep=("administration_frequency", "duration_days"))
        return routed

    return routed


def compare_field_values(left: Any, right: Any) -> bool:
    """Returns True when two high-level field values are equivalent after normalization."""
    if isinstance(left, list) or isinstance(right, list):
        left_list = normalize_study_type_list(left)
        right_list = normalize_study_type_list(right)
        left_set = {item.lower() for item in left_list}
        right_set = {item.lower() for item in right_list}
        if left_set == right_set:
            return True
        if not left_set and not right_set:
            return True
        # Multi-label study_type: partial extraction counts as agreement when one list
        # is a subset of the other (LLM may include all applicable design labels).
        if left_set and right_set and (left_set.issubset(right_set) or right_set.issubset(left_set)):
            return True
        return False
    if left in (None, "", []) and right in (None, "", []):
        return True
    return _clean_text(left) == _clean_text(right)


def compare_classifiers(
    maude: Dict[str, Any],
    llm: Dict[str, Any],
    title: str = "",
    abstract: str = "",
) -> Dict[str, Any]:
    """Compares normalized high-level fields between Maude and LLM payloads."""
    left = normalize_classification_record(maude, title, abstract)
    right = normalize_classification_record(llm, title, abstract)
    disagreements: Dict[str, Dict[str, Any]] = {}
    agreed: Dict[str, Any] = {}
    for field in HIGH_LEVEL_COMPARE_FIELDS:
        maude_value = left.get(field)
        llm_value = right.get(field)
        if compare_field_values(maude_value, llm_value):
            agreed[field] = maude_value if maude_value not in (None, "", []) else llm_value
        else:
            disagreements[field] = {"maude": maude_value, "llm": llm_value}
    promotion_fields = ("publication_type", "study_type")
    promotion_disagreements = {key: value for key, value in disagreements.items() if key in promotion_fields}
    return {
        "fields": disagreements,
        "agreed_fields": agreed,
        "high_level_count": len(disagreements),
        "promotion_field_count": len(promotion_disagreements),
        "flagged_for_review": len(disagreements) > 0,
        "promotion_fields": list(promotion_disagreements.keys()),
    }


def infer_routing_subnode(mode: str, extracted: Dict[str, Any]) -> str:
    """Maps a normalized classification result to a decision-tree sub-node id."""
    normalized = normalize_classification_record(extracted)
    pub = _clean_text(normalized.get("publication_type"))
    study_types = normalize_study_type_list(normalized.get("study_type"))
    study_blob = " ".join(item.lower() for item in study_types)

    if mode == "node1_routing":
        if pub == "review":
            if "systematic review" in study_blob:
                return "node3a"
            if "meta-analysis" in study_blob:
                return "node3b"
            if any(token in study_blob for token in ("editorial", "comment", "letter to the editor", "perspectives paper")):
                return "node3c"
            return "node1b"
        if pub == "case study":
            return "node1c"
        if pub == "original research":
            if any(token in study_blob for token in ("clinical", "rct", "prospective", "retrospective", "observational")):
                return "node2a"
            if any(token in study_blob for token in ("animal", "mouse", "rat", "rodent", "in vivo")):
                return "node2b"
            if any(token in study_blob for token in ("cell culture", "vitro", "organoid")):
                return "node2c"
            return "node2d"
        if normalized.get("ingestion_status") == "not_cannabis_related":
            return "node0"
        return "node1"

    if mode == "preclinical_original":
        if any(token in study_blob for token in ("cell culture", "vitro", "organoid")):
            return "node2c"
        if any(token in study_blob for token in ("animal", "mouse", "rat", "rodent", "in vivo")):
            return "node2b"
        return "node2d"

    if mode == "unclassified":
        return "node0"
    if mode == "low_confidence":
        return "crosscut"
    return "mixed"
