#!/usr/bin/env python3
"""Orchestrate one golden-endpoint row: cycle → guard (with retry hooks) → reingest → push → HTML."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_build import MAUDE_CLASSIFIER_BUILD_ID
from golden_endpoint_status import prior_rows_guard_passed, update_endpoint_status
from scripts.golden_endpoint_cycle import (
    endpoint_block_from_golden,
    load_tree_path_golden,
    sorted_endpoint_ids_from_golden,
)

logger = logging.getLogger(__name__)


def _endpoint_ids_in_row_order() -> list[str]:
    """Return golden table endpoint ids sorted by PDF pool size (desc)."""
    return sorted_endpoint_ids_from_golden(load_tree_path_golden())


def _row_index_for_endpoint(endpoint_id: str, endpoint_ids: list[str]) -> Optional[int]:
    """Return table row index for an endpoint id, or None if not in the golden table."""
    try:
        return endpoint_ids.index(endpoint_id)
    except ValueError:
        return None


def _enforce_prior_rows_guard(
    row_index: int,
    endpoint_ids: list[str],
    *,
    skip_check: bool,
) -> None:
    """Exit if any prior table row has not passed the golden guard."""
    if skip_check:
        return
    ok, blocking_id, message = prior_rows_guard_passed(row_index, endpoint_ids)
    if not ok:
        logger.error(message)
        if blocking_id:
            logger.error("Blocking endpoint: %s", blocking_id)
        raise SystemExit(3)
def _resolve_endpoint_id(
    row_index: Optional[int],
    endpoint_id: Optional[str],
    endpoint_ids: Optional[list[str]] = None,
) -> str:
    """Return endpoint id from explicit id or sorted row index."""
    ids = endpoint_ids or _endpoint_ids_in_row_order()
    if endpoint_id:
        return endpoint_id
    if row_index is None:
        raise ValueError("Provide --row-index or --endpoint-id")
    if row_index < 0 or row_index >= len(ids):
        raise ValueError(f"row_index {row_index} out of range (0..{len(ids) - 1})")
    return ids[row_index]


def _latest_artifact_dir(endpoint_id: str) -> Optional[Path]:
    """Return the newest cycle artifact directory for an endpoint."""
    base = ROOT / "scratch/golden_dataset/cycles" / endpoint_id
    if not base.is_dir():
        return None
    candidates = sorted(base.glob(f"{endpoint_id}_*"), key=lambda p: p.name, reverse=True)
    return candidates[0] if candidates else None


def _run_shell(cmd: list[str], *, env: Optional[Dict[str, str]] = None) -> int:
    """Run a shell command in repo root and return exit code."""
    merged = os.environ.copy()
    if env:
        merged.update(env)
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, env=merged).returncode


def _cycle_report(artifact_dir: Path) -> Dict[str, Any]:
    """Load cycle_report.json from an artifact directory."""
    path = artifact_dir / "cycle_report.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_delegation_bundle(endpoint_id: str, artifact_dir: Path, report: Dict[str, Any]) -> Path:
    """Write a delegation markdown file for calibration-automation when guard blocks."""
    failures = list(artifact_dir.glob("golden_regression_failures_iter_*.json"))
    failures_path = max(failures, key=lambda p: p.name) if failures else None
    batch = next(artifact_dir.glob("golden_disagreement_*.json"), None)
    feedback = next(artifact_dir.glob("*_golden_feedback_report.json"), None)
    staged = list((artifact_dir / "staged_patches").glob("*.json")) if (
        artifact_dir / "staged_patches"
    ).is_dir() else []

    lines = [
        f"# Golden guard delegation — {endpoint_id}",
        "",
        f"**Cycle status:** {report.get('status')}",
        f"**Artifact dir:** `{artifact_dir.relative_to(ROOT)}`",
        f"**Scope subnode:** {report.get('scope_subnode')}",
        "",
        "## Action for calibration-automation",
        "",
        "Implement Maude patch with `GOLDEN_ENDPOINT_CYCLE=1` (no Fly deploy).",
        "Read staged patch + disagreement batch + regression failures.",
        "Bump `calibration_build.py`, add tests, then re-run guard:",
        "",
        "```bash",
        f"GUARD_ONLY=1 ARTIFACT_DIR={artifact_dir.relative_to(ROOT)} \\",
        f"  ENDPOINT_ID={endpoint_id} PULL=0 PUSH=0 \\",
        "  ./scripts/run_golden_endpoint_cycle.sh",
        "```",
        "",
        "## Artifacts",
        "",
    ]
    if batch:
        lines.append(f"- Disagreement batch: `{batch.relative_to(ROOT)}`")
    if feedback:
        lines.append(f"- Claude feedback: `{feedback.relative_to(ROOT)}`")
    if failures_path:
        lines.append(f"- Guard failures: `{failures_path.relative_to(ROOT)}`")
    for path in staged:
        lines.append(f"- Staged patch: `{path.relative_to(ROOT)}`")

    out = artifact_dir / "delegation_for_calibration_automation.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _record_status(endpoint_id: str, report: Dict[str, Any]) -> None:
    """Update golden_endpoint_status.json from a cycle report."""
    stages = report.get("stages") or {}
    guard = stages.get("golden_guard") or {}
    promote = stages.get("promote") or {}
    push = stages.get("push") or {}
    reingest = stages.get("reingest") or {}
    llm = stages.get("llm") or {}
    push_summary = push.get("stdout") or push.get("stdout_tail")
    if isinstance(push_summary, str) and len(push_summary) > 120:
        push_summary = push_summary[-120:].strip()
    update_endpoint_status(
        endpoint_id,
        {
            "status": report.get("status"),
            "cycle_id": report.get("cycle_id"),
            "scope_subnode": report.get("scope_subnode"),
            "guard_passed": guard.get("passed"),
            "batch_alignment_pct": guard.get("batch_alignment_pct"),
            "guard_iterations": guard.get("iterations"),
            "promoted_count": promote.get("promoted_count"),
            "promoted_paper_ids": promote.get("paper_ids"),
            "llm_classifier_version": llm.get("classifier_version"),
            "reingest_paper_ids": reingest.get("paper_ids"),
            "push_summary": push_summary,
            "maude_build_id": MAUDE_CLASSIFIER_BUILD_ID,
        },
    )


def run_phase_cycle(
    endpoint_id: str,
    *,
    row_index: Optional[int],
    pull: bool,
    push: bool,
    use_fly_proxy: bool,
) -> tuple[int, Optional[Path]]:
    """Run LLM → promote → feedback → guard cycle (no reingest/push in this phase)."""
    env = {
        "PULL": "1" if pull else "0",
        "PUSH": "0",
        "LLM": "1",
        "PROMOTE": "1",
        "FEEDBACK": "1",
        "GOLDEN_GUARD": "1",
        "REINGEST": "0",
        "GOLDEN_HANDOFF_CLAUDE": os.getenv("GOLDEN_HANDOFF_CLAUDE", "0"),
        "ANTHROPIC_TIMEOUT_SEC": os.getenv("ANTHROPIC_TIMEOUT_SEC", "600"),
        "ANTHROPIC_MAX_RETRIES": os.getenv("ANTHROPIC_MAX_RETRIES", "5"),
        "ENDPOINT_ID": endpoint_id,
    }
    if row_index is not None:
        env["ROW_INDEX"] = str(row_index)

    script = (
        "scripts/run_golden_endpoint_with_fly_proxy.sh"
        if use_fly_proxy and pull
        else "scripts/run_golden_endpoint_cycle.sh"
    )
    code = _run_shell(["bash", script], env=env)
    if code != 0 and use_fly_proxy and pull:
        logger.warning("Fly proxy cycle failed; retrying with PULL=0")
        env["PULL"] = "0"
        code = _run_shell(["bash", "scripts/run_golden_endpoint_cycle.sh"], env=env)

    artifact_dir = _latest_artifact_dir(endpoint_id)
    return code, artifact_dir


def run_guard_only(endpoint_id: str, artifact_dir: Path) -> int:
    """Re-run golden guard on an existing artifact directory."""
    env = {
        "GUARD_ONLY": "1",
        "ARTIFACT_DIR": str(artifact_dir.relative_to(ROOT)),
        "ENDPOINT_ID": endpoint_id,
        "PULL": "0",
        "PUSH": "0",
    }
    return _run_shell(["bash", "scripts/run_golden_endpoint_cycle.sh"], env=env)


def run_phase_finish(
    endpoint_id: str,
    *,
    row_index: Optional[int],
    push: bool,
    use_fly_proxy: bool,
) -> int:
    """Run reingest (+ optional push) and export HTML."""
    env = {
        "PULL": "0",
        "LLM": "0",
        "PROMOTE": "0",
        "FEEDBACK": "0",
        "GOLDEN_GUARD": "0",
        "REINGEST": "1",
        "PUSH": "1" if push else "0",
        "ENDPOINT_ID": endpoint_id,
    }
    if row_index is not None:
        env["ROW_INDEX"] = str(row_index)
        if int(row_index) >= 3:
            env["GOLDEN_ROW_INDEX"] = str(int(row_index))
    script = (
        "scripts/run_golden_endpoint_with_fly_proxy.sh"
        if use_fly_proxy and push
        else "scripts/run_golden_endpoint_cycle.sh"
    )
    code = _run_shell(["bash", script], env=env)
    if code != 0 and use_fly_proxy and push:
        env["PULL"] = "0"
        code = _run_shell(["bash", "scripts/run_golden_endpoint_cycle.sh"], env=env)
    if code == 0:
        _run_shell(["python3", "scripts/export_golden_table_html.py"])
    return code


def refresh_golden_candidates() -> int:
    """Rebuild tree_path_golden.json from local SQLite so later rows use updated classifications."""
    code = _run_shell(["python3", "scripts/build_golden_dataset.py"])
    if code != 0:
        return code
    return _run_shell(["python3", "scripts/export_golden_table_html.py"])


def run_single_row(
    row_index: int,
    endpoint_ids: list[str],
    *,
    pull: bool,
    push: bool,
    use_fly_proxy: bool,
    skip_prior_guard_check: bool,
) -> int:
    """
    Run one golden table row end-to-end (cycle → finish).

    Returns exit code 0 on success, 2 on golden guard block, 3 if prior rows failed guard.
    """
    endpoint_id = endpoint_ids[row_index]
    _enforce_prior_rows_guard(row_index, endpoint_ids, skip_check=skip_prior_guard_check)

    code, artifact_dir = run_phase_cycle(
        endpoint_id,
        row_index=row_index,
        pull=pull,
        push=False,
        use_fly_proxy=use_fly_proxy,
    )
    if not artifact_dir:
        logger.error("No artifact directory created for row %d (%s)", row_index, endpoint_id)
        return code or 1

    report = _cycle_report(artifact_dir)
    status = report.get("status")
    logger.info("Row %d (%s) phase cycle status: %s", row_index, endpoint_id, status)
    _record_status(endpoint_id, report)

    if status == "blocked_golden_guard":
        delegation = _write_delegation_bundle(endpoint_id, artifact_dir, report)
        logger.error(
            "Guard blocked on row %d. Delegate to calibration-automation then run:\n"
            "  python3 scripts/golden_endpoint_automate_row.py --endpoint-id %s --guard-only\n"
            "  python3 scripts/golden_endpoint_automate_row.py --endpoint-id %s --finish",
            row_index,
            endpoint_id,
            endpoint_id,
        )
        logger.error("Delegation bundle: %s", delegation)
        logger.error("Auto-advance stopped — fix guard before starting the next row.")
        return 2

    if status != "completed":
        return code or 1

    finish_code = run_phase_finish(
        endpoint_id,
        row_index=row_index,
        push=push,
        use_fly_proxy=use_fly_proxy,
    )
    _record_status(endpoint_id, _cycle_report(artifact_dir) or report)
    return finish_code


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Automate one golden-endpoint table row.")
    parser.add_argument("--row-index", type=int, default=None)
    parser.add_argument("--endpoint-id", type=str, default=None)
    parser.add_argument("--guard-only", action="store_true", help="Only re-run guard on latest artifact dir")
    parser.add_argument("--finish", action="store_true", help="Reingest + push + HTML after guard passed")
    parser.add_argument(
        "--auto-advance",
        action="store_true",
        help="Run rows sequentially from --row-index (or 0); stop when guard blocks or a prior row failed guard",
    )
    parser.add_argument(
        "--skip-prior-guard-check",
        action="store_true",
        help="Allow starting a row even when earlier rows have not passed golden guard",
    )
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-fly-proxy", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    endpoint_ids = _endpoint_ids_in_row_order()
    endpoint_id = _resolve_endpoint_id(args.row_index, args.endpoint_id, endpoint_ids)
    row_index = (
        args.row_index
        if args.row_index is not None
        else _row_index_for_endpoint(endpoint_id, endpoint_ids)
    )
    use_proxy = not args.no_fly_proxy
    pull = not args.no_pull
    push = not args.no_push

    if args.auto_advance:
        start_row = args.row_index if args.row_index is not None else 0
        if start_row < 0 or start_row >= len(endpoint_ids):
            raise SystemExit(f"row_index {start_row} out of range (0..{len(endpoint_ids) - 1})")
        for current_row in range(start_row, len(endpoint_ids)):
            logger.info(
                "=== golden row %d / %d: %s ===",
                current_row,
                len(endpoint_ids) - 1,
                endpoint_ids[current_row],
            )
            code = run_single_row(
                current_row,
                endpoint_ids,
                pull=pull,
                push=push,
                use_fly_proxy=use_proxy,
                skip_prior_guard_check=args.skip_prior_guard_check,
            )
            if code != 0:
                raise SystemExit(code)
            if current_row < len(endpoint_ids) - 1:
                logger.info(
                    "Row %d complete — refreshing golden candidate pool before next row",
                    current_row,
                )
                refresh_code = refresh_golden_candidates()
                if refresh_code != 0:
                    raise SystemExit(refresh_code)
                endpoint_ids = _endpoint_ids_in_row_order()
        raise SystemExit(0)

    if args.guard_only:
        artifact_dir = _latest_artifact_dir(endpoint_id)
        if not artifact_dir:
            raise SystemExit(f"No artifact dir for {endpoint_id}")
        code = run_guard_only(endpoint_id, artifact_dir)
        report = _cycle_report(artifact_dir)
        if report:
            _record_status(endpoint_id, report)
        raise SystemExit(code)

    if args.finish:
        code = run_phase_finish(
            endpoint_id,
            row_index=row_index,
            push=push,
            use_fly_proxy=use_proxy,
        )
        artifact_dir = _latest_artifact_dir(endpoint_id)
        if artifact_dir and (artifact_dir / "cycle_report.json").is_file():
            _record_status(endpoint_id, _cycle_report(artifact_dir))
        raise SystemExit(code)

    if row_index is None:
        raise SystemExit(
            "Provide --row-index or --endpoint-id that appears in tree_path_golden.json"
        )

    if not args.skip_prior_guard_check:
        _enforce_prior_rows_guard(row_index, endpoint_ids, skip_check=False)

    code = run_single_row(
        row_index,
        endpoint_ids,
        pull=pull,
        push=push,
        use_fly_proxy=use_proxy,
        skip_prior_guard_check=True,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
