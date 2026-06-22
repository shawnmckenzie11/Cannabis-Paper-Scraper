# maude_cues.py
"""Single source of truth for Maude decision-tree routing cues (base + dashboard-learned)."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent
RULES_CONFIG_FILE = BASE_DIR / "rules_config.json"
MAUDE_CUES_FILENAME = "maude_cues.json"
LEGACY_LEARNED_CUES_FILENAME = "maude_learned_cues.json"

DEFAULT_METADATA_ROUTING: List[Dict[str, Any]] = [
    {
        "id": "pubmed_meta_analysis_prefix",
        "match": "publication type: meta-analysis",
        "match_field": "abstract",
        "node_id": "node1b_reviews",
        "publication_type": "review",
        "study_type": "meta-analysis",
        "extra_nodes": ["node3b"],
        "score": 0.55,
        "source": "harvest.py PubMed PublicationTypeList",
        "priority": 100,
    },
    {
        "id": "pubmed_review_prefix",
        "match": "publication type: review",
        "match_field": "abstract",
        "node_id": "node1b_reviews",
        "publication_type": "review",
        "study_type": "review",
        "extra_nodes": [],
        "score": 0.5,
        "source": "harvest.py PubMed PublicationTypeList",
        "priority": 90,
    },
]

WEAK_REVIEW_PHRASES = frozenset({"review", "overview", "perspective", "perspectives"})

BLOCKED_REVIEW_LEARNED_CUES = frozenset({
    "cannabis",
    "marijuana",
    "cannabinoid",
    "cannabinoids",
    "cbd",
    "thc",
    "days",
    "day",
    "weeks",
    "week",
    "mg/kg",
    "mg/kg/day",
    "in the present study",
    "we conducted",
    "we conducted a randomised controlled trial",
    "we conducted a randomized controlled trial",
    "we revisit",
    "ed to examine caps",
})

_REVIEW_LEARNED_DOSING_PATTERN = re.compile(
    r"\b(?:days?|weeks?|mg/kg|intraperitoneal|subcutaneous|gavage|injection)\b",
    re.IGNORECASE,
)


def is_valid_review_learned_cue(cue: str) -> bool:
    """Returns False for learned cues that would cause broad original-research misroutes."""
    normalized = (cue or "").strip().lower()
    if not normalized or normalized in BLOCKED_REVIEW_LEARNED_CUES:
        return False
    if normalized in WEAK_REVIEW_PHRASES:
        return True
    if len(normalized) < 4:
        return False
    if _REVIEW_LEARNED_DOSING_PATTERN.search(normalized):
        return False
    return True


def resolve_calibration_output_dir(explicit: Optional[Path] = None) -> Path:
    """Returns the active calibration artifacts directory."""
    if explicit is not None:
        return explicit
    env_dir = os.getenv("CALIBRATION_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("/data/calibration_runs").exists():
        return Path("/data/calibration_runs")
    return BASE_DIR / "scratch" / "calibration_runs"


def resolve_baseline_cues_path() -> Path:
    """Returns the committed repo baseline maude_cues.json path."""
    return BASE_DIR / MAUDE_CUES_FILENAME


def resolve_runtime_cues_path(output_dir: Optional[Path] = None) -> Path:
    """Returns the runtime overlay path (Fly volume or local scratch)."""
    return resolve_calibration_output_dir(output_dir) / MAUDE_CUES_FILENAME


def resolve_legacy_learned_cues_path(output_dir: Optional[Path] = None) -> Path:
    """Returns the legacy learned-cues file path for migration."""
    return resolve_calibration_output_dir(output_dir) / LEGACY_LEARNED_CUES_FILENAME


def load_rules_config() -> Dict[str, Any]:
    """Loads rules_config.json."""
    with open(RULES_CONFIG_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def _empty_store() -> Dict[str, Any]:
    """Returns an empty cue store skeleton."""
    return {
        "version": 2,
        "updated_at": None,
        "description": "Maude routing cues: base phrases, PubMed metadata rules, and dashboard-learned cues.",
        "metadata_routing": deepcopy(DEFAULT_METADATA_ROUTING),
        "nodes": {},
        "resolutions": [],
    }


def bootstrap_nodes_from_rules_config(rules_config: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Copies decision_nodes positive/negative cues into the Maude cue store shape."""
    rules_config = rules_config or load_rules_config()
    nodes: Dict[str, Dict[str, Any]] = {}
    for node_id, node in (rules_config.get("decision_nodes") or {}).items():
        nodes[node_id] = {
            "tree_label": node.get("tree_label"),
            "purpose": node.get("purpose"),
            "positive_cues": list(node.get("positive_cues") or []),
            "negative_cues": list(node.get("negative_cues") or []),
            "learned_cues": [],
        }
    return nodes


def bootstrap_cue_store(rules_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Builds a fresh cue store from rules_config plus default PubMed metadata rules."""
    store = _empty_store()
    store["nodes"] = bootstrap_nodes_from_rules_config(rules_config)
    store["updated_at"] = datetime.now().isoformat()
    return store


def _merge_node(base_node: Dict[str, Any], overlay_node: Dict[str, Any]) -> Dict[str, Any]:
    """Merges runtime node overlay into baseline node definitions."""
    merged = deepcopy(base_node)
    for key in ("tree_label", "purpose"):
        if overlay_node.get(key):
            merged[key] = overlay_node[key]
    for cue_key in ("positive_cues", "negative_cues"):
        base_list = list(merged.get(cue_key) or [])
        overlay_list = list(overlay_node.get(cue_key) or [])
        seen = {str(item).strip().lower() for item in base_list}
        for item in overlay_list:
            normalized = str(item).strip()
            if normalized.lower() not in seen:
                base_list.append(normalized)
                seen.add(normalized.lower())
        merged[cue_key] = base_list
    learned: List[Dict[str, Any]] = list(merged.get("learned_cues") or [])
    seen_learned = {(row.get("cue") or "").lower() for row in learned}
    for row in overlay_node.get("learned_cues") or []:
        cue = (row.get("cue") or "").strip().lower()
        if cue and cue not in seen_learned:
            learned.append(row)
            seen_learned.add(cue)
    merged["learned_cues"] = learned
    return merged


def merge_cue_stores(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Merges runtime overlay cues into the baseline store."""
    merged = deepcopy(base)
    merged["version"] = max(int(base.get("version") or 2), int(overlay.get("version") or 2))
    merged["updated_at"] = overlay.get("updated_at") or base.get("updated_at")

    base_meta = {row.get("id"): row for row in (base.get("metadata_routing") or [])}
    for row in overlay.get("metadata_routing") or []:
        row_id = row.get("id")
        if row_id and row_id in base_meta:
            base_meta[row_id] = {**base_meta[row_id], **row}
        elif row_id:
            base_meta[row_id] = row
    merged["metadata_routing"] = list(base_meta.values())

    merged_nodes = deepcopy(base.get("nodes") or {})
    for node_id, overlay_node in (overlay.get("nodes") or {}).items():
        if node_id in merged_nodes:
            merged_nodes[node_id] = _merge_node(merged_nodes[node_id], overlay_node)
        else:
            merged_nodes[node_id] = overlay_node
    merged["nodes"] = merged_nodes

    resolutions = list(base.get("resolutions") or [])
    seen_keys = {
        (row.get("paper_id"), row.get("batch_id"), row.get("resolved_at"))
        for row in resolutions
    }
    for row in overlay.get("resolutions") or []:
        key = (row.get("paper_id"), row.get("batch_id"), row.get("resolved_at"))
        if key not in seen_keys:
            resolutions.append(row)
            seen_keys.add(key)
    merged["resolutions"] = resolutions
    return merged


def _migrate_legacy_learned_cues(store: Dict[str, Any], legacy_path: Path) -> Dict[str, Any]:
    """Imports cue_updates/resolutions from legacy maude_learned_cues.json."""
    if not legacy_path.exists():
        return store
    try:
        with open(legacy_path, encoding="utf-8") as handle:
            legacy = json.load(handle)
    except Exception:
        return store

    store = deepcopy(store)
    nodes = store.setdefault("nodes", {})
    for update in legacy.get("cue_updates") or []:
        node_id = update.get("node_id")
        cue = (update.get("cue") or "").strip()
        if not node_id or not cue:
            continue
        node = nodes.setdefault(node_id, {"positive_cues": [], "negative_cues": [], "learned_cues": []})
        learned = node.setdefault("learned_cues", [])
        if not any((row.get("cue") or "").lower() == cue.lower() for row in learned):
            learned.append({
                "cue": cue,
                "field": update.get("field"),
                "source": "dashboard",
                "source_paper_id": update.get("source_paper_id"),
                "explanation": update.get("explanation"),
                "added_at": update.get("added_at"),
                "migrated_from": LEGACY_LEARNED_CUES_FILENAME,
            })

    resolutions = store.setdefault("resolutions", [])
    seen = {(r.get("paper_id"), r.get("batch_id"), r.get("resolved_at")) for r in resolutions}
    for row in legacy.get("resolutions") or []:
        key = (row.get("paper_id"), row.get("batch_id"), row.get("resolved_at"))
        if key not in seen:
            resolutions.append({**row, "migrated_from": LEGACY_LEARNED_CUES_FILENAME})
            seen.add(key)
    return store


def load_cue_store(output_dir: Optional[Path] = None, *, persist_migrations: bool = True) -> Dict[str, Any]:
    """Loads merged Maude cues from repo baseline + runtime overlay (+ legacy migration)."""
    baseline_path = resolve_baseline_cues_path()
    runtime_path = resolve_runtime_cues_path(output_dir)
    legacy_path = resolve_legacy_learned_cues_path(output_dir)

    if baseline_path.exists():
        with open(baseline_path, encoding="utf-8") as handle:
            store = json.load(handle)
    else:
        store = bootstrap_cue_store()

    migrated = False
    if legacy_path.exists() and not runtime_path.exists():
        store = _migrate_legacy_learned_cues(store, legacy_path)
        migrated = True

    if runtime_path.exists():
        with open(runtime_path, encoding="utf-8") as handle:
            overlay = json.load(handle)
        store = merge_cue_stores(store, overlay)
    elif migrated and persist_migrations:
        save_cue_store(store, output_dir=output_dir, runtime_only=True)

    return store


def save_cue_store(
    store: Dict[str, Any],
    output_dir: Optional[Path] = None,
    *,
    runtime_only: bool = False,
) -> Path:
    """Persists the cue store. Dashboard updates write runtime overlay only."""
    store = deepcopy(store)
    store["updated_at"] = datetime.now().isoformat()
    target = resolve_runtime_cues_path(output_dir) if runtime_only else resolve_baseline_cues_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)
    return target


def save_runtime_overlay(base_store: Dict[str, Any], output_dir: Optional[Path] = None) -> Path:
    """Writes only runtime-learned overlay (nodes.learned_cues + resolutions) to the volume."""
    overlay = {
        "version": base_store.get("version", 2),
        "updated_at": datetime.now().isoformat(),
        "metadata_routing": base_store.get("metadata_routing") or [],
        "nodes": {},
        "resolutions": base_store.get("resolutions") or [],
    }
    for node_id, node in (base_store.get("nodes") or {}).items():
        learned = node.get("learned_cues") or []
        if learned:
            overlay["nodes"][node_id] = {"learned_cues": learned}
    return save_cue_store(overlay, output_dir=output_dir, runtime_only=True)


def get_node_config(node_id: str, store: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns merged node cue config for a decision-tree node id."""
    store = store if store is not None else load_cue_store()
    return deepcopy((store.get("nodes") or {}).get(node_id) or {})


def get_positive_phrases(node_id: str, store: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns base + learned positive cue phrases for a node."""
    node = get_node_config(node_id, store)
    phrases: List[str] = []
    seen: set = set()
    learned_rows = node.get("learned_cues") or []
    learned_cues = []
    for row in learned_rows:
        cue = row.get("cue")
        if not cue:
            continue
        if node_id == "node1b_reviews" and not is_valid_review_learned_cue(cue):
            continue
        learned_cues.append(cue)
    for source in (node.get("positive_cues") or []) + learned_cues:
        normalized = str(source).strip()
        key = normalized.lower()
        if normalized and key not in seen:
            phrases.append(normalized)
            seen.add(key)
    return phrases


def get_negative_phrases(node_id: str, store: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns negative cue phrases for a node."""
    node = get_node_config(node_id, store)
    return [str(item).strip() for item in (node.get("negative_cues") or []) if str(item).strip()]


def phrases_to_patterns(phrases: Sequence[str]) -> Tuple[str, ...]:
    """Converts plain-text cue phrases into word-boundary regex patterns."""
    patterns: List[str] = []
    seen: set = set()
    for phrase in phrases:
        normalized = str(phrase).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patterns.append(rf"\b{re.escape(normalized)}\b")
    return tuple(patterns)


def get_routing_patterns(
    node_id: str,
    fallback: Sequence[str] = (),
    store: Optional[Dict[str, Any]] = None,
) -> Tuple[str, ...]:
    """Builds regex routing patterns from base cues, learned cues, and fallbacks."""
    phrases = get_positive_phrases(node_id, store)
    patterns = list(phrases_to_patterns(phrases))
    for pattern in fallback:
        if pattern not in patterns:
            patterns.append(pattern)
    return tuple(patterns)


def get_negative_patterns(node_id: str, fallback: Sequence[str] = (), store: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    """Builds regex patterns for negative cues on a node."""
    phrases = get_negative_phrases(node_id, store)
    if phrases:
        return phrases_to_patterns(phrases)
    return tuple(fallback)


def count_pattern_matches(text: str, patterns: Sequence[str]) -> int:
    """Returns how many regex patterns match anywhere in text."""
    if not text:
        return 0
    return sum(1 for pattern in patterns if re.search(pattern, text, re.IGNORECASE))


def count_positive_matches(text: str, node_id: str, store: Optional[Dict[str, Any]] = None) -> int:
    """Counts positive cue pattern hits for a decision-tree node."""
    patterns = get_routing_patterns(node_id, store=store)
    return count_pattern_matches(text, patterns)


def count_negative_matches(text: str, node_id: str, store: Optional[Dict[str, Any]] = None) -> int:
    """Counts negative cue pattern hits for a decision-tree node."""
    patterns = get_negative_patterns(node_id, (), store=store)
    return count_pattern_matches(text, patterns)


def node_cue_matches(text: str, node_id: str, store: Optional[Dict[str, Any]] = None) -> bool:
    """True when a node has positive cue hits and no blocking negative cues."""
    positives = count_positive_matches(text, node_id, store=store)
    if positives == 0:
        return False
    negatives = count_negative_matches(text, node_id, store=store)
    return positives > negatives


def score_node_cues(text: str, node_id: str, store: Optional[Dict[str, Any]] = None) -> int:
    """Returns net cue score (positive hits minus negative hits) for routing."""
    return count_positive_matches(text, node_id, store=store) - count_negative_matches(text, node_id, store=store)


def get_metadata_routing_rules(store: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Returns PubMed/metadata routing rules sorted by priority (highest first)."""
    store = store if store is not None else load_cue_store()
    rules = list(store.get("metadata_routing") or DEFAULT_METADATA_ROUTING)
    return sorted(rules, key=lambda row: int(row.get("priority") or 0), reverse=True)


def apply_learned_cue(
    node_id: str,
    cue: str,
    field: str,
    paper_id: int,
    explanation: str,
    store: Optional[Dict[str, Any]] = None,
    *,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Appends a dashboard-learned cue to the store and persists runtime overlay."""
    store = deepcopy(store if store is not None else load_cue_store(output_dir))
    cue = cue.strip().lower()
    if not cue:
        return store
    if node_id == "node1b_reviews" and not is_valid_review_learned_cue(cue):
        return store
    node = store.setdefault("nodes", {}).setdefault(
        node_id,
        {"positive_cues": [], "negative_cues": [], "learned_cues": []},
    )
    learned = node.setdefault("learned_cues", [])
    if any((row.get("cue") or "").lower() == cue for row in learned):
        return store
    learned.append({
        "cue": cue,
        "field": field,
        "source": "dashboard",
        "source_paper_id": paper_id,
        "explanation": explanation,
        "added_at": datetime.now().isoformat(),
    })
    save_runtime_overlay(store, output_dir=output_dir)
    return store


def append_resolution(record: Dict[str, Any], store: Optional[Dict[str, Any]] = None, *, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Appends an expert resolution record and persists runtime overlay."""
    store = deepcopy(store if store is not None else load_cue_store(output_dir))
    store.setdefault("resolutions", []).append(record)
    save_runtime_overlay(store, output_dir=output_dir)
    return store


def list_all_learned_cues(store: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Flattens learned cues across all nodes for dashboard display."""
    store = store if store is not None else load_cue_store()
    rows: List[Dict[str, Any]] = []
    for node_id, node in (store.get("nodes") or {}).items():
        for row in node.get("learned_cues") or []:
            rows.append({"node_id": node_id, **row})
    return rows


def write_baseline_from_rules_config(path: Optional[Path] = None) -> Path:
    """Writes repo baseline maude_cues.json from rules_config decision_nodes."""
    store = bootstrap_cue_store()
    target = path or resolve_baseline_cues_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)
    return target


# Backward-compatible aliases used by maude_feedback / calibration_metrics
def resolve_learned_cues_path(output_dir: Optional[Path] = None) -> Path:
    """Deprecated alias — returns runtime maude_cues.json path."""
    return resolve_runtime_cues_path(output_dir)


def load_learned_cues_store(path: Optional[Path] = None) -> Dict[str, Any]:
    """Deprecated alias — returns cue store in legacy {cue_updates, resolutions} shape for metrics."""
    output_dir = path.parent if path else None
    store = load_cue_store(output_dir)
    cue_updates: List[Dict[str, Any]] = []
    for node_id, node in (store.get("nodes") or {}).items():
        for row in node.get("learned_cues") or []:
            cue_updates.append({
                "node_id": node_id,
                "field": row.get("field"),
                "cue": row.get("cue"),
                "source_paper_id": row.get("source_paper_id"),
                "explanation": row.get("explanation"),
                "added_at": row.get("added_at"),
            })
    return {
        "version": store.get("version", 2),
        "cue_updates": cue_updates,
        "resolutions": store.get("resolutions") or [],
        "_full_store": store,
    }


def save_learned_cues_store(store: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Deprecated alias — persists via unified maude_cues runtime overlay."""
    output_dir = path.parent if path else None
    full_store = store.get("_full_store") or store
    return save_runtime_overlay(full_store, output_dir=output_dir)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrap or inspect Maude cue store.")
    parser.add_argument("--bootstrap", action="store_true", help="Write repo baseline maude_cues.json from rules_config.")
    parser.add_argument("--print", action="store_true", help="Print merged cue store JSON.")
    args = parser.parse_args()
    if args.bootstrap:
        written = write_baseline_from_rules_config()
        print(f"Wrote baseline cues: {written}")
    elif args.print:
        print(json.dumps(load_cue_store(), indent=2))
    else:
        parser.print_help()
