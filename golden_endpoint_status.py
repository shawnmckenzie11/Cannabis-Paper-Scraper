"""Per-endpoint golden RL status for dashboard / HTML table export."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

DEFAULT_STATUS_PATH = Path("scratch/golden_dataset/golden_endpoint_status.json")


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


def update_endpoint_status(
    endpoint_id: str,
    patch: Dict[str, Any],
    *,
    path: Path = DEFAULT_STATUS_PATH,
) -> Dict[str, Any]:
    """Merges one endpoint status record and saves."""
    data = load_status(path)
    current = dict(data["endpoints"].get(endpoint_id) or {})
    current.update(patch)
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
