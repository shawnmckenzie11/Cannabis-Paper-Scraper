# calibration_rl_orchestrator.py
"""Orchestrates sub-node Maude RL calibration batches with Claude feedback cycles."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import calibration_coordinator
import calibration_feedback_agent
import calibration_metrics
import classifier
import subnode_field_scopes
from calibration_agent import build_arg_parser, run_calibration


SUBNODE_TO_MODE = {
    "node2a": "node2a_clinical",
    "node2b": "node2b_in_vivo",
    "node2c": "node2c_in_vitro",
}


def load_rl_config() -> Dict[str, Any]:
    """Returns calibration_rl settings."""
    return calibration_feedback_agent.load_rl_config()


def subnode_passes_gate(subnode: str, rules_config: Optional[Dict[str, Any]] = None) -> bool:
    """Returns True when the sub-node has met consecutive-batch promotion readiness."""
    output_dir = calibration_metrics.resolve_calibration_output_dir()
    batches = []
    for path in sorted(output_dir.glob("*_calibration_*.json")):
        if path.name.endswith("_data.json") or "_feedback_report" in path.name:
            continue
        try:
            batches.append(calibration_metrics.load_calibration_batch(path))
        except Exception:
            continue
    readiness = calibration_metrics.build_subnode_promotion_readiness(
        batches,
        rules_config=rules_config or classifier.load_rules_config(),
        target_subnode=subnode,
    )
    subnode_row = (readiness.get("subnodes") or {}).get(subnode) or {}
    return bool(subnode_row.get("promotion_ready"))


def run_preflight() -> None:
    """Runs fly_db_check.py when available (no-op locally if script missing)."""
    script = Path(__file__).resolve().parent / "fly_db_check.py"
    if not script.exists():
        return
    subprocess.run([sys.executable, str(script)], check=False)


def run_orchestrator(args: argparse.Namespace) -> Dict[str, Any]:
    """Runs bounded RL cycles for one dashboard sub-node."""
    subnode = args.subnode
    mode = SUBNODE_TO_MODE.get(subnode)
    if not mode:
        raise ValueError(f"Unsupported sub-node: {subnode}. Use node2a, node2b, or node2c.")

    if args.preflight:
        run_preflight()

    rules_config = classifier.load_rules_config()
    rl_cfg = load_rl_config()
    threshold_pct = float(rl_cfg.get("agreement_threshold_pct") or 90)
    threshold = threshold_pct / 100.0

    cycle_reports = []
    last_batch_path: Optional[Path] = None

    for cycle in range(1, args.max_cycles + 1):
        if subnode_passes_gate(subnode, rules_config):
            return {
                "status": "promotion_ready",
                "subnode": subnode,
                "cycles_run": cycle - 1,
                "cycle_reports": cycle_reports,
            }

        cal_parser = build_arg_parser()
        cal_args = cal_parser.parse_args([
            "--max-calls", str(args.max_calls),
            "--mode", mode,
            "--target-subnode", subnode,
            "--variants", args.variants,
            "--abstract-only",
            "--skip-lock",
            "--lock-owner", f"orchestrator-{subnode}-cycle{cycle}",
        ])
        if args.output_dir:
            cal_args.output_dir = args.output_dir
        if args.include_calibrated:
            cal_args.include_calibrated = True

        calibration_coordinator.acquire_lock(
            "running_batch",
            f"orchestrator-{subnode}-cycle{cycle}",
            subnode=subnode,
        )
        try:
            json_path, walkthrough_path = run_calibration(cal_args)
        finally:
            calibration_coordinator.release_lock()

        last_batch_path = json_path
        output_dir = json_path.parent
        calibration_metrics.build_dashboard(
            output_dir=output_dir,
            rules_path=Path("rules_config.json"),
        )

        readiness = calibration_metrics.build_subnode_promotion_readiness(
            [calibration_metrics.load_calibration_batch(json_path)],
            rules_config=rules_config,
            target_subnode=subnode,
        )
        subnode_row = (readiness.get("subnodes") or {}).get(subnode) or {}
        latest_rate = subnode_row.get("latest_agreement_rate")

        cycle_report: Dict[str, Any] = {
            "cycle": cycle,
            "batch_path": str(json_path),
            "walkthrough_path": str(walkthrough_path),
            "latest_agreement_rate": latest_rate,
            "promotion_ready": bool(subnode_row.get("promotion_ready")),
        }

        if latest_rate is not None and latest_rate >= threshold and not subnode_row.get("promotion_ready"):
            feedback_report = calibration_feedback_agent.run_feedback_cycle(
                json_path,
                output_dir=output_dir,
                skip_lock=False,
            )
            cycle_report["feedback"] = feedback_report
            calibration_metrics.build_dashboard(
                output_dir=output_dir,
                rules_path=Path("rules_config.json"),
            )

        cycle_reports.append(cycle_report)
        if subnode_row.get("promotion_ready"):
            return {
                "status": "promotion_ready",
                "subnode": subnode,
                "cycles_run": cycle,
                "cycle_reports": cycle_reports,
                "last_batch_path": str(last_batch_path) if last_batch_path else None,
            }

    return {
        "status": "max_cycles_reached",
        "subnode": subnode,
        "cycles_run": args.max_cycles,
        "cycle_reports": cycle_reports,
        "last_batch_path": str(last_batch_path) if last_batch_path else None,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds CLI parser for the RL orchestrator."""
    parser = argparse.ArgumentParser(description="Run sub-node Maude RL calibration orchestrator.")
    parser.add_argument("--subnode", required=True, choices=["node2a", "node2b", "node2c"])
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--max-cycles", type=int, default=5)
    parser.add_argument("--variants", default="control")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--include-calibrated", action="store_true")
    parser.add_argument("--preflight", action="store_true", default=True)
    parser.add_argument("--no-preflight", action="store_false", dest="preflight")
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    result = run_orchestrator(args)
    print(result)


if __name__ == "__main__":
    main()
