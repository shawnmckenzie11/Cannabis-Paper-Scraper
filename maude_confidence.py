"""Alignment-calibrated confidence scores for Maude-classified papers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import classification_schema
import handoff_learning_log
from classification_regression_guard import is_field_empty

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

# Fields that should be filled for original-research Node 2 papers.
_ORIGINAL_FILL_FIELDS: Tuple[str, ...] = (
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
)


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


def real_fill_rate(extracted: Dict[str, Any], fields: Tuple[str, ...] = _ORIGINAL_FILL_FIELDS) -> float:
    """Fraction of fields with real (non-empty, non-unknown) values."""
    if not fields:
        return 0.0
    filled = sum(1 for field in fields if not is_field_empty(extracted.get(field)))
    return filled / float(len(fields))


def _tier_bonus(extracted: Dict[str, Any]) -> float:
    """Small confidence bump when PDF/full-text or methods text was used."""
    version = str(extracted.get("classifier_version") or "").lower()
    meta = extracted.get("_maude_meta") or {}
    bonus = 0.0
    if version.startswith("maude-pdf-") or version.startswith("maude-ft-") or version.startswith("maude-fulltext-"):
        bonus += 0.04
    if meta.get("methods_used"):
        bonus += 0.02
    cue_score = meta.get("cue_score")
    if isinstance(cue_score, (int, float)) and cue_score >= 0.6:
        bonus += 0.03
    elif isinstance(cue_score, (int, float)) and cue_score >= 0.4:
        bonus += 0.015
    return bonus


def _fill_penalty(extracted: Dict[str, Any]) -> float:
    """Penalty when original-research Node 2 fields are empty or unknown."""
    pub = str(extracted.get("publication_type") or "").strip().lower()
    if pub != "original research":
        return 0.0
    penalties = {
        "exposure_method": 0.08,
        "cannabis_type": 0.06,
        "outcome_domain": 0.05,
    }
    total = 0.0
    for field, amount in penalties.items():
        if is_field_empty(extracted.get(field)):
            total += amount
    return total


def confidence_for_classification(extracted: Dict[str, Any]) -> float:
    """Maps a Maude classification to confidence using alignment, fill rate, and cue coverage.

    Base score is the node's latest RL alignment. Original-research papers lose confidence
    when exposure/cannabis/outcome are empty or ``unknown``; PDF/methods/cue strength add
    small bonuses so triage can prefer richer extractions.
    """
    routing_subnode = routing_subnode_for_classification(extracted)
    pct = alignment_pct_for_routing_subnode(routing_subnode)
    base = pct / 100.0
    adjusted = base - _fill_penalty(extracted) + _tier_bonus(extracted)
    return round(max(0.35, min(0.95, adjusted)), 3)


def apply_maude_confidence(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Overwrites classification_confidence on a Maude result using calibrated scoring."""
    extracted["classification_confidence"] = confidence_for_classification(extracted)
    return extracted


def bump_alignment_for_subnode(
    subnode: str,
    delta_pct: float,
    *,
    cap: float = 100.0,
    output_dir: Optional[Path] = None,
) -> Tuple[float, float]:
    """Raises the latest RL alignment % for a sub-node by delta_pct, capped at cap.

    Returns (previous_pct, new_pct). Persists a lightweight handoff log entry so
    ``confidence_for_classification`` picks up the new alignment on next classify.
    """
    rl_subnode = ROUTING_TO_RL_SUBNODE.get(subnode, subnode)
    alignments = dict(cached_alignment_pcts())
    previous = float(alignments.get(rl_subnode, DEFAULT_ALIGNMENT_PCT.get(rl_subnode, 70.0)))
    new_value = round(min(float(cap), previous + float(delta_pct)), 1)

    entry = {
        "entry_type": "manual_edit_alignment",
        "source_subnode": rl_subnode,
        "beneficiary_nodes": [rl_subnode],
        "post_patch_alignment_pct": new_value,
        "summary_title": f"Manual edit alignment bump ({rl_subnode})",
        "learning_notes": [
            f"Expert drawer corrections increased {rl_subnode} alignment from {previous}% to {new_value}%.",
            f"Applied +{delta_pct}% confidence delta from manual edit cycle (cap {cap}%).",
            "Alignment feeds maude_confidence.apply_maude_confidence on subsequent classifications.",
        ],
    }
    handoff_learning_log.append_handoff_entry(entry, output_dir=output_dir)
    cached_alignment_pcts.cache_clear()
    return previous, new_value
