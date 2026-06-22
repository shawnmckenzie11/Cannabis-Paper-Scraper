"""Alignment-calibrated confidence scores for Maude-classified papers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import classification_schema
import handoff_learning_log

# Maps infer_routing_subnode ids to RL sub-node ids used in handoff logs.
ROUTING_TO_RL_SUBNODE: Dict[str, str] = {
    "node2a": "node2a",
    "node2b": "node2b",
    "node2c": "node2c",
    "node2d": "node2d",
    "node1b": "node1",
    "node1c": "node1",
    "node3a": "node1",
    "node3b": "node1",
    "node3c": "node1",
    "node1": "node1",
    "node1a": "node1",
    "node0": "node0",
}

# Fallback alignment % when no handoff metric exists yet for a sub-node.
DEFAULT_ALIGNMENT_PCT: Dict[str, float] = {
    "node2a": 79.4,
    "node2b": 76.3,
    "node2c": 79.9,
    "node2d": 75.0,
    "node1": 70.0,
    "node0": 85.0,
}


def _read_handoff_payload(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Loads the raw handoff learning log JSON payload."""
    path = handoff_learning_log.resolve_log_path(output_dir)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def latest_post_patch_alignment_by_subnode(
    output_dir: Optional[Path] = None,
) -> Dict[str, float]:
    """Returns the most recent post-patch alignment % keyed by RL sub-node id."""
    payload = _read_handoff_payload(output_dir)
    entries = payload.get("handoffs") or []
    latest: Dict[str, float] = {}
    for entry in entries:
        if entry.get("entry_type") == "verification":
            continue
        subnode = entry.get("source_subnode")
        if not subnode:
            beneficiaries = entry.get("beneficiary_nodes") or []
            subnode = beneficiaries[0] if beneficiaries else None
        pct = entry.get("post_patch_alignment_pct")
        if subnode and pct is not None:
            latest[str(subnode)] = float(pct)
    return latest


@lru_cache(maxsize=1)
def cached_alignment_pcts() -> Dict[str, float]:
    """Cached alignment table merged with defaults for all RL sub-nodes."""
    merged = dict(DEFAULT_ALIGNMENT_PCT)
    merged.update(latest_post_patch_alignment_by_subnode())
    node2_vals = [merged[key] for key in ("node2a", "node2b", "node2c") if key in merged]
    if node2_vals:
        merged["node2d"] = round(sum(node2_vals) / len(node2_vals), 1)
    return merged


def routing_subnode_for_classification(extracted: Dict[str, Any]) -> str:
    """Infers the decision-tree sub-node for a normalized Maude classification."""
    return classification_schema.infer_routing_subnode("node1_routing", extracted)


def alignment_pct_for_routing_subnode(routing_subnode: str) -> float:
    """Returns the latest RL alignment % for a routing sub-node id."""
    alignments = cached_alignment_pcts()
    rl_subnode = ROUTING_TO_RL_SUBNODE.get(routing_subnode, routing_subnode)
    if rl_subnode in alignments:
        return alignments[rl_subnode]
    if routing_subnode.startswith("node2"):
        return alignments.get("node2d", DEFAULT_ALIGNMENT_PCT["node2d"])
    return DEFAULT_ALIGNMENT_PCT.get(rl_subnode, DEFAULT_ALIGNMENT_PCT["node1"])


def confidence_for_classification(extracted: Dict[str, Any]) -> float:
    """Maps a Maude classification to confidence using the node's latest RL alignment %."""
    routing_subnode = routing_subnode_for_classification(extracted)
    pct = alignment_pct_for_routing_subnode(routing_subnode)
    return round(max(0.0, min(1.0, pct / 100.0)), 3)


def apply_maude_confidence(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Overwrites classification_confidence on a Maude result using node alignment."""
    extracted["classification_confidence"] = confidence_for_classification(extracted)
    return extracted
