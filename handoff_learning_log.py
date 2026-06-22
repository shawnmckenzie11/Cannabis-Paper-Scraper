# handoff_learning_log.py
"""Human-readable log of applied Maude RL handoff patches for the calibration dashboard."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import maude_cues

HANDOFF_LOG_FILENAME = "handoff_learning_log.json"

# Sub-nodes that share PDF/abstract extraction fields from node2 handoffs.
SHARED_EXTRACTION_SUBNODES: Sequence[str] = ("node2a", "node2b", "node2c")
SHARED_EXTRACTION_NODE_IDS: Sequence[str] = (
    "node2a_clinical",
    "node2b_in_vivo",
    "node2c_in_vitro",
)


def resolve_log_path(output_dir: Optional[Path] = None) -> Path:
    """Returns the handoff learning log JSON path under the calibration output directory."""
    base = output_dir or maude_cues.resolve_calibration_output_dir()
    return base / HANDOFF_LOG_FILENAME


def load_handoff_learning_log(output_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads applied handoff entries newest-first for dashboard display."""
    path = resolve_log_path(output_dir)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return []
    entries = payload.get("handoffs") or []
    if not isinstance(entries, list):
        return []
    return sorted(entries, key=lambda row: row.get("applied_at") or "", reverse=True)


def handoff_affects_subnode(entry: Dict[str, Any], subnode_id: str) -> bool:
    """Returns True when a handoff entry applies to the given dashboard sub-node id."""
    if not subnode_id:
        return False
    source = entry.get("source_subnode") or ""
    beneficiaries = entry.get("beneficiary_nodes") or entry.get("beneficiary_subnodes") or []
    return source == subnode_id or subnode_id in beneficiaries


def build_node_learning_timeline(
    subnode_id: str,
    handoffs: Sequence[Dict[str, Any]],
    runs: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Builds a chronological learning timeline for one sub-node (handoffs + batch runs)."""
    events: List[Dict[str, Any]] = []
    for entry in handoffs:
        if not handoff_affects_subnode(entry, subnode_id):
            continue
        events.append({
            "kind": "handoff",
            "occurred_at": entry.get("applied_at"),
            "title": entry.get("summary_title") or entry.get("id") or "Classifier handoff",
            "source_subnode": entry.get("source_subnode"),
            "handoff_id": entry.get("id"),
            "learning_notes": list(entry.get("learning_notes") or []),
        })

    for run in runs or []:
        run_index = run.get("run_index")
        batch_id = run.get("batch_id") or "batch"
        events.append({
            "kind": "batch_run",
            "occurred_at": run.get("created_at"),
            "title": f"RL run {run_index} · {batch_id}" if run_index else batch_id,
            "batch_id": batch_id,
            "alignment_pct": run.get("alignment_pct"),
            "maude_recall_pct": run.get("maude_recall_pct"),
            "learning_notes": list(run.get("learning_notes") or []),
        })

    events.sort(key=lambda row: row.get("occurred_at") or "")
    return events


def append_handoff_entry(
    entry: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Path:
    """Appends one applied-handoff record with 3–5 human-readable learning notes."""
    path = resolve_log_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"handoffs": []}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            payload = {"handoffs": []}
    handoffs = list(payload.get("handoffs") or [])
    normalized = dict(entry)
    normalized.setdefault("applied_at", datetime.now().isoformat())
    notes = normalized.get("learning_notes") or []
    if not isinstance(notes, list) or not (3 <= len(notes) <= 8):
        raise ValueError("handoff entry requires learning_notes: list of 3–8 bullet strings")
    handoffs.append(normalized)
    payload["handoffs"] = handoffs
    payload["updated_at"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def mark_staged_patch_applied(
    staged_patch_path: Path,
    applied_at: Optional[str] = None,
) -> None:
    """Annotates a staged patch JSON file as implemented."""
    if not staged_patch_path.exists():
        return
    try:
        with open(staged_patch_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return
    payload["status"] = "applied"
    payload["applied_at"] = applied_at or datetime.now().isoformat()
    with open(staged_patch_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
