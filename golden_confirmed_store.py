"""Load, save, and filter confirmed golden-dataset papers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_CONFIRMED_PATH = Path("scratch/golden_dataset/golden_confirmed.json")

ROUTING_GROUND_TRUTH_FIELDS: Tuple[str, ...] = (
    "publication_type",
    "study_type",
    "exposure_method",
    "species",
)

JSON_LIST_FIELDS: frozenset = frozenset({
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
})


def _parse_field_value(field: str, value: Any) -> Any:
    """Normalizes a SQLite paper field value for ground_truth comparison."""
    if value is None:
        return value
    if field in JSON_LIST_FIELDS and isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
    return value


def default_store() -> Dict[str, Any]:
    """Returns an empty confirmed-golden store document."""
    return {"version": 1, "papers": []}


def load_confirmed(path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads golden_confirmed.json or returns an empty store if missing."""
    store_path = path or DEFAULT_CONFIRMED_PATH
    if not store_path.exists():
        return default_store()
    with open(store_path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return default_store()
    data.setdefault("version", 1)
    data.setdefault("papers", [])
    return data


def save_confirmed(store: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Writes the confirmed golden store to disk."""
    store_path = path or DEFAULT_CONFIRMED_PATH
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2, ensure_ascii=False)
    return store_path


def _paper_key(paper: Dict[str, Any]) -> Tuple[int, str]:
    """Returns dedupe key for a confirmed paper record."""
    return int(paper.get("paper_id")), str(paper.get("endpoint_id") or "")


def filter_by_scope_subnode(
    papers: Sequence[Dict[str, Any]],
    scope_subnode: str,
) -> List[Dict[str, Any]]:
    """Returns confirmed papers whose scope_subnode matches the given subnode."""
    target = str(scope_subnode or "").strip()
    if not target:
        return list(papers)
    return [
        paper
        for paper in papers
        if str(paper.get("scope_subnode") or "").strip() == target
    ]


def append_papers(
    new_papers: Sequence[Dict[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Appends papers to the confirmed store, deduping on (paper_id, endpoint_id)."""
    store = load_confirmed(path)
    existing = {_paper_key(paper): paper for paper in store.get("papers") or []}
    for paper in new_papers:
        key = _paper_key(paper)
        existing[key] = paper
    store["papers"] = list(existing.values())
    save_confirmed(store, path)
    return store


def build_ground_truth_from_row(
    row: Dict[str, Any],
    scope_fields: Sequence[str],
    *,
    include_routing: bool = True,
) -> Dict[str, Any]:
    """Builds ground_truth dict from a SQLite paper row and endpoint scope."""
    import calibration_metrics

    fields: List[str] = list(scope_fields)
    if include_routing:
        for field in ROUTING_GROUND_TRUTH_FIELDS:
            if field not in fields:
                fields.append(field)

    ground_truth: Dict[str, Any] = {}
    for field in fields:
        value = _parse_field_value(field, row.get(field))
        if calibration_metrics.field_is_populated(value):
            ground_truth[field] = value
    return ground_truth


def replace_endpoint_papers(
    endpoint_id: str,
    new_papers: Sequence[Dict[str, Any]],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Replaces all confirmed papers for one endpoint with a new promoted set."""
    store = load_confirmed(path)
    kept = [
        paper
        for paper in store.get("papers") or []
        if str(paper.get("endpoint_id") or "") != str(endpoint_id)
    ]
    store["papers"] = kept + list(new_papers)
    save_confirmed(store, path)
    return store


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO format."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
