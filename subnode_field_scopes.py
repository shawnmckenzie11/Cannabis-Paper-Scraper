# subnode_field_scopes.py
"""Branch-tailored field scopes for Maude RL calibration (from Cannabis_Classification_Decision_Tree)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

MODE_TO_TARGET_SUBNODE = {
    "node2a_clinical": "node2a",
    "node2b_in_vivo": "node2b",
    "node2c_in_vitro": "node2c",
    "node1_routing": "node1",
}

SUBNODE_TO_MODE = {value: key for key, value in MODE_TO_TARGET_SUBNODE.items()}

SUBNODE_TO_CALIBRATION_LABEL = {
    "node2a": "node2a-calibration",
    "node2b": "node2b-calibration",
    "node2c": "node2c-calibration",
    "node1": "node1-calibration",
}

SUBNODE_TO_BATCH_PREFIX = {
    "node2a": "node2a_calibration",
    "node2b": "node2b_calibration",
    "node2c": "node2c_calibration",
    "node1": "node1_calibration",
}

NODE7_IN_VIVO_FIELDS: Dict[str, List[str]] = {
    "7a": [
        "exposure_method", "cannabis_type", "puff_count", "inhaled_exposure_duration",
        "thc_pct", "cbd_pct", "strain_reported", "thc_mg_ml", "cbd_mg_ml",
        "duration_days", "administration_frequency", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7b": [
        "exposure_method", "cannabis_type", "puff_count", "inhaled_exposure_duration",
        "thc_pct", "cbd_pct", "strain_reported", "thc_mg_ml", "cbd_mg_ml",
        "duration_days", "administration_frequency", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7c": [
        "exposure_method", "cannabis_type", "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g",
        "thc_pct", "cbd_pct", "dose_mg", "duration_days", "administration_frequency",
        "strain_reported", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7d": [
        "exposure_method", "cannabis_type", "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g",
        "thc_pct", "cbd_pct", "dose_mg", "duration_days", "administration_frequency",
        "strain_reported", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7e": [
        "exposure_method", "cannabis_type", "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g",
        "thc_pct", "cbd_pct", "dose_mg", "duration_days", "administration_frequency",
        "strain_reported", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7f": [
        "exposure_method", "cannabis_type", "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g",
        "thc_pct", "cbd_pct", "dose_mg", "duration_days", "administration_frequency",
        "strain_reported", "repeat_exposure_count", "exposure_regimen_bin",
    ],
    "7g": [
        "exposure_method", "cannabis_type", "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g",
        "thc_pct", "cbd_pct", "dose_mg", "duration_days", "administration_frequency",
        "strain_reported", "repeat_exposure_count", "exposure_regimen_bin",
    ],
}

NODE7_IN_VITRO_FIELDS: Dict[str, List[str]] = {
    "7a": [
        "exposure_method", "cannabis_type", "thc_mg_ml", "cbd_mg_ml", "puff_count",
        "thc_pct", "cbd_pct", "strain_reported", "treatment_duration",
        "multiple_doses", "multiple_time_intervals", "outcome_domain",
    ],
    "7b": [
        "exposure_method", "cannabis_type", "thc_mg_ml", "cbd_mg_ml", "puff_count",
        "thc_pct", "cbd_pct", "strain_reported", "inhaled_exposure_duration",
        "repeat_exposure_count", "multiple_doses", "multiple_time_intervals", "outcome_domain",
    ],
    "7c": [
        "exposure_method", "cannabis_type", "thc_uM", "cbd_uM", "thc_mg_ml", "cbd_mg_ml",
        "thc_pct", "cbd_pct", "treatment_duration", "strain_reported",
        "multiple_doses", "multiple_time_intervals", "outcome_domain",
    ],
}

IN_VIVO_EXPOSURE_TO_NODE7 = {
    "whole body. smoke/vapor": "7a",
    "nose only smoke/vapor": "7b",
    "injection cannabinoids": "7c",
    "oral administration": "7d",
    "sub-lingual": "7e",
    "intranasal": "7f",
    "intratracheal": "7g",
    "cannabinoids dissolved in media": "7c",
}

IN_VITRO_EXPOSURE_TO_NODE7 = {
    "smoke/vapor conditioned media": "7a",
    "exposure of cells to smoke/vapor": "7b",
    "cannabinoids dissolved in media": "7c",
}

SUBNODE_FIELD_SCOPES: Dict[str, List[str]] = {
    "node2a": [
        "study_type", "exposure_method", "cannabis_type", "outcome_domain",
        "sample_size", "duration_days", "dose_mg", "strain_reported", "strain_normalized",
        "thc_pct", "cbd_pct", "administration_frequency", "multiple_doses", "multiple_time_intervals",
        "inhaled_exposure_duration", "puff_count",
    ],
    "node2b": [
        "study_type", "species", "exposure_method", "cannabis_type", "outcome_domain",
        "sample_size", "multiple_doses", "multiple_time_intervals",
        "puff_count", "inhaled_exposure_duration", "thc_pct", "cbd_pct", "strain_reported",
        "thc_mg_ml", "cbd_mg_ml", "duration_days", "administration_frequency",
        "repeat_exposure_count", "exposure_regimen_bin",
        "thc_mg_kg", "cbd_mg_kg", "thc_mg_g", "cbd_mg_g", "dose_mg",
    ],
    "node2c": [
        "study_type", "exposure_method", "cannabis_type", "outcome_domain",
        "treatment_duration", "multiple_doses", "multiple_time_intervals",
        "thc_mg_ml", "cbd_mg_ml", "puff_count", "thc_pct", "cbd_pct", "strain_reported",
        "inhaled_exposure_duration", "repeat_exposure_count", "thc_uM", "cbd_uM",
    ],
}

for path_id, fields in NODE7_IN_VIVO_FIELDS.items():
    SUBNODE_FIELD_SCOPES[f"node7_in_vivo.{path_id}"] = fields
for path_id, fields in NODE7_IN_VITRO_FIELDS.items():
    SUBNODE_FIELD_SCOPES[f"node7_in_vitro.{path_id}"] = fields


def _exposure_blob(exposure_method: Any) -> str:
    """Normalizes exposure_method to a lowercase searchable string."""
    if exposure_method is None:
        return ""
    if isinstance(exposure_method, list):
        return " ".join(str(item).lower() for item in exposure_method)
    return str(exposure_method).lower()


def infer_node7_in_vivo_path(exposure_method: Any) -> Optional[str]:
    """Maps in vivo exposure_method value(s) to Node 7 path id (7a–7g)."""
    blob = _exposure_blob(exposure_method)
    if not blob:
        return None
    for label, path_id in IN_VIVO_EXPOSURE_TO_NODE7.items():
        if label.lower() in blob:
            return path_id
    return None


def infer_node7_in_vitro_path(exposure_method: Any) -> Optional[str]:
    """Maps in vitro exposure_method value(s) to Node 7 path id (7a–7c)."""
    blob = _exposure_blob(exposure_method)
    if not blob:
        return None
    for label, path_id in IN_VITRO_EXPOSURE_TO_NODE7.items():
        if label.lower() in blob:
            return path_id
    return None


def resolve_scope_key(target_subnode: str, llm_record: Optional[Dict[str, Any]] = None) -> str:
    """Returns the SUBNODE_FIELD_SCOPES key for a batch result (with Node 7 refinement)."""
    if not llm_record:
        return target_subnode
    exposure = llm_record.get("exposure_method")
    if target_subnode == "node2b":
        path = infer_node7_in_vivo_path(exposure)
        if path:
            return f"node7_in_vivo.{path}"
    if target_subnode == "node2c":
        path = infer_node7_in_vitro_path(exposure)
        if path:
            return f"node7_in_vitro.{path}"
    return target_subnode


def fields_in_scope(target_subnode: str, llm_record: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns ordered unique field names in agreement scope for a sub-node result."""
    scope_key = resolve_scope_key(target_subnode, llm_record)
    fields = SUBNODE_FIELD_SCOPES.get(scope_key) or SUBNODE_FIELD_SCOPES.get(target_subnode) or []
    return list(dict.fromkeys(fields))


def mode_to_target_subnode(mode: str, explicit: Optional[str] = None) -> Optional[str]:
    """Resolves dashboard sub-node id from calibration mode or --target-subnode."""
    if explicit:
        return explicit
    return MODE_TO_TARGET_SUBNODE.get(mode)


def calibration_label_for_subnode(subnode: Optional[str], mode: str) -> str:
    """Returns llm classifier_version label prefix for a sub-node batch."""
    if subnode and subnode in SUBNODE_TO_CALIBRATION_LABEL:
        return SUBNODE_TO_CALIBRATION_LABEL[subnode]
    if mode == "node1_routing":
        return "node1-calibration"
    return "calibration"


def batch_prefix_for_subnode(subnode: Optional[str], mode: str) -> str:
    """Returns JSON artifact filename prefix for a sub-node batch."""
    if subnode and subnode in SUBNODE_TO_BATCH_PREFIX:
        return SUBNODE_TO_BATCH_PREFIX[subnode]
    if mode == "node1_routing":
        return "node1_calibration"
    return "calibration"


def compare_scoped_fields(
    maude: Dict[str, Any],
    llm: Dict[str, Any],
    target_subnode: str,
    field_equal_fn,
    scope_fields: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compares Maude vs LLM on branch-tailored fields only."""
    if scope_fields is None:
        scope_fields = fields_in_scope(target_subnode, llm)
    else:
        scope_fields = list(dict.fromkeys(scope_fields))
    disagreements: Dict[str, Dict[str, Any]] = {}
    agreed: Dict[str, Any] = {}
    for field in scope_fields:
        left = maude.get(field)
        right = llm.get(field)
        if field_equal_fn(left, right):
            agreed[field] = left if left not in (None, "", []) else right
        else:
            disagreements[field] = {"maude": left, "llm": right}
    return {
        "fields": disagreements,
        "agreed_fields": agreed,
        "scoped_field_count": len(scope_fields),
        "disagreement_count": len(disagreements),
        "agreement_rate": round((len(scope_fields) - len(disagreements)) / len(scope_fields), 4)
        if scope_fields
        else None,
        "scope_key": resolve_scope_key(target_subnode, llm),
        "fields_in_scope": scope_fields,
    }
