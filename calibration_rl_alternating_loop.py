# calibration_rl_alternating_loop.py
"""Alternating node2a/2b/2c RL cycle state machine until target alignment is met."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import calibration_feedback_agent as cfa
import calibration_metrics
import subnode_field_scopes
from calibration_agent import refresh_maude_batch
from calibration_rl_orchestrator import SUBNODE_TO_MODE

DEFAULT_SEQUENCE = ["node2b", "node2c", "node2a"]
DEFAULT_OFFSET0_EVERY_N_CYCLES = 3
DEFAULT_GATE_MODE = "holdout_field_subset"
TARGETED_FIELD_CANDIDATES = frozenset({
    "treatment_duration",
    "duration_days",
    "sample_size",
    "administration_frequency",
    "inhaled_exposure_duration",
    "cannabis_type",
    "exposure_method",
    "outcome_domain",
    "dose_mg",
})
STATE_FILENAME = "rl_alternating_loop_state.json"


def resolve_state_path(output_dir: Optional[Path] = None) -> Path:
    """Returns the persisted loop state JSON path."""
    from maude_cues import resolve_calibration_output_dir

    base = output_dir or resolve_calibration_output_dir()
    return base / STATE_FILENAME


def load_loop_state(output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Loads alternating RL loop state, creating defaults when missing."""
    path = resolve_state_path(output_dir)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return {
        "sequence": list(DEFAULT_SEQUENCE),
        "next_index": 0,
        "paper_offsets": {"node2a": 10, "node2b": 10, "node2c": 10},
        "papers_per_batch": 10,
        "target_alignment_pct": 85.0,
        "gate_mode": DEFAULT_GATE_MODE,
        "offset0_every_n_cycles": DEFAULT_OFFSET0_EVERY_N_CYCLES,
        "primary_holdout_batches": {},
        "cycles_completed": 0,
        "latest_alignment_pct": {},
        "latest_holdout_alignment_pct": {},
        "latest_offset0_alignment_pct": {},
        "history": [],
        "updated_at": datetime.now().isoformat(),
    }


def save_loop_state(state: Dict[str, Any], output_dir: Optional[Path] = None) -> Path:
    """Persists alternating RL loop state."""
    path = resolve_state_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, default=str)
    return path


def compute_scoped_metrics(batch_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Computes dashboard-aligned alignment/recall and field disagree counts for a batch."""
    target = batch_payload.get("target_subnode") or batch_payload.get("automation_node") or ""
    field_disagrees: Dict[str, int] = {}
    optional_recall_hits = 0
    optional_recall_total = 0
    align_rates: List[float] = []
    recall_rates: List[float] = []
    paper_count = 0

    for result in batch_payload.get("results") or []:
        scored = calibration_metrics.score_paper_rl_metrics(result, target)
        if not scored:
            continue
        paper_count += 1
        if scored.get("alignment_rate") is not None:
            align_rates.append(float(scored["alignment_rate"]))
        if scored.get("maude_recall_rate") is not None:
            recall_rates.append(float(scored["maude_recall_rate"]))
        for field in scored.get("alignment_disagree_fields") or []:
            field_disagrees[field] = field_disagrees.get(field, 0) + 1
        for field, hit in (scored.get("optional_field_recall") or {}).items():
            optional_recall_total += 1
            optional_recall_hits += int(hit)

    top_fields = sorted(field_disagrees.items(), key=lambda item: (-item[1], item[0]))
    alignment_pct = round(100 * sum(align_rates) / len(align_rates), 1) if align_rates else None
    recall_pct = round(100 * sum(recall_rates) / len(recall_rates), 1) if recall_rates else None
    optional_recall_pct = (
        round(100 * optional_recall_hits / optional_recall_total, 1)
        if optional_recall_total
        else None
    )
    return {
        "alignment_pct": alignment_pct,
        "maude_recall_pct": recall_pct,
        "optional_strain_recall_pct": optional_recall_pct,
        "field_disagrees": top_fields,
        "top_disagree_field": top_fields[0][0] if top_fields else None,
        "top_disagree_count": top_fields[0][1] if top_fields else 0,
        "paper_count": paper_count,
        "gate_mode": DEFAULT_GATE_MODE,
    }


def load_batch_json(path: Path) -> Dict[str, Any]:
    """Loads a calibration batch JSON artifact."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def should_run_targeted_pass(
    metrics: Dict[str, Any],
    *,
    min_disagree_count: int = 4,
    min_disagree_pct: float = 35.0,
) -> Optional[str]:
    """Returns a field name when a same-holdout targeted pass is likely beneficial."""
    paper_count = metrics.get("paper_count") or 0
    if paper_count <= 0:
        return None
    top_field = metrics.get("top_disagree_field")
    top_count = metrics.get("top_disagree_count") or 0
    if not top_field or top_field not in TARGETED_FIELD_CANDIDATES:
        return None
    disagree_pct = 100.0 * top_count / paper_count
    if top_count >= min_disagree_count and disagree_pct >= min_disagree_pct:
        return top_field
    return None


def min_node_alignment(state: Dict[str, Any]) -> Optional[float]:
    """Returns the minimum latest alignment % across node2 sub-nodes."""
    latest = _gate_alignment_table(state)
    values = [latest[key] for key in ("node2a", "node2b", "node2c") if latest.get(key) is not None]
    return min(values) if values else None


def _gate_alignment_table(state: Dict[str, Any]) -> Dict[str, float]:
    """Returns the alignment table used for target_met checks."""
    gate_mode = state.get("gate_mode") or DEFAULT_GATE_MODE
    if gate_mode == "holdout_field_subset":
        holdout = state.get("latest_holdout_alignment_pct") or {}
        if holdout:
            return {key: float(value) for key, value in holdout.items() if value is not None}
    latest = state.get("latest_alignment_pct") or {}
    return {key: float(value) for key, value in latest.items() if value is not None}


def holdout_batch_id(state: Dict[str, Any], subnode: str) -> Optional[str]:
    """Returns the fixed holdout batch id for a sub-node, if configured."""
    holdouts = state.get("primary_holdout_batches") or {}
    batch_id = holdouts.get(subnode)
    return str(batch_id) if batch_id else None


def should_run_offset0_batch(state: Dict[str, Any]) -> bool:
    """True when a fresh offset-0 generalization batch should run this cycle."""
    interval = int(state.get("offset0_every_n_cycles") or DEFAULT_OFFSET0_EVERY_N_CYCLES)
    if interval <= 0:
        return False
    cycles = int(state.get("cycles_completed") or 0)
    return cycles > 0 and cycles % interval == 0


def target_met(state: Dict[str, Any]) -> bool:
    """True when all tracked sub-nodes meet the target alignment threshold."""
    target = float(state.get("target_alignment_pct") or 85.0)
    latest = _gate_alignment_table(state)
    for subnode in ("node2a", "node2b", "node2c"):
        value = latest.get(subnode)
        if value is None or value < target:
            return False
    return True


def next_subnode(state: Dict[str, Any]) -> str:
    """Returns the next sub-node id in the alternating sequence."""
    sequence = state.get("sequence") or DEFAULT_SEQUENCE
    index = int(state.get("next_index") or 0) % len(sequence)
    return sequence[index]


def advance_subnode(state: Dict[str, Any]) -> str:
    """Advances the sequence pointer and returns the sub-node that was active."""
    sequence = state.get("sequence") or DEFAULT_SEQUENCE
    index = int(state.get("next_index") or 0) % len(sequence)
    subnode = sequence[index]
    state["next_index"] = (index + 1) % len(sequence)
    return subnode


def current_offset(state: Dict[str, Any], subnode: str) -> int:
    """Returns the paper offset for the next batch on a sub-node."""
    offsets = state.setdefault("paper_offsets", {})
    return int(offsets.get(subnode) or 0)


def bump_offset(state: Dict[str, Any], subnode: str) -> int:
    """Increments the paper offset after a batch and returns the new value."""
    offsets = state.setdefault("paper_offsets", {})
    step = int(state.get("papers_per_batch") or 10)
    offsets[subnode] = int(offsets.get(subnode) or 0) + step
    return offsets[subnode]


def record_cycle_result(
    state: Dict[str, Any],
    *,
    subnode: str,
    batch_id: str,
    offset: int,
    baseline_metrics: Dict[str, Any],
    post_metrics: Optional[Dict[str, Any]] = None,
    targeted_field: Optional[str] = None,
    build_id: Optional[str] = None,
    dashboard_metrics: Optional[Dict[str, Any]] = None,
    is_holdout_gate: bool = True,
) -> None:
    """Appends a cycle summary and updates latest alignment per sub-node."""
    entry = {
        "at": datetime.now().isoformat(),
        "subnode": subnode,
        "batch_id": batch_id,
        "offset": offset,
        "baseline_alignment_pct": baseline_metrics.get("alignment_pct"),
        "baseline_recall_pct": baseline_metrics.get("maude_recall_pct"),
        "post_alignment_pct": (post_metrics or {}).get("alignment_pct"),
        "post_recall_pct": (post_metrics or {}).get("maude_recall_pct"),
        "optional_strain_recall_pct": (post_metrics or {}).get("optional_strain_recall_pct"),
        "targeted_field": targeted_field,
        "build_id": build_id,
        "is_holdout_gate": is_holdout_gate,
    }
    state.setdefault("history", []).append(entry)
    if post_metrics and post_metrics.get("alignment_pct") is not None:
        pct = post_metrics["alignment_pct"]
        if is_holdout_gate:
            state.setdefault("latest_holdout_alignment_pct", {})[subnode] = pct
            state.setdefault("latest_alignment_pct", {})[subnode] = pct
        else:
            state.setdefault("latest_offset0_alignment_pct", {})[subnode] = pct
    if dashboard_metrics and dashboard_metrics.get("alignment_pct") is not None:
        state.setdefault("latest_offset0_alignment_pct", {})[subnode] = dashboard_metrics["alignment_pct"]
    state["cycles_completed"] = int(state.get("cycles_completed") or 0) + 1


def run_fly_batch(
    subnode: str,
    *,
    max_calls: int = 10,
    offset: int = 0,
    app: str = "cannabis-paper-scraper",
    deploy_first: bool = False,
) -> Tuple[str, str]:
    """Runs a PDF Maude A/B batch on Fly and returns remote JSON + walkthrough paths."""
    script = Path(__file__).resolve().parent / "scripts" / "run_subnode_calibration.sh"
    deploy_env = "DEPLOY_FIRST=1" if deploy_first else "DEPLOY_FIRST=0"
    subprocess.run(
        [
            "env",
            f"SUBNODE={subnode}",
            f"MAX_CALLS={max_calls}",
            f"OFFSET={offset}",
            deploy_env,
            "RUN_FEEDBACK=0",
            "PULL_LOCAL=0",
            "bash",
            str(script),
        ],
        check=True,
    )
    proc = subprocess.run(
        [
            "fly", "ssh", "console", "-a", app, "-C",
            f"sh -c 'ls -t /data/calibration_runs/{subnode}_calibration_*.json 2>/dev/null | head -1'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    remote_json = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if not remote_json:
        raise RuntimeError(f"Could not locate remote batch JSON for {subnode}")
    return remote_json, remote_json.replace(".json", "_walkthrough.md")


def pull_remote_batch(remote_path: str, local_dir: Path, app: str = "cannabis-paper-scraper") -> Path:
    """SFTP-pulls a batch JSON from Fly into scratch/calibration_runs."""
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / Path(remote_path).name
    subprocess.run(
        ["fly", "ssh", "sftp", "get", "-a", app, remote_path, str(local_path)],
        check=True,
    )
    return local_path


def local_feedback(batch_path: Path) -> Dict[str, Any]:
    """Runs fast local disagreement analysis (no Claude, no in-cycle refresh)."""
    return cfa.run_feedback_cycle(batch_path, skip_lock=True, local_only=True, skip_refresh=True)


def refresh_holdout(batch_path: Path) -> Path:
    """Re-runs Maude on the same holdout papers after a code patch."""
    json_path, _ = refresh_maude_batch(batch_path)
    return json_path


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI parser for loop utilities."""
    parser = argparse.ArgumentParser(description="Alternating node2 RL loop utilities.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Print loop state and alignment progress.")

    plan = sub.add_parser("plan-next", help="Show the next sub-node, offset, and target gap.")
    plan.add_argument("--output-dir", default=None)

    metrics_p = sub.add_parser("metrics", help="Compute scoped metrics for a batch JSON.")
    metrics_p.add_argument("batch_path")

    targeted_p = sub.add_parser("targeted-check", help="Check if batch warrants a targeted pass.")
    targeted_p.add_argument("batch_path")

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else None

    if args.command == "status":
        state = load_loop_state(output_dir)
        print(json.dumps(state, indent=2))
        print("target_met", target_met(state), "min_alignment", min_node_alignment(state))
        return

    if args.command == "plan-next":
        state = load_loop_state(output_dir)
        subnode = next_subnode(state)
        holdout = holdout_batch_id(state, subnode)
        print(json.dumps({
            "subnode": subnode,
            "offset": current_offset(state, subnode),
            "papers_per_batch": state.get("papers_per_batch"),
            "target_alignment_pct": state.get("target_alignment_pct"),
            "gate_mode": state.get("gate_mode") or DEFAULT_GATE_MODE,
            "holdout_batch_id": holdout,
            "run_offset0_generalization": should_run_offset0_batch(state),
            "offset0_every_n_cycles": state.get("offset0_every_n_cycles") or DEFAULT_OFFSET0_EVERY_N_CYCLES,
            "latest_holdout_alignment_pct": state.get("latest_holdout_alignment_pct"),
            "latest_offset0_alignment_pct": state.get("latest_offset0_alignment_pct"),
            "latest_alignment_pct": state.get("latest_alignment_pct"),
            "target_met": target_met(state),
        }, indent=2))
        return

    if args.command == "metrics":
        payload = load_batch_json(Path(args.batch_path))
        print(json.dumps(compute_scoped_metrics(payload), indent=2))
        return

    if args.command == "targeted-check":
        payload = load_batch_json(Path(args.batch_path))
        metrics = compute_scoped_metrics(payload)
        field = should_run_targeted_pass(metrics)
        print(json.dumps({"metrics": metrics, "targeted_field": field}, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
