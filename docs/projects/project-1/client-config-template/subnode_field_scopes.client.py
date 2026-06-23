# subnode_field_scopes.client.py
"""Branch-tailored field scopes for Maude RL calibration — [Client / Lab Name].

Copy to subnode_field_scopes.py after discovery sign-off. Fill SUBNODE_FIELD_SCOPES
from the discovery worksheet (§10.1). Do not copy cannabis field names unless the
client domain genuinely uses the same extraction variables.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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

SUBNODE_FIELD_SCOPES: Dict[str, List[str]] = {
    "node2a": [
        # TODO: client clinical fields — e.g. sample_size, exposure_method, outcome_domain
    ],
    "node2b": [
        # TODO: client in vivo fields — e.g. species, dose_mg_kg, duration_days
    ],
    "node2c": [
        # TODO: client in vitro fields — e.g. treatment_duration, concentration_uM
    ],
}


def resolve_scope_key(target_subnode: str, llm_record: Optional[Dict[str, Any]] = None) -> str:
    """Returns the SUBNODE_FIELD_SCOPES key for a batch result."""
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
