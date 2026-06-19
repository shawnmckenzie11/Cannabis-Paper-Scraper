# maude_feedback.py
"""Expert resolution of Maude vs LLM disagreements with cue learning and feedback loop."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import maude_classifier
import classification_schema

BASE_DIR = Path(__file__).resolve().parent
RULES_CONFIG_FILE = BASE_DIR / "rules_config.json"
DEFAULT_OUTPUT_DIR = Path(
    os.getenv("CALIBRATION_OUTPUT_DIR")
    or ("/data/calibration_runs" if Path("/data/calibration_runs").exists() else "scratch/calibration_runs")
)
LEARNED_CUES_FILENAME = "maude_learned_cues.json"

FIELD_TO_NODE: Dict[str, str] = {
    "ingestion_status": "node0_ingestion",
    "publication_type": "node1b_reviews",
    "study_type": "node1b_reviews",
    "exposure_method": "node1a_original",
    "cannabis_type": "node1a_original",
    "outcome_domain": "node1a_original",
    "species": "node2b_in_vivo",
}


def resolve_learned_cues_path(output_dir: Optional[Path] = None) -> Path:
    """Returns the path for persisted Maude learned cues and resolutions."""
    return (output_dir or DEFAULT_OUTPUT_DIR) / LEARNED_CUES_FILENAME


def load_learned_cues_store(path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads learned cue updates and expert resolutions from disk."""
    store_path = path or resolve_learned_cues_path()
    if not store_path.exists():
        return {"version": 1, "cue_updates": [], "resolutions": []}
    with open(store_path, encoding="utf-8") as handle:
        return json.load(handle)


def save_learned_cues_store(store: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persists learned cue updates and expert resolutions to disk."""
    store_path = path or resolve_learned_cues_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)
    return store_path


def load_rules_config() -> Dict[str, Any]:
    """Loads rules_config.json."""
    with open(RULES_CONFIG_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def merged_decision_nodes(rules_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns decision_nodes merged with learned positive cues."""
    rules_config = rules_config or load_rules_config()
    nodes = deepcopy(rules_config.get("decision_nodes") or {})
    store = load_learned_cues_store()
    for update in store.get("cue_updates") or []:
        node_id = update.get("node_id")
        cue = update.get("cue")
        if not node_id or not cue or node_id not in nodes:
            continue
        positive = nodes[node_id].setdefault("positive_cues", [])
        if cue not in positive:
            positive.append(cue)
    return nodes


def extract_cue_from_explanation(explanation: str, abstract: str = "") -> Optional[str]:
    """Extracts a short phrase cue from an expert explanation and optional abstract."""
    explanation = (explanation or "").strip()
    if not explanation:
        return None

    quoted = re.findall(r"['\"]([^'\"]{3,80})['\"]", explanation)
    if quoted:
        return quoted[0].strip().lower()

    lowered = explanation.lower()
    abstract_lower = (abstract or "").lower()
    candidate_phrases = [
        "overview paper",
        "this overview",
        "narrative review",
        "systematic review",
        "meta-analysis",
        "scoping review",
        "we review",
        "this review",
        "case report",
        "case series",
        "editorial",
        "commentary",
        "letter to the editor",
    ]
    for phrase in candidate_phrases:
        if phrase in lowered or phrase in abstract_lower:
            return phrase

    tokens = re.findall(r"[a-z][a-z0-9\- ]{2,40}", lowered)
    for token in tokens:
        if token in abstract_lower and len(token.split()) <= 4:
            return token.strip()
    return None


def infer_node_for_field(field: str, resolved_value: Any, routing_subnode: Optional[str] = None) -> str:
    """Maps a disagreement field to the decision node that should receive a learned cue."""
    if routing_subnode == "node1a" or str(resolved_value).lower() == "original research":
        if field == "publication_type":
            return "node1b_reviews"
    if field == "publication_type" and str(resolved_value).lower() in {
        "review",
        "systematic review",
        "meta-analysis",
        "editorial",
        "comment",
        "letter to the editor",
        "perspectives paper",
        "case study",
    }:
        return "node1b_reviews"
    return FIELD_TO_NODE.get(field, "node1b_reviews")


def serialize_value(value: Any) -> str:
    """Serializes a field value for feedback_audit storage."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def find_batch_result(
    batch_payloads: Sequence[Dict[str, Any]],
    batch_id: str,
    paper_id: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Finds a calibration batch payload and paper result by ids."""
    for payload in batch_payloads:
        if payload.get("batch_id") != batch_id:
            continue
        for result in payload.get("results") or []:
            if int(result.get("paper_id") or 0) == int(paper_id):
                return payload, result
    return None, None


def load_calibration_batches(output_dir: Path) -> List[Dict[str, Any]]:
    """Loads all calibration batch JSON payloads from an output directory."""
    batches: List[Dict[str, Any]] = []
    if not output_dir.exists():
        return batches
    for path in sorted(output_dir.glob("*.json")):
        if path.name.endswith("_data.json") or path.name == LEARNED_CUES_FILENAME:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["artifact_path"] = str(path)
            batches.append(payload)
        except Exception:
            continue
    return batches


def save_batch_payload(payload: Dict[str, Any]) -> None:
    """Writes an updated calibration batch payload back to its artifact path."""
    artifact_path = payload.get("artifact_path")
    if not artifact_path:
        raise ValueError("Batch payload is missing artifact_path")
    serializable = {key: value for key, value in payload.items() if key != "artifact_path"}
    with open(artifact_path, "w", encoding="utf-8") as handle:
        json.dump(serializable, handle, indent=2)


def apply_cue_update(
    node_id: str,
    cue: str,
    field: str,
    paper_id: int,
    explanation: str,
    store: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Appends a learned cue update if it is not already recorded."""
    store = store if store is not None else load_learned_cues_store()
    cue = cue.strip().lower()
    if not cue:
        return store
    existing = {
        (row.get("node_id"), row.get("cue"))
        for row in (store.get("cue_updates") or [])
    }
    if (node_id, cue) in existing:
        return store
    store.setdefault("cue_updates", []).append({
        "node_id": node_id,
        "field": field,
        "cue": cue,
        "source_paper_id": paper_id,
        "explanation": explanation,
        "added_at": datetime.now().isoformat(),
    })
    return store


def resolve_disagreement(
    paper_id: int,
    batch_id: str,
    field_resolutions: Sequence[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    db=None,
) -> Dict[str, Any]:
    """Resolves Maude vs LLM disagreements for one paper and updates learned cues + feedback loop."""
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not field_resolutions:
        raise ValueError("At least one field resolution is required")

    batch_payloads = load_calibration_batches(output_dir)
    payload, result = find_batch_result(batch_payloads, batch_id, paper_id)
    if result is None:
        raise ValueError(f"Paper {paper_id} not found in batch {batch_id}")

    title = result.get("title") or ""
    abstract = result.get("abstract") or ""
    if db is not None and not abstract:
        paper = db.get_paper(paper_id)
        if paper:
            abstract = paper.get("abstract") or ""
            title = title or paper.get("title") or ""

    maude_before = deepcopy(result.get("maude") or {})
    llm_values = result.get("llm") or {}
    disagreement = result.get("disagreement") or {}
    routing_subnode = result.get("routing_subnode")
    rules_config = load_rules_config()
    rules_version = rules_config.get("version") or "unknown"
    now_str = datetime.now().isoformat()

    store = load_learned_cues_store(resolve_learned_cues_path(output_dir))
    applied_fields: List[Dict[str, Any]] = []
    cue_updates: List[Dict[str, Any]] = []

    for item in field_resolutions:
        field = item.get("field")
        if not field:
            continue
        resolved_value = item.get("resolved_value")
        if resolved_value is None and item.get("source") == "llm":
            resolved_value = llm_values.get(field)
        elif resolved_value is None and item.get("source") == "maude":
            resolved_value = maude_before.get(field)
        explanation = (item.get("explanation") or "").strip()
        if not explanation:
            raise ValueError(f"Explanation is required for field '{field}'")

        maude_value = maude_before.get(field)
        cue = extract_cue_from_explanation(explanation, abstract)
        if cue:
            store = apply_cue_update(
                infer_node_for_field(field, resolved_value, routing_subnode),
                cue,
                field,
                paper_id,
                explanation,
                store,
            )
            cue_updates.append({
                "node_id": infer_node_for_field(field, resolved_value, routing_subnode),
                "field": field,
                "cue": cue,
            })

        if db is not None:
            db.insert_feedback_audit(
                paper_id=paper_id,
                field_name=f"maude:{field}",
                old_value=serialize_value(maude_value),
                new_value=serialize_value(resolved_value),
                title=title,
                abstract=abstract,
                timestamp=now_str,
                confidence_before_review=(maude_before.get("classification_confidence")),
                classifier_version=f"maude-feedback-{rules_version}",
            )
            db.increment_metadata("feedback_corrections_since_eval", 1)
            db.set_metadata("last_feedback_audit_timestamp", now_str)

        applied_fields.append({
            "field": field,
            "resolved_value": resolved_value,
            "source": item.get("source"),
            "explanation": explanation,
            "maude_before": maude_value,
            "llm_value": llm_values.get(field),
            "cue_added": cue,
        })

    maude_after = maude_classifier.classify_paper(title, abstract, rules_version=rules_version)
    disagreement_field_names = set((disagreement.get("fields") or {}).keys())
    resolved_fields = set(result.get("expert_resolved_fields") or [])
    resolved_fields.update(item["field"] for item in applied_fields if item.get("field"))
    result["expert_resolved_fields"] = sorted(resolved_fields)

    prior_resolution = result.get("expert_resolution") or {}
    merged_fields: Dict[str, Dict[str, Any]] = {
        row["field"]: row for row in (prior_resolution.get("fields") or []) if row.get("field")
    }
    for row in applied_fields:
        merged_fields[row["field"]] = row

    resolution_record = {
        "paper_id": paper_id,
        "batch_id": batch_id,
        "title": title,
        "resolved_at": now_str,
        "fields": list(merged_fields.values()),
        "cue_updates": cue_updates,
        "maude_reclassified": {
            "publication_type": maude_after.get("publication_type"),
            "study_type": maude_after.get("study_type"),
            "classification_confidence": maude_after.get("classification_confidence"),
            "nodes_visited": (maude_after.get("_maude_meta") or {}).get("nodes_visited"),
        },
    }
    store.setdefault("resolutions", []).append({
        **resolution_record,
        "fields": applied_fields,
        "partial": bool(disagreement_field_names - resolved_fields),
    })
    save_learned_cues_store(store, resolve_learned_cues_path(output_dir))

    result["expert_resolution"] = resolution_record
    result["maude_after_resolution"] = {
        key: maude_after.get(key)
        for key in (
            "publication_type",
            "study_type",
            "exposure_method",
            "cannabis_type",
            "outcome_domain",
            "ingestion_status",
            "species",
            "classification_confidence",
        )
    }
    result["disagreement_resolved"] = disagreement_field_names.issubset(resolved_fields)
    save_batch_payload(payload)

    return {
        "paper_id": paper_id,
        "batch_id": batch_id,
        "resolution": {
            **resolution_record,
            "fields": applied_fields,
            "resolved_field_count": len(applied_fields),
            "remaining_fields": sorted(disagreement_field_names - resolved_fields),
        },
        "maude_reclassified": resolution_record["maude_reclassified"],
        "remaining_disagreements": _count_open_disagreements(batch_payloads, store),
        "feedback_logged": db is not None,
        "rules_version": rules_version,
    }


def _count_open_disagreements(
    batch_payloads: Sequence[Dict[str, Any]],
    store: Dict[str, Any],
) -> int:
    """Counts unresolved flagged papers across native Maude A/B batches."""
    open_count = 0
    for payload in batch_payloads:
        for result in payload.get("results") or []:
            if result.get("disagreement_resolved"):
                continue
            if not isinstance(result.get("maude"), dict) or result.get("maude", {}).get("backfilled"):
                continue
            disagreement = result.get("disagreement") or {}
            if not disagreement.get("flagged_for_review"):
                continue
            unresolved = set((disagreement.get("fields") or {}).keys()) - set(result.get("expert_resolved_fields") or [])
            if unresolved:
                open_count += 1
    return open_count


def build_disagreement_paper_queue(
    batch_payloads: Sequence[Dict[str, Any]],
    output_dir: Optional[Path] = None,
    db=None,
) -> List[Dict[str, Any]]:
    """Builds paper-level disagreement queue rows for the dashboard."""
    queue: List[Dict[str, Any]] = []

    for payload in batch_payloads:
        batch_id = payload.get("batch_id")
        for result in payload.get("results") or []:
            if not isinstance(result.get("maude"), dict) or not result.get("llm"):
                continue
            if result.get("maude", {}).get("backfilled"):
                continue
            paper_id = int(result.get("paper_id") or 0)
            if result.get("disagreement_resolved"):
                continue

            resolved_fields = set(result.get("expert_resolved_fields") or [])
            title = result.get("title") or ""
            abstract = result.get("abstract") or ""
            if db is not None and not abstract:
                paper = db.get_paper(paper_id)
                if paper:
                    abstract = paper.get("abstract") or ""
                    title = title or paper.get("title") or ""

            llm_block = classification_schema.normalize_classification_record(
                result.get("llm") or {},
                title,
                abstract,
            )
            maude_block = classification_schema.normalize_classification_record(
                result.get("maude") or {},
                title,
                abstract,
            )
            comparison = classification_schema.compare_classifiers(
                maude_block,
                llm_block,
                title,
                abstract,
            )
            if not comparison.get("flagged_for_review"):
                continue

            fields = []
            for field, values in (comparison.get("fields") or {}).items():
                if field in resolved_fields:
                    continue
                fields.append({
                    "field": field,
                    "maude_value": values.get("maude"),
                    "llm_value": values.get("llm"),
                })
            agreed_fields = [
                {"field": field_name, "value": field_value}
                for field_name, field_value in (comparison.get("agreed_fields") or {}).items()
            ]
            if not fields:
                continue

            queue.append({
                "paper_id": paper_id,
                "pmid": result.get("pmid"),
                "title": title,
                "abstract": abstract,
                "abstract_excerpt": (abstract or "")[:320],
                "batch_id": batch_id,
                "routing_subnode": result.get("routing_subnode"),
                "maude_confidence": (result.get("maude") or {}).get("classification_confidence"),
                "llm_confidence": (result.get("llm") or {}).get("classification_confidence"),
                "nodes_visited": (result.get("maude") or {}).get("nodes_visited"),
                "agreed_fields": agreed_fields,
                "fields": fields,
                "promotion_fields": comparison.get("promotion_fields") or [],
            })

    queue.sort(key=lambda row: (
        0 if "publication_type" in {field["field"] for field in row.get("fields") or []} else 1,
        row.get("maude_confidence") or 1.0,
    ))
    return queue
