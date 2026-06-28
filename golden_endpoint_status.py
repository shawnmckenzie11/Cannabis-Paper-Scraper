"""Per-endpoint golden RL status for dashboard / HTML table export."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from patch_blast_radius import local_report_file_url

DEFAULT_STATUS_PATH = Path("scratch/golden_dataset/golden_endpoint_status.json")
CYCLES_ROOT = Path("scratch/golden_dataset/cycles")
FLY_PUSH_LOG = Path("scratch/local_reingest_cycle.log")

RL_TABLE_FIELDS = (
    "status",
    "guard_passed",
    "batch_alignment_pct",
    "promoted_count",
    "promoted_paper_ids",
    "reingest_paper_ids",
    "push_summary",
    "delta_count",
    "maude_build_id",
    "llm_classifier_version",
    "papers_scanned",
    "papers_changed",
    "papers_pushed",
    "blast_radius_report_path",
    "cohort_validation",
)


def load_status(path: Path = DEFAULT_STATUS_PATH) -> Dict[str, Any]:
    """Loads golden endpoint status JSON (empty shell if missing)."""
    if not path.is_file():
        return {"endpoints": {}, "updated_at": None}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("endpoints", {})
    return data


def save_status(data: Dict[str, Any], path: Path = DEFAULT_STATUS_PATH) -> None:
    """Writes golden endpoint status JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def _push_summary_from_stage(push: Dict[str, Any]) -> Optional[str]:
    """Extract a short push summary string from a cycle push stage dict."""
    if not push:
        return None
    raw = push.get("stdout") or push.get("stdout_tail") or ""
    if isinstance(raw, str) and raw.strip():
        summary = raw.strip()
        if len(summary) > 120:
            summary = summary[-120:].strip()
        return summary
    delta_count = push.get("delta_count")
    if delta_count is not None:
        return f"{int(delta_count)} deltas applied (Fly SSH)"
    return None


def _delta_count_from_push_summary(summary: Optional[str]) -> Optional[int]:
    """Parse an integer delta count from a push summary string."""
    if not summary:
        return None
    match = re.search(r"(\d+)\s+deltas?\s+applied", summary, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"applied\s+(\d+)\s+of\s+\d+\s+delta", summary, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def patch_from_cycle_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Build a status patch from a golden cycle report (omit unset fields)."""
    if not report:
        return {}

    stages = report.get("stages") or {}
    guard = stages.get("golden_guard") or {}
    promote = stages.get("promote") or {}
    push = stages.get("push") or {}
    reingest = stages.get("reingest") or {}
    llm = stages.get("llm") or {}

    patch: Dict[str, Any] = {}
    for key in ("status", "cycle_id", "scope_subnode"):
        value = report.get(key)
        if value is not None:
            patch[key] = value
    if "passed" in guard:
        patch["guard_passed"] = guard.get("passed")
    if guard.get("batch_alignment_pct") is not None:
        patch["batch_alignment_pct"] = guard.get("batch_alignment_pct")
    if guard.get("iterations") is not None:
        patch["guard_iterations"] = guard.get("iterations")
    if promote.get("promoted_count") is not None:
        patch["promoted_count"] = promote.get("promoted_count")
    if promote.get("paper_ids"):
        patch["promoted_paper_ids"] = promote.get("paper_ids")
    if llm.get("classifier_version"):
        patch["llm_classifier_version"] = llm.get("classifier_version")
    if reingest.get("paper_ids"):
        patch["reingest_paper_ids"] = reingest.get("paper_ids")

    push_summary = _push_summary_from_stage(push)
    if push_summary:
        patch["push_summary"] = push_summary
        delta_count = _delta_count_from_push_summary(push_summary)
        if delta_count is not None:
            patch["delta_count"] = delta_count

    return patch


def patch_from_guard_regression(
    regression: Dict[str, Any],
    *,
    iterations: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a status patch from a golden regression JSON artifact."""
    if not regression:
        return {}
    patch: Dict[str, Any] = {}
    if "passed" in regression:
        patch["guard_passed"] = regression.get("passed")
    if regression.get("batch_alignment_pct") is not None:
        patch["batch_alignment_pct"] = regression.get("batch_alignment_pct")
    if iterations is not None:
        patch["guard_iterations"] = iterations
    if regression.get("passed"):
        patch["status"] = "completed"
    elif regression.get("passed") is False:
        patch["status"] = "blocked_golden_guard"
    return patch


def parse_fly_push_summary_from_log(log_path: Path = FLY_PUSH_LOG) -> Optional[str]:
    """Return the most recent Fly SSH delta push summary from the push log."""
    if not log_path.is_file():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = list(re.finditer(r"Done: applied (\d+) of (\d+) delta\(s\)", text))
    if not matches:
        return None
    applied = matches[-1].group(1)
    return f"{applied} deltas applied (Fly SSH)"


def status_patch_from_blast_radius(blast_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build golden endpoint status patch from blast-radius finish payload."""
    if not blast_payload:
        return {}
    cohort = blast_payload.get("cohort_validation") or {}
    report_paths = blast_payload.get("report_paths") or {}
    html_path = report_paths.get("html_relative") or report_paths.get("html") or report_paths.get("markdown")
    patch: Dict[str, Any] = {
        "papers_scanned": blast_payload.get("papers_scanned"),
        "papers_changed": blast_payload.get("papers_changed"),
        "papers_pushed": blast_payload.get("papers_pushed"),
        "subnode_field_changes": blast_payload.get("field_change_counts"),
        "subnode_reingest_scope": blast_payload.get("scope_subnode"),
        "subnode_reingest_papers_processed": blast_payload.get("papers_scanned"),
        "subnode_reingest_papers_written": blast_payload.get("papers_written"),
        "subnode_reingest_full": blast_payload.get("full_subnode"),
        "blast_radius_report_path": local_report_file_url(html_path) if html_path else None,
        "cohort_validation": cohort if cohort else None,
    }
    if cohort.get("cohort_routing_delta") is not None:
        patch["cohort_routing_delta"] = cohort.get("cohort_routing_delta")
    return {key: value for key, value in patch.items() if value is not None}


def missing_rl_table_fields(record: Dict[str, Any]) -> List[str]:
    """Return RL summary column names that are empty for this endpoint record."""
    if not record:
        return list(RL_TABLE_FIELDS)

    gaps: List[str] = []
    status = record.get("status")
    if not status:
        gaps.append("status")

    if status in {"completed", "blocked_golden_guard"}:
        if record.get("guard_passed") is not True:
            gaps.append("guard_passed")
        if record.get("batch_alignment_pct") is None:
            gaps.append("guard_align")
        if record.get("promoted_count") is None:
            gaps.append("promoted")
        if status == "completed":
            if not record.get("reingest_paper_ids") and not record.get("papers_scanned"):
                gaps.append("reingest")
            if not record.get("push_summary") and record.get("delta_count") is None and record.get("papers_pushed") is None:
                gaps.append("push/deltas")
            if not record.get("blast_radius_report_path"):
                gaps.append("blast_radius")
            if not record.get("papers_scanned"):
                gaps.append("scanned")
            if record.get("papers_changed") is None:
                gaps.append("changed")
            if record.get("endpoint_id") and not record.get("cohort_validation"):
                gaps.append("cohort")
        if not record.get("maude_build_id"):
            gaps.append("maude_build")

    return gaps


def reconcile_endpoint_from_artifacts(
    endpoint_id: str,
    *,
    cycles_root: Path = CYCLES_ROOT,
    fly_push_log: Path = FLY_PUSH_LOG,
) -> Dict[str, Any]:
    """Rebuild merged status fields from on-disk cycle artifacts."""
    base = cycles_root / endpoint_id
    if not base.is_dir():
        return {}

    merged: Dict[str, Any] = {}
    artifact_dirs = sorted(base.glob(f"{endpoint_id}_*"), key=lambda path: path.name)
    for artifact_dir in artifact_dirs:
        report_path = artifact_dir / "cycle_report.json"
        if report_path.is_file():
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            merged.update(patch_from_cycle_report(report))

        regression_files = sorted(artifact_dir.glob("golden_regression_iter_*.json"))
        if regression_files:
            latest = regression_files[-1]
            with open(latest, encoding="utf-8") as handle:
                regression = json.load(handle)
            attempt_match = re.search(r"_iter_(\d+)\.json$", latest.name)
            iterations = int(attempt_match.group(1)) if attempt_match else None
            reg_patch = patch_from_guard_regression(regression, iterations=iterations)
            if reg_patch.get("guard_passed") is True or merged.get("guard_passed") is not True:
                merged.update(reg_patch)

    if not merged.get("push_summary"):
        for artifact_dir in reversed(artifact_dirs):
            report_path = artifact_dir / "cycle_report.json"
            if not report_path.is_file():
                continue
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            push_patch = patch_from_cycle_report(report)
            if push_patch.get("push_summary"):
                merged.update(push_patch)
                break

    return merged


def reconcile_all_endpoint_status(
    endpoint_ids: Sequence[str],
    *,
    path: Path = DEFAULT_STATUS_PATH,
    cycles_root: Path = CYCLES_ROOT,
) -> Dict[str, Any]:
    """Merge artifact-derived patches into golden_endpoint_status.json."""
    data = load_status(path)
    for endpoint_id in endpoint_ids:
        artifact_patch = reconcile_endpoint_from_artifacts(
            endpoint_id,
            cycles_root=cycles_root,
        )
        if not artifact_patch:
            continue
        current = dict(data["endpoints"].get(endpoint_id) or {})
        for key, value in artifact_patch.items():
            if value is None:
                continue
            current[key] = value
        current["endpoint_id"] = endpoint_id
        data["endpoints"][endpoint_id] = current
    save_status(data, path)
    return data


def update_endpoint_status(
    endpoint_id: str,
    patch: Dict[str, Any],
    *,
    path: Path = DEFAULT_STATUS_PATH,
) -> Dict[str, Any]:
    """Merges one endpoint status record and saves (None values do not overwrite)."""
    data = load_status(path)
    current = dict(data["endpoints"].get(endpoint_id) or {})
    for key, value in patch.items():
        if key in ("endpoint_id", "updated_at"):
            continue
        if value is None:
            continue
        current[key] = value
    if patch.get("push_summary"):
        delta_count = _delta_count_from_push_summary(str(patch["push_summary"]))
        if delta_count is not None:
            current["delta_count"] = delta_count
    current["endpoint_id"] = endpoint_id
    current["updated_at"] = datetime.utcnow().isoformat() + "Z"
    data["endpoints"][endpoint_id] = current
    save_status(data, path)
    return current


def status_for_endpoint(
    endpoint_id: str,
    *,
    path: Path = DEFAULT_STATUS_PATH,
) -> Dict[str, Any]:
    """Returns status dict for one endpoint (empty if unknown)."""
    return dict(load_status(path).get("endpoints", {}).get(endpoint_id) or {})


def prior_rows_guard_passed(
    row_index: int,
    endpoint_ids: Sequence[str],
    *,
    path: Path = DEFAULT_STATUS_PATH,
) -> tuple[bool, Optional[str], str]:
    """
    Return whether every table row before ``row_index`` has passed the golden guard.

    Rows are ordered by ``endpoint_ids`` (same order as ``sorted_endpoint_ids_from_golden``).
  """
    if row_index <= 0:
        return True, None, ""
    if row_index > len(endpoint_ids):
        return False, None, f"row_index {row_index} out of range (0..{len(endpoint_ids) - 1})"

    data = load_status(path)
    endpoints = data.get("endpoints") or {}
    for prior_index in range(row_index):
        endpoint_id = endpoint_ids[prior_index]
        record = endpoints.get(endpoint_id) or {}
        if record.get("guard_passed") is True:
            continue
        status = record.get("status")
        alignment = record.get("batch_alignment_pct")
        message = (
            f"Row {prior_index} ({endpoint_id}) has not passed golden guard "
            f"(status={status!r}, guard_passed={record.get('guard_passed')!r}, "
            f"alignment={alignment}). "
            f"Resolve row {prior_index} before starting row {row_index}."
        )
        return False, endpoint_id, message
    return True, None, ""
