# calibration_metrics.py
"""Aggregate calibration run artifacts into learning metrics for dashboards and agents."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import classification_schema
import handoff_learning_log
import maude_feedback
import subnode_field_scopes
import calibration_coordinator
import content_tiers

HIGH_LEVEL_FIELDS = (
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "publication_type",
)

DEFAULT_OUTPUT_DIR = Path("scratch/calibration_runs")
DEFAULT_RULES_PATH = Path("rules_config.json")
DEFAULT_RELIABILITY_MANIFEST = Path("reliability_manifest.json")


def resolve_calibration_output_dir(explicit: Optional[Path] = None) -> Path:
    """Returns the calibration artifacts directory (Fly volume, env override, or local scratch)."""
    if explicit is not None:
        return explicit
    import os

    env_dir = os.getenv("CALIBRATION_OUTPUT_DIR")
    if env_dir:
        return Path(env_dir)
    fly_dir = Path("/data/calibration_runs")
    if fly_dir.exists():
        return fly_dir
    return DEFAULT_OUTPUT_DIR


def load_rules_config(path: Path = DEFAULT_RULES_PATH) -> Dict[str, Any]:
    """Loads rules configuration used for automation readiness checks."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reliability_manifest(path: Path = DEFAULT_RELIABILITY_MANIFEST) -> Dict[str, Any]:
    """Loads reliability eval manifest when present."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _get_database_manager():
    """Returns DatabaseManager when available (local or production via env)."""
    try:
        from db_manager import DatabaseManager

        return DatabaseManager()
    except Exception:
        return None


def _decision_boundaries_readiness(
    decision_boundaries: Dict[str, Any],
    decision_nodes: Dict[str, Any],
    expert_notes: Dict[int, str],
) -> Tuple[str, str, str]:
    """Returns status, detail, and ready_when text for decision boundary checklist item."""
    if not decision_boundaries:
        return (
            "pending",
            "0 boundary rule(s)",
            "Promote calibration lessons into rules_config.decision_boundaries after expert review "
            "(each entry needs rule + expected + source).",
        )

    complete_count = sum(
        1 for boundary in decision_boundaries.values() if boundary.get("rule") and boundary.get("expected")
    )
    has_nodes = bool(
        decision_nodes.get("node0_ingestion")
        and decision_nodes.get("node1b_reviews")
        and decision_nodes.get("node1a_original")
    )
    boundary_keys = set(decision_boundaries.keys())
    expert_validated = any(
        any(key in note for key in boundary_keys) or "boundary" in note.lower()
        for note in expert_notes.values()
    )

    detail = f"{len(decision_boundaries)} boundary rule(s), {complete_count} with rule+expected"
    ready_when = (
        "Complete when: (1) each boundary has rule + expected fields, (2) decision_nodes for Node 0/1 are "
        "encoded, (3) expert validates boundaries via *_review.md or edit-classification, and "
        "(4) at least 2 distinct boundaries are promoted from calibration (currently "
        f"{complete_count}). Use rule_optimizer after feedback threshold or manual rules_config edit."
    )

    if complete_count >= 2 and has_nodes and (expert_validated or expert_notes):
        return "complete", detail, "Met: 2+ validated boundaries with decision_nodes encoded."
    if complete_count >= 1:
        return "in_progress", detail, ready_when
    return "in_progress", detail, ready_when


def build_propagation_timeline_from_batches(
    batch_payloads: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Aggregates BM25 few-shot usage from calibration JSON when llm_calls_log is unavailable."""
    timeline: List[Dict[str, Any]] = []
    for payload in batch_payloads:
        batch_id = payload.get("batch_id") or "unknown"
        created_at = payload.get("created_at")
        results = payload.get("results") or []
        call_count = 0
        bm25_used = 0
        sim_sum = 0.0
        sim_count = 0
        for result in results:
            metrics = result.get("llm_metrics") or {}
            if not metrics:
                continue
            call_count += 1
            if metrics.get("bm25_retrieval_used"):
                bm25_used += 1
            sim = metrics.get("few_shot_similarity")
            if sim is not None:
                sim_sum += float(sim)
                sim_count += 1
        timeline.append({
            "batch_id": batch_id,
            "first_call": created_at,
            "call_count": call_count,
            "bm25_used_count": bm25_used,
            "bm25_usage_rate": round(bm25_used / call_count, 3) if call_count else 0.0,
            "avg_few_shot_similarity": round(sim_sum / sim_count, 3) if sim_count else None,
            "source": "calibration_artifacts",
        })
    return timeline


def build_automation_layers(
    batch_payloads: Sequence[Dict[str, Any]],
    rules_config: Dict[str, Any],
    reliability_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds per-layer automation metrics aligned with docs/agent_automation_plan.md."""
    agent_cfg = rules_config.get("agent_automation") or {}
    eval_threshold = int(agent_cfg.get("feedback_eval_threshold") or 10)
    reliability_manifest = reliability_manifest or load_reliability_manifest()

    db = _get_database_manager()
    feedback: Dict[str, Any] = {}
    optimization: Dict[str, Any] = {
        "total_runs": 0,
        "by_status": {},
        "needs_human_review_count": 0,
        "recent_runs": [],
    }
    db_timeline: List[Dict[str, Any]] = []
    last_eval_ts: Optional[str] = None

    if db is not None:
        try:
            feedback = db.get_feedback_loop_metrics()
            optimization = db.get_optimization_log_metrics(limit=20)
            db_timeline = db.get_bm25_propagation_timeline(limit=50)
            last_eval_ts = db.get_metadata("last_reliability_eval_timestamp")
        except Exception:
            pass

    corrections_since_eval = int(feedback.get("corrections_since_eval") or 0)
    eval_progress = min(1.0, corrections_since_eval / eval_threshold) if eval_threshold else 0.0
    feedback_status = "active" if feedback.get("total_corrections", 0) > 0 else "idle"
    if corrections_since_eval >= eval_threshold:
        feedback_status = "eval_due"
    if not feedback.get("fts_index_ready"):
        feedback_status = "blocked" if feedback.get("total_corrections", 0) == 0 else feedback_status

    artifact_timeline = build_propagation_timeline_from_batches(batch_payloads)
    if db_timeline:
        db_batch_ids = {row.get("batch_id") for row in db_timeline}
        merged_timeline = list(db_timeline)
        for row in artifact_timeline:
            if row.get("batch_id") not in db_batch_ids:
                merged_timeline.append(row)
        timeline = merged_timeline
        timeline_source = "llm_calls_log+calibration_artifacts"
    else:
        timeline = artifact_timeline
        timeline_source = "calibration_artifacts"

    total_calls = sum(int(row.get("call_count") or 0) for row in timeline)
    total_bm25 = sum(int(row.get("bm25_used_count") or 0) for row in timeline)
    sim_values = [
        float(row["avg_few_shot_similarity"])
        for row in timeline
        if row.get("avg_few_shot_similarity") is not None
    ]
    propagation_status = "active" if total_bm25 > 0 else "pending"
    if total_calls > 0 and total_bm25 == 0:
        propagation_status = "idle"

    hamming_recent = []
    for run in optimization.get("recent_runs") or []:
        scores = run.get("field_group_scores") or {}
        hamming_recent.append({
            "run_id": run.get("run_id"),
            "timestamp": run.get("timestamp"),
            "status": run.get("status"),
            "reward": run.get("reward"),
            "gate_passed": bool(run.get("gate_passed")),
            "failed_attempts": run.get("failed_attempts"),
            "field_group_scores": scores,
            "rules_version_before": run.get("rules_version_before"),
            "rules_version_after": run.get("rules_version_after"),
        })

    opt_status = "idle"
    if optimization.get("total_runs", 0) > 0:
        opt_status = "active"
    if optimization.get("needs_human_review_count", 0) > 0:
        opt_status = "needs_human_review"

    manifest_metrics = reliability_manifest.get("metrics") or {}
    threshold = float(reliability_manifest.get("threshold") or 0.75)
    reliable_fields = 0
    total_fields = 0
    field_rows: List[Dict[str, Any]] = []
    for cohort, fields in manifest_metrics.items():
        if not isinstance(fields, dict):
            continue
        for field_name, stats in fields.items():
            if not isinstance(stats, dict):
                continue
            total_fields += 1
            score = stats.get("score")
            is_reliable = bool(stats.get("reliable"))
            if is_reliable:
                reliable_fields += 1
            field_rows.append({
                "cohort": cohort,
                "field": field_name,
                "score": score,
                "reliable": is_reliable,
                "below_threshold": score is not None and float(score) < threshold,
            })

    reliability_status = "stale"
    if reliability_manifest.get("last_updated"):
        reliability_status = "current"
    if corrections_since_eval >= eval_threshold:
        reliability_status = "re_eval_recommended"

    return {
        "feedback_loop": {
            "status": feedback_status,
            "total_corrections": feedback.get("total_corrections", 0),
            "unique_papers_corrected": feedback.get("unique_papers_corrected", 0),
            "corrections_since_eval": corrections_since_eval,
            "eval_threshold": eval_threshold,
            "eval_progress_pct": round(eval_progress * 100, 1),
            "eval_due": corrections_since_eval >= eval_threshold,
            "last_feedback_timestamp": feedback.get("last_feedback_timestamp"),
            "last_reliability_eval_timestamp": last_eval_ts or reliability_manifest.get("last_updated"),
            "fts_index_ready": feedback.get("fts_index_ready", False),
            "corrections_by_field": feedback.get("corrections_by_field") or {},
        },
        "upward_propagation": {
            "status": propagation_status,
            "source": timeline_source,
            "timeline": timeline,
            "overall_bm25_rate": round(total_bm25 / total_calls, 3) if total_calls else 0.0,
            "avg_few_shot_similarity": round(sum(sim_values) / len(sim_values), 3) if sim_values else None,
            "batch_count": len(timeline),
        },
        "optimization_logging": {
            "status": opt_status,
            "total_runs": optimization.get("total_runs", 0),
            "by_status": optimization.get("by_status") or {},
            "needs_human_review_count": optimization.get("needs_human_review_count", 0),
            "recent_runs": hamming_recent,
        },
        "reliability_eval": {
            "status": reliability_status,
            "last_updated": reliability_manifest.get("last_updated"),
            "last_eval_timestamp": last_eval_ts,
            "threshold": threshold,
            "reliable_field_count": reliable_fields,
            "total_field_count": total_fields,
            "field_rows": field_rows,
            "manifest_path": str(DEFAULT_RELIABILITY_MANIFEST),
        },
    }


def load_calibration_batch(path: Path) -> Dict[str, Any]:
    """Loads a single calibration JSON artifact."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def discover_calibration_batches(output_dir: Path = DEFAULT_OUTPUT_DIR) -> List[Path]:
    """Returns calibration JSON artifacts sorted by batch timestamp."""
    if not output_dir.exists():
        return []
    patterns = (
        "calibration_*.json",
        "node1_calibration_*.json",
        "node2a_calibration_*.json",
        "node2b_calibration_*.json",
        "node2c_calibration_*.json",
        "llm_pdf_maude_ab_*.json",
    )
    batches: List[Path] = []
    for pattern in patterns:
        batches.extend(output_dir.glob(pattern))
    batches = sorted(batches)
    return [path for path in batches if not path.name.endswith("_data.json")]


DECISION_TREE: List[Dict[str, Any]] = [
    {"id": "all", "label": "All calibration runs", "parent": None, "depth": 0},
    {"id": "node0", "label": "Node 0 · Ingestion", "parent": None, "depth": 0},
    {"id": "node1", "label": "Node 1 · Review vs Original", "parent": None, "depth": 0},
    {"id": "node1b", "label": "Node 1B · Reviews / Secondary", "parent": "node1", "depth": 1},
    {"id": "node1a", "label": "Node 1A · Original Papers", "parent": "node1", "depth": 1},
    {"id": "node2", "label": "Node 2 · Original Subtypes", "parent": None, "depth": 0},
    {"id": "node2a", "label": "Node 2A · Clinical", "parent": "node2", "depth": 1},
    {"id": "node2b", "label": "Node 2B · In Vivo", "parent": "node2", "depth": 1},
    {"id": "node2c", "label": "Node 2C · In Vitro", "parent": "node2", "depth": 1},
    {"id": "node2d", "label": "Node 2D · Mixed / Unclear", "parent": "node2", "depth": 1},
    {"id": "node3", "label": "Node 3 · Review subtypes", "parent": None, "depth": 0},
    {"id": "node3a", "label": "Node 3A · Systematic Review", "parent": "node3", "depth": 1},
    {"id": "node3b", "label": "Node 3B · Meta-analysis", "parent": "node3", "depth": 1},
    {"id": "node3c", "label": "Node 3C · Narrative / Editorial", "parent": "node3", "depth": 1},
    {"id": "crosscut", "label": "Cross-cutting · Low confidence", "parent": None, "depth": 0},
    {"id": "legacy_preclinical", "label": "Legacy · Preclinical original mode", "parent": "node2", "depth": 1},
]

REVIEW_PUBLICATION_TYPES = {"review", "case study"}

MODE_TO_BATCH_NODE = {
    "node1_routing": "node1",
    "node2a_clinical": "node2a",
    "node2b_in_vivo": "node2b",
    "node2c_in_vitro": "node2c",
    "llm_pdf_maude_ab": "node1",
    "preclinical_original": "legacy_preclinical",
    "low_confidence": "crosscut",
    "unclassified": "node0",
    "mixed": "all",
}

ALL_ROUTING_SUBNODES = [
    "node0",
    "node1",
    "node1b",
    "node1a",
    "node2",
    "node2a",
    "node2b",
    "node2c",
    "node2d",
    "node3",
    "node3a",
    "node3b",
    "node3c",
    "crosscut",
    "legacy_preclinical",
    "mixed",
]

# Dashboard node selection includes the node itself and downstream routing subnodes.
NODE_DOWNSTREAM: Dict[str, List[str]] = {
    "all": ALL_ROUTING_SUBNODES,
    "node0": ["node0"],
    "node1": [
        "node1",
        "node1b",
        "node1a",
        "node3",
        "node3a",
        "node3b",
        "node3c",
        "node2",
        "node2a",
        "node2b",
        "node2c",
        "node2d",
        "legacy_preclinical",
    ],
    "node1b": ["node1b", "node3", "node3a", "node3b", "node3c"],
    "node1a": ["node1a", "node2", "node2a", "node2b", "node2c", "node2d", "legacy_preclinical"],
    "node2": ["node2", "node1a", "node2a", "node2b", "node2c", "node2d", "legacy_preclinical"],
    "node2a": ["node2a"],
    "node2b": ["node2b"],
    "node2c": ["node2c"],
    "node2d": ["node2d"],
    "node3": ["node3", "node3a", "node3b", "node3c"],
    "node3a": ["node3a"],
    "node3b": ["node3b"],
    "node3c": ["node3c"],
    "crosscut": ["crosscut"],
    "legacy_preclinical": ["legacy_preclinical"],
}

# Maude decision-tree node ids mapped to dashboard routing subnodes.
MAUDE_NODE_TO_SUBNODE: Dict[str, str] = {
    "node1a_original": "node1a",
    "node2a_clinical": "node2a",
    "node2b_in_vivo": "node2b",
    "node2c_in_vitro": "node2c",
    "node2d_mixed": "node2d",
}

NODE2_SUBNODES = {"node1a", "node2a", "node2b", "node2c", "node2d", "legacy_preclinical"}

# High-level Maude vs LLM fields compared per decision-tree node (dashboard field picker).
NODE_CHARACTERISTICS: Dict[str, List[str]] = {
    "all": list(classification_schema.HIGH_LEVEL_COMPARE_FIELDS),
    "node0": ["ingestion_status"],
    "node1": ["ingestion_status", "publication_type", "study_type"],
    "node1b": ["publication_type", "study_type"],
    "node1a": ["publication_type", "study_type"],
    "node2": ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "species"],
    "node2a": [
        "study_type",
        "exposure_method",
        "cannabis_type",
        "outcome_domain",
        "species",
        "population_age",
        "population_sex",
    ],
    "node2b": ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "species"],
    "node2c": ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "species"],
    "node2d": ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "species"],
    "node3": ["publication_type", "study_type"],
    "node3a": ["study_type"],
    "node3b": ["study_type"],
    "node3c": ["study_type"],
    "crosscut": list(classification_schema.HIGH_LEVEL_COMPARE_FIELDS),
    "legacy_preclinical": ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "species"],
}

SUBNODE_FIELD_SCOPES = subnode_field_scopes.SUBNODE_FIELD_SCOPES

RL_NODE_LABELS: Dict[str, str] = {
    "node0": "Node 0 · Ingestion",
    "node1a": "Node 1A · Original Papers",
    "node1b": "Node 1B · Reviews",
    "node1c": "Node 1C · Case Report",
    "node2a": "Node 2A · Clinical",
    "node2b": "Node 2B · In Vivo",
    "node2c": "Node 2C · In Vitro",
    "node2d": "Node 2D · Mixed",
    "node3a": "Node 3A · Systematic Review",
    "node3b": "Node 3B · Meta-analysis",
    "node3c": "Node 3C · Narrative / Editorial",
}

RL_PREREQUISITE_NODES = ("node0", "node1a")


def field_is_populated(value: Any) -> bool:
    """Returns True when a classification field has a non-empty extracted value."""
    if value is None:
        return False
    if value == "":
        return False
    if value == []:
        return False
    if value == {}:
        return False
    return True


def score_paper_rl_metrics(
    result: Dict[str, Any],
    subnode: str,
) -> Optional[Dict[str, float]]:
    """Computes alignment and Maude recall rates for one paired paper result."""
    llm = result.get("llm") or {}
    maude = result.get("maude") or {}
    if not llm or not maude:
        return None

    paper_tier = result.get("content_tier") or content_tiers.infer_content_tier({
        **llm,
        "classifier_version": llm.get("classifier_version") or result.get("before_classifier_version"),
        "full_text_link": result.get("full_text_link"),
    })
    scope_fields = content_tiers.alignment_fields_in_scope_for_tier(subnode, paper_tier, llm)
    if not scope_fields:
        return None

    scoped = subnode_field_scopes.compare_scoped_fields(
        maude,
        llm,
        subnode,
        classification_schema.compare_field_values,
        scope_fields=scope_fields,
    )

    claude_populated_fields = [
        field for field in scope_fields if field_is_populated(llm.get(field))
    ]
    if claude_populated_fields:
        maude_populated = sum(
            1 for field in claude_populated_fields if field_is_populated(maude.get(field))
        )
        maude_recall_rate = round(maude_populated / len(claude_populated_fields), 4)
    else:
        maude_recall_rate = None

    optional_recall: Dict[str, Optional[float]] = {}
    for field in content_tiers.OPTIONAL_RECALL_FIELDS:
        if not field_is_populated(llm.get(field)):
            continue
        optional_recall[field] = (
            1.0
            if classification_schema.compare_field_values(maude.get(field), llm.get(field))
            else 0.0
        )

    alignment_rate = scoped.get("agreement_rate")
    if alignment_rate is None and scoped.get("scoped_field_count"):
        disagreements = len((scoped.get("fields") or {}))
        total = int(scoped.get("scoped_field_count") or len(scope_fields))
        alignment_rate = round((total - disagreements) / total, 4) if total else None

    return {
        "alignment_rate": float(alignment_rate) if alignment_rate is not None else None,
        "maude_recall_rate": maude_recall_rate,
        "claude_fields_populated": len(claude_populated_fields),
        "maude_fields_populated": sum(
            1 for field in claude_populated_fields if field_is_populated(maude.get(field))
        ) if claude_populated_fields else 0,
        "fields_in_scope": len(scope_fields),
        "content_tier": paper_tier,
        "optional_field_recall": optional_recall,
        "alignment_disagree_fields": [
            field
            for field in (scoped.get("fields") or {}).keys()
            if field not in content_tiers.ALIGNMENT_EXCLUDED_FIELDS
        ],
    }


def _average_metric(values: Sequence[Optional[float]]) -> Optional[float]:
    """Returns the mean of numeric values, ignoring None."""
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return round(sum(numeric) / len(numeric), 4)


def build_rl_node_progress(
    batch_payloads: Sequence[Dict[str, Any]],
    rules_config: Optional[Dict[str, Any]] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Builds cross-node RL progress timelines for alignment and Maude recall rates."""
    rules_config = rules_config or load_rules_config()
    rl_cfg = (rules_config.get("agent_automation") or {}).get("calibration_rl") or {}
    threshold_pct = float(rl_cfg.get("agreement_threshold_pct") or 90)
    threshold = threshold_pct / 100.0
    min_consecutive = int(rl_cfg.get("min_consecutive_pass_batches") or 2)
    active_queue = list(rl_cfg.get("subnode_queue") or calibration_coordinator.DEFAULT_SUBNODE_QUEUE)
    deferred = list(rl_cfg.get("subnode_reevaluate_later") or calibration_coordinator.DEFAULT_REEVALUATE_LATER)
    prerequisites = list(rl_cfg.get("prerequisites_passed") or RL_PREREQUISITE_NODES)
    handoffs = handoff_learning_log.load_handoff_learning_log(output_dir)

    ordered_nodes: List[str] = []
    for node_id in prerequisites:
        if node_id not in ordered_nodes:
            ordered_nodes.append(node_id)
    for node_id in active_queue:
        if node_id not in ordered_nodes:
            ordered_nodes.append(node_id)
    for node_id in deferred:
        if node_id not in ordered_nodes:
            ordered_nodes.append(node_id)

    batches_by_node: Dict[str, List[Dict[str, Any]]] = {node_id: [] for node_id in ordered_nodes}
    for payload in batch_payloads:
        subnode = payload.get("target_subnode") or payload.get("automation_node")
        if subnode in batches_by_node:
            batches_by_node[subnode].append(payload)

    nodes: Dict[str, Any] = {}
    combined_runs: List[Dict[str, Any]] = []
    tier_timelines: Dict[str, List[Dict[str, Any]]] = {
        tier: [] for tier in content_tiers.CONTENT_TIERS
    }

    for node_id in ordered_nodes:
        if node_id in prerequisites:
            phase = "prerequisite_passed"
            status = "passed"
        elif node_id in active_queue:
            phase = "active"
            status = "pending"
        else:
            phase = "deferred"
            status = "deferred"

        runs: List[Dict[str, Any]] = []
        consecutive_pass = 0
        for payload in sorted(batches_by_node.get(node_id) or [], key=lambda row: row.get("created_at") or ""):
            # Feedback refresh artifacts re-score Maude but are not new RL measurement batches.
            if payload.get("maude_refresh_only"):
                continue
            paper_metrics: List[Dict[str, float]] = []
            for result in payload.get("results") or []:
                scored = score_paper_rl_metrics(result, node_id)
                if scored:
                    paper_metrics.append(scored)

            gate_metrics = [
                row for row in paper_metrics
                if row.get("content_tier") == content_tiers.CONTENT_TIER_PDF_EXTRACTED
            ] or paper_metrics
            alignment_rate = _average_metric([row.get("alignment_rate") for row in gate_metrics])
            maude_recall_rate = _average_metric([row.get("maude_recall_rate") for row in gate_metrics])
            passed = alignment_rate is not None and alignment_rate >= threshold
            if passed:
                consecutive_pass += 1
            else:
                consecutive_pass = 0

            tier_metrics: Dict[str, Any] = {}
            for tier in content_tiers.CONTENT_TIERS:
                tier_rows = [row for row in paper_metrics if row.get("content_tier") == tier]
                if not tier_rows:
                    continue
                tier_alignment = _average_metric([row.get("alignment_rate") for row in tier_rows])
                tier_recall = _average_metric([row.get("maude_recall_rate") for row in tier_rows])
                tier_metrics[tier] = {
                    "label": content_tiers.CONTENT_TIER_LABELS.get(tier, tier),
                    "paper_count": len(tier_rows),
                    "alignment_rate": tier_alignment,
                    "alignment_pct": round(tier_alignment * 100, 1) if tier_alignment is not None else None,
                    "maude_recall_rate": tier_recall,
                    "maude_recall_pct": round(tier_recall * 100, 1) if tier_recall is not None else None,
                }

            run_index = len(runs) + 1
            run_row = {
                "run_index": run_index,
                "batch_id": payload.get("batch_id"),
                "created_at": payload.get("created_at"),
                "paper_count": len(paper_metrics),
                "content_tier": payload.get("content_tier"),
                "content_tier_counts": payload.get("content_tier_counts") or {},
                "alignment_rate": alignment_rate,
                "alignment_pct": round(alignment_rate * 100, 1) if alignment_rate is not None else None,
                "maude_recall_rate": maude_recall_rate,
                "maude_recall_pct": round(maude_recall_rate * 100, 1) if maude_recall_rate is not None else None,
                "tier_metrics": tier_metrics,
                "passed": passed,
                "mode": payload.get("mode"),
            }
            runs.append(run_row)
            combined_runs.append({
                **run_row,
                "node_id": node_id,
                "node_label": RL_NODE_LABELS.get(node_id, node_id),
                "series_label": f"{node_id} run {run_index}",
            })

        for run_row in runs:
            for tier, metrics in (run_row.get("tier_metrics") or {}).items():
                tier_timelines.setdefault(tier, []).append({
                    "run_index": run_row.get("run_index"),
                    "batch_id": run_row.get("batch_id"),
                    "created_at": run_row.get("created_at"),
                    "node_id": node_id,
                    **metrics,
                })

        if phase == "active" and runs:
            status = "passed" if consecutive_pass >= min_consecutive else "in_progress"
        elif phase == "active" and not runs:
            status = "pending"

        latest = runs[-1] if runs else {}
        nodes[node_id] = {
            "node_id": node_id,
            "label": RL_NODE_LABELS.get(node_id, node_id),
            "phase": phase,
            "status": status,
            "threshold_pct": threshold_pct,
            "min_consecutive_pass_batches": min_consecutive,
            "consecutive_pass_batches": consecutive_pass,
            "promotion_ready": consecutive_pass >= min_consecutive if phase == "active" else phase == "prerequisite_passed",
            "fields_in_scope": subnode_field_scopes.fields_in_scope(node_id),
            "runs": runs,
            "run_count": len(runs),
            "latest_alignment_rate": latest.get("alignment_rate"),
            "latest_alignment_pct": latest.get("alignment_pct"),
            "latest_maude_recall_rate": latest.get("maude_recall_rate"),
            "latest_maude_recall_pct": latest.get("maude_recall_pct"),
            "learning_timeline": handoff_learning_log.build_node_learning_timeline(
                node_id,
                handoffs,
                runs,
            ),
        }

    combined_runs.sort(key=lambda row: row.get("created_at") or "")

    return {
        "threshold_pct": threshold_pct,
        "min_consecutive_pass_batches": min_consecutive,
        "prerequisites_passed": prerequisites,
        "active_queue": active_queue,
        "deferred_queue": deferred,
        "ordered_nodes": ordered_nodes,
        "nodes": nodes,
        "combined_runs": combined_runs,
        "tier_timelines": tier_timelines,
        "content_tier_labels": content_tiers.CONTENT_TIER_LABELS,
        "reset_at": None,
    }


def build_subnode_promotion_readiness(
    batch_payloads: Sequence[Dict[str, Any]],
    rules_config: Optional[Dict[str, Any]] = None,
    target_subnode: Optional[str] = None,
    threshold: float = 0.90,
    min_consecutive: int = 2,
) -> Dict[str, Any]:
    """Computes per-sub-node Maude vs Claude agreement and promotion gate status."""
    rules_config = rules_config or load_rules_config()
    rl_cfg = (rules_config.get("agent_automation") or {}).get("calibration_rl") or {}
    threshold_pct = float(rl_cfg.get("agreement_threshold_pct") or threshold * 100)
    threshold = threshold_pct / 100.0
    min_consecutive = int(rl_cfg.get("min_consecutive_pass_batches") or min_consecutive)
    queue = rl_cfg.get("subnode_queue") or calibration_coordinator.DEFAULT_SUBNODE_QUEUE
    subnodes = [target_subnode] if target_subnode else list(queue)

    subnode_batches: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in subnodes}
    for payload in batch_payloads:
        subnode = payload.get("target_subnode") or payload.get("automation_node")
        if subnode not in subnode_batches:
            continue
        subnode_batches[subnode].append(payload)

    results: Dict[str, Any] = {}
    for subnode in subnodes:
        batch_timeline: List[Dict[str, Any]] = []
        consecutive_pass = 0
        promotion_ready = False

        for payload in sorted(subnode_batches.get(subnode) or [], key=lambda row: row.get("created_at") or ""):
            paper_scores: List[float] = []
            for result in payload.get("results") or []:
                llm = result.get("llm") or {}
                maude = result.get("maude") or {}
                if not llm or not maude:
                    continue
                scoped = result.get("scoped_disagreement")
                if scoped is None:
                    scoped = subnode_field_scopes.compare_scoped_fields(
                        maude,
                        llm,
                        subnode,
                        classification_schema.compare_field_values,
                    )
                rate = scoped.get("agreement_rate")
                if rate is not None:
                    paper_scores.append(float(rate))

            batch_rate = round(sum(paper_scores) / len(paper_scores), 4) if paper_scores else None
            recall_scores: List[float] = []
            for result in payload.get("results") or []:
                scored = score_paper_rl_metrics(result, subnode)
                if scored and scored.get("maude_recall_rate") is not None:
                    recall_scores.append(float(scored["maude_recall_rate"]))
            maude_recall_rate = _average_metric(recall_scores)
            passed = batch_rate is not None and batch_rate >= threshold
            if passed:
                consecutive_pass += 1
            else:
                consecutive_pass = 0
            batch_timeline.append({
                "batch_id": payload.get("batch_id"),
                "created_at": payload.get("created_at"),
                "paper_count": len(paper_scores),
                "agreement_rate": batch_rate,
                "alignment_rate": batch_rate,
                "alignment_pct": round(batch_rate * 100, 1) if batch_rate is not None else None,
                "maude_recall_rate": maude_recall_rate,
                "maude_recall_pct": round(maude_recall_rate * 100, 1) if maude_recall_rate is not None else None,
                "passed": passed,
            })

        if consecutive_pass >= min_consecutive:
            promotion_ready = True

        latest_rate = batch_timeline[-1]["agreement_rate"] if batch_timeline else None
        results[subnode] = {
            "target_subnode": subnode,
            "threshold_pct": threshold_pct,
            "min_consecutive_pass_batches": min_consecutive,
            "consecutive_pass_batches": consecutive_pass,
            "promotion_ready": promotion_ready,
            "latest_agreement_rate": latest_rate,
            "batch_timeline": batch_timeline,
            "fields_in_scope": subnode_field_scopes.fields_in_scope(subnode),
        }

    return {
        "threshold_pct": threshold_pct,
        "subnodes": results,
        "subnode_queue": queue,
    }


def load_staged_patches(output_dir: Path) -> List[Dict[str, Any]]:
    """Loads staged code/rules patch proposals from the calibration output directory."""
    staged_dir = output_dir / "staged_patches"
    if not staged_dir.exists():
        return []
    patches: List[Dict[str, Any]] = []
    for path in sorted(staged_dir.glob("*.json"), reverse=True):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["artifact_path"] = str(path)
            patches.append(payload)
        except Exception:
            continue
    return patches[:20]


def _normalize_publication_type(result: Dict[str, Any]) -> Optional[str]:
    """Returns the best available publication_type label from a calibration result."""
    pub = result.get("after_publication_type")
    if pub:
        return str(pub).strip().lower()
    changes = result.get("changes") or {}
    if "publication_type" in changes:
        new_val = changes["publication_type"].get("new")
        if isinstance(new_val, list):
            return str(new_val[0]).strip().lower() if new_val else None
        if new_val:
            return str(new_val).strip().lower()
    old_val = changes.get("publication_type", {}).get("old")
    if isinstance(old_val, str) and old_val.startswith("["):
        try:
            parsed = json.loads(old_val)
            if parsed:
                return str(parsed[0]).strip().lower()
        except Exception:
            pass
    return None


def _study_type_blob(result: Dict[str, Any]) -> str:
    """Builds a lowercase study_type string from calibration result fields."""
    study = result.get("after_study_type")
    if study is None:
        changes = result.get("changes") or {}
        if "study_type" in changes:
            study = changes["study_type"].get("new") or changes["study_type"].get("old")
    if isinstance(study, str):
        if study.startswith("["):
            try:
                study = json.loads(study)
            except Exception:
                study = [study]
        else:
            study = [study]
    if not isinstance(study, list):
        study = [study] if study else []
    return " ".join(str(item).lower() for item in study)


def _result_publication_type(result: Dict[str, Any]) -> Optional[str]:
    """Returns publication_type from native classifiers or calibration diff fields."""
    maude = result.get("maude") or {}
    llm = result.get("llm") or {}
    for block in (maude, llm):
        pub = block.get("publication_type")
        if pub:
            return str(pub).strip().lower()
    return _normalize_publication_type(result)


def _result_study_type(result: Dict[str, Any]) -> Any:
    """Returns study_type from native classifiers or calibration diff fields."""
    maude = result.get("maude") or {}
    llm = result.get("llm") or {}
    for block in (maude, llm):
        study = block.get("study_type")
        if study not in (None, "", []):
            return study
    return result.get("after_study_type")


def result_is_original_research(result: Dict[str, Any]) -> bool:
    """True when Maude, LLM, or Maude tree traversal indicates original research."""
    maude = result.get("maude") or {}
    llm = result.get("llm") or {}
    for block in (maude, llm):
        pub = (block.get("publication_type") or "").strip().lower()
        if pub == "original research":
            return True
    if _normalize_publication_type(result) == "original research":
        return True
    for node in maude.get("nodes_visited") or []:
        node_key = str(node).strip().lower()
        if node_key in MAUDE_NODE_TO_SUBNODE and node_key != "node1a_original":
            return True
        if node_key == "node1a_original":
            return True
    return False


def _routing_publication_type(result: Dict[str, Any]) -> Optional[str]:
    """Chooses publication_type for routing; either classifier can surface originals."""
    maude_pub = (result.get("maude") or {}).get("publication_type")
    llm_pub = (result.get("llm") or {}).get("publication_type")
    maude_norm = str(maude_pub).strip().lower() if maude_pub else None
    llm_norm = str(llm_pub).strip().lower() if llm_pub else None
    if maude_norm == "original research" or llm_norm == "original research":
        return "original research"
    return maude_norm or llm_norm or _normalize_publication_type(result)


def _infer_subnode_from_maude_nodes(nodes_visited: Sequence[Any]) -> Optional[str]:
    """Maps Maude nodes_visited to the finest dashboard routing subnode."""
    mapped: List[str] = []
    for node in nodes_visited or []:
        subnode = MAUDE_NODE_TO_SUBNODE.get(str(node).strip().lower())
        if subnode:
            mapped.append(subnode)
    if not mapped:
        return None
    for preferred in ("node2a", "node2b", "node2c", "node2d", "node1a"):
        if preferred in mapped:
            return preferred
    return mapped[-1]


def _routing_study_type(result: Dict[str, Any], routing_pub: Optional[str]) -> Any:
    """Chooses study_type for routing, preferring the classifier that marked original research."""
    maude = result.get("maude") or {}
    llm = result.get("llm") or {}
    if routing_pub == "original research":
        for block in (llm, maude):
            pub = (block.get("publication_type") or "").strip().lower()
            study = block.get("study_type")
            if pub == "original research" and study not in (None, "", []):
                return study
    return _result_study_type(result)


def infer_result_subnode(payload: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Infers decision-tree sub-node for a calibration paper result (supports legacy artifacts)."""
    mode = payload.get("mode") or ""
    routing_pub = _routing_publication_type(result)
    extracted = classification_schema.normalize_classification_record(
        {
            "publication_type": routing_pub,
            "study_type": _routing_study_type(result, routing_pub),
            "ingestion_status": (result.get("maude") or {}).get("ingestion_status")
            or (result.get("llm") or {}).get("ingestion_status"),
        },
        result.get("title") or "",
        result.get("abstract") or "",
    )
    subnode = classification_schema.infer_routing_subnode(mode, extracted)

    maude = result.get("maude") or {}
    from_maude = _infer_subnode_from_maude_nodes(maude.get("nodes_visited"))
    pub = (extracted.get("publication_type") or "").strip().lower()
    if from_maude and (pub == "original research" or from_maude in NODE2_SUBNODES):
        if from_maude.startswith("node2") or from_maude == "node1a":
            subnode = from_maude

    stored = str(result.get("routing_subnode") or "").strip()
    if stored in NODE2_SUBNODES and result_is_original_research(result):
        if stored == "node1a" and subnode.startswith("node2"):
            return subnode
        if stored.startswith("node2") and not subnode.startswith("node2"):
            return stored

    if result_is_original_research(result):
        if subnode in {"node1b", "node1"} or subnode.startswith("node3"):
            if from_maude and (from_maude.startswith("node2") or from_maude == "node1a"):
                return from_maude if from_maude != "node1a" else "node2d"
            return subnode if subnode.startswith("node2") else "node2d"

    return subnode


def resolve_batch_node(payload: Dict[str, Any]) -> str:
    """Maps a calibration batch payload to its primary automation-tree node."""
    automation_node = payload.get("automation_node")
    if automation_node == "node1":
        return "node1"
    mode = payload.get("mode") or ""
    return MODE_TO_BATCH_NODE.get(mode, "all")


def is_native_maude_ab_result(result: Dict[str, Any]) -> bool:
    """True when calibration logged live Maude+LLM pairing (not metrics backfill)."""
    maude = result.get("maude")
    if not isinstance(maude, dict) or not maude or maude.get("backfilled"):
        return False
    return bool(result.get("llm"))


def filter_maude_ab_payloads(batch_payloads: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Returns batch payloads containing only native Maude A/B paired results."""
    filtered: List[Dict[str, Any]] = []
    for payload in batch_payloads:
        native_results = [
            result for result in (payload.get("results") or [])
            if is_native_maude_ab_result(result)
        ]
        if not native_results:
            continue
        batch_copy = dict(payload)
        batch_copy["results"] = native_results
        filtered.append(batch_copy)
    return filtered


def build_maude_ab_epoch(batch_payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Builds metadata for when native Maude A/B testing began."""
    epoch_batches: List[Dict[str, Any]] = []
    for payload in batch_payloads:
        native_results = [
            result for result in (payload.get("results") or [])
            if is_native_maude_ab_result(result)
        ]
        if not native_results:
            continue
        epoch_batches.append({
            "batch_id": payload.get("batch_id"),
            "created_at": payload.get("created_at"),
            "paired_count": len(native_results),
        })
    epoch_batches.sort(key=lambda row: row.get("created_at") or "")
    first = epoch_batches[0] if epoch_batches else None
    return {
        "started_at": (first or {}).get("created_at"),
        "first_batch_id": (first or {}).get("batch_id"),
        "batch_count": len(epoch_batches),
        "paired_papers": sum(row["paired_count"] for row in epoch_batches),
        "batch_ids": [row["batch_id"] for row in epoch_batches if row.get("batch_id")],
    }


def summarize_maude_ab_batches(
    batch_payloads: Sequence[Dict[str, Any]],
    confidence_threshold: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Summarizes dashboard overview metrics from native Maude A/B batches only."""
    batches: List[Dict[str, Any]] = []
    all_candidates: List[Dict[str, Any]] = []
    aggregate_field_counts: Dict[str, int] = {}
    aggregate_hl_field_counts: Dict[str, int] = {}
    total_papers = 0
    total_cost = 0.0
    total_high_level_changed = 0

    for payload in filter_maude_ab_payloads(batch_payloads):
        summary = summarize_batch(payload)
        summary["artifact_path"] = payload.get("artifact_path")
        candidates = build_review_candidates(payload.get("results") or [], confidence_threshold)
        summary["review_candidates"] = candidates[:15]
        all_candidates.extend(candidates)
        batches.append(summary)

        total_papers += summary["paper_count"]
        total_cost += summary["total_cost"]
        total_high_level_changed += summary["high_level_changed"]
        for field, count in summary["field_change_counts"].items():
            aggregate_field_counts[field] = aggregate_field_counts.get(field, 0) + count
        for field, count in summary["high_level_field_counts"].items():
            aggregate_hl_field_counts[field] = aggregate_hl_field_counts.get(field, 0) + count

    deduped_candidates: Dict[int, Dict[str, Any]] = {}
    for candidate in all_candidates:
        paper_id = candidate["paper_id"]
        existing = deduped_candidates.get(paper_id)
        if existing is None or (candidate.get("confidence") or 1.0) < (existing.get("confidence") or 1.0):
            deduped_candidates[paper_id] = candidate
    priority_review = sorted(
        deduped_candidates.values(),
        key=lambda row: (
            row.get("confidence") if row.get("confidence") is not None else 1.0,
            -row.get("high_level_field_count", 0),
        ),
    )[:20]

    summary = {
        "batch_count": len(batches),
        "total_papers": total_papers,
        "total_cost": round(total_cost, 4),
        "high_level_changed": total_high_level_changed,
        "high_level_change_rate": round(total_high_level_changed / total_papers, 3) if total_papers else 0.0,
        "priority_review_count": len(priority_review),
    }
    field_change_totals = {
        "all_fields": dict(sorted(aggregate_field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_fields": dict(
            sorted(aggregate_hl_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
    }
    return batches, priority_review, {"summary": summary, "field_change_totals": field_change_totals}


def node_ancestor_chain(node_id: str) -> List[str]:
    """Returns node id and ancestor ids for aggregation (leaf → root)."""
    by_id = {node["id"]: node for node in DECISION_TREE}
    chain = [node_id]
    current = node_id
    while True:
        parent = (by_id.get(current) or {}).get("parent")
        if not parent or parent in chain:
            break
        chain.append(parent)
        current = parent
    return chain


def build_paper_traversal_row(
    payload: Dict[str, Any],
    result: Dict[str, Any],
    subnode: str,
) -> Dict[str, Any]:
    """Builds a dashboard row for one paper under a decision-tree node."""
    hl_count, hl_fields = count_high_level_changes(result.get("changes"))
    return {
        "paper_id": result.get("paper_id"),
        "pmid": result.get("pmid"),
        "title": result.get("title"),
        "variant": result.get("variant"),
        "confidence": result.get("after_confidence"),
        "batch_id": payload.get("batch_id"),
        "mode": payload.get("mode"),
        "automation_node": payload.get("automation_node"),
        "routing_subnode": subnode,
        "publication_type": _result_publication_type(result),
        "high_level_field_count": hl_count,
        "high_level_fields": hl_fields,
        "changes": result.get("changes") or {},
        "status": result.get("status"),
    }


def summarize_node_papers(papers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarizes metrics for papers grouped under one decision-tree node."""
    confidences: List[float] = []
    hl_changed = 0
    field_counts: Dict[str, int] = {}
    hl_field_counts: Dict[str, int] = {}
    variant_counts: Dict[str, int] = {}
    total_cost = 0.0

    for paper in papers:
        if paper.get("confidence") is not None:
            confidences.append(float(paper["confidence"]))
        if paper.get("high_level_field_count", 0) > 0:
            hl_changed += 1
        for field in paper.get("high_level_fields") or []:
            hl_field_counts[field] = hl_field_counts.get(field, 0) + 1
        for field in (paper.get("changes") or {}).keys():
            field_counts[field] = field_counts.get(field, 0) + 1
        variant = paper.get("variant") or "unknown"
        variant_counts[variant] = variant_counts.get(variant, 0) + 1

    paper_count = len(papers)
    return {
        "paper_count": paper_count,
        "batch_count": len({paper.get("batch_id") for paper in papers if paper.get("batch_id")}),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "high_level_changed": hl_changed,
        "high_level_change_rate": round(hl_changed / paper_count, 3) if paper_count else 0.0,
        "variant_counts": variant_counts,
        "field_change_counts": dict(sorted(field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_field_counts": dict(
            sorted(hl_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
    }


def build_node_traversal(batch_payloads: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Builds per-node paper lists and stats for interactive dashboard traversal."""
    node_papers: Dict[str, List[Dict[str, Any]]] = {node["id"]: [] for node in DECISION_TREE}
    node_batch_ids: Dict[str, set] = {node["id"]: set() for node in DECISION_TREE}

    for payload in batch_payloads:
        batch_id = payload.get("batch_id")
        batch_node = resolve_batch_node(payload)
        if batch_id:
            node_batch_ids["all"].add(batch_id)
            if batch_node in node_batch_ids:
                node_batch_ids[batch_node].add(batch_id)

        for result in payload.get("results") or []:
            subnode = infer_result_subnode(payload, result)
            row = build_paper_traversal_row(payload, result, subnode)
            for node_id, allowed_subnodes in NODE_DOWNSTREAM.items():
                if subnode not in allowed_subnodes:
                    continue
                if node_id in node_papers:
                    node_papers[node_id].append(row)
                    if batch_id:
                        node_batch_ids[node_id].add(batch_id)

    nodes: Dict[str, Any] = {}
    original_research_count = 0
    for payload in batch_payloads:
        for result in payload.get("results") or []:
            if result_is_original_research(result):
                original_research_count += 1

    for tree_node in DECISION_TREE:
        node_id = tree_node["id"]
        papers = node_papers.get(node_id, [])
        stats = summarize_node_papers(papers)
        if node_id == "node2":
            stats["original_research_count"] = original_research_count
        nodes[node_id] = {
            **tree_node,
            "batch_ids": sorted(node_batch_ids.get(node_id, set())),
            "papers": papers,
            "stats": stats,
        }

    return {
        "tree": DECISION_TREE,
        "nodes": nodes,
        "downstream": NODE_DOWNSTREAM,
        "original_research_paper_count": original_research_count,
    }


def count_high_level_changes(changes: Optional[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Counts how many high-level fields changed in a result diff."""
    if not changes:
        return 0, []
    changed = [field for field in HIGH_LEVEL_FIELDS if field in changes]
    return len(changed), changed


def summarize_variant_results(results: Sequence[Dict[str, Any]], variant: str) -> Dict[str, Any]:
    """Summarizes metrics for one prompt variant within a batch."""
    variant_results = [row for row in results if row.get("variant") == variant]
    confidences: List[float] = []
    total_cost = 0.0
    high_level_changed = 0
    field_counts: Dict[str, int] = {}
    high_level_field_counts: Dict[str, int] = {}

    for result in variant_results:
        if result.get("after_confidence") is not None:
            confidences.append(float(result["after_confidence"]))
        metrics = result.get("llm_metrics") or {}
        total_cost += float(metrics.get("cost") or 0.0)

        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count:
            high_level_changed += 1
        for field in hl_fields:
            high_level_field_counts[field] = high_level_field_counts.get(field, 0) + 1

        for field in (result.get("changes") or {}).keys():
            field_counts[field] = field_counts.get(field, 0) + 1

    paper_count = len(variant_results)
    return {
        "variant": variant,
        "paper_count": paper_count,
        "updates_applied": sum(1 for row in variant_results if row.get("status") == "updated"),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "high_level_changed": high_level_changed,
        "high_level_change_rate": round(high_level_changed / paper_count, 3) if paper_count else 0.0,
        "total_cost": round(total_cost, 4),
        "avg_cost": round(total_cost / paper_count, 4) if paper_count else 0.0,
        "field_change_counts": dict(sorted(field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_field_counts": dict(
            sorted(high_level_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
    }


def summarize_batch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarizes one calibration batch payload."""
    results = payload.get("results") or []
    variants = payload.get("variants") or sorted({row.get("variant", "unknown") for row in results})
    variant_summaries = [summarize_variant_results(results, variant) for variant in variants]

    total_cost = 0.0
    field_counts: Dict[str, int] = {}
    high_level_field_counts: Dict[str, int] = {}
    high_level_changed = 0
    confidences: List[float] = []

    for result in results:
        if result.get("after_confidence") is not None:
            confidences.append(float(result["after_confidence"]))
        total_cost += float((result.get("llm_metrics") or {}).get("cost") or 0.0)
        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count:
            high_level_changed += 1
        for field in hl_fields:
            high_level_field_counts[field] = high_level_field_counts.get(field, 0) + 1
        for field in (result.get("changes") or {}).keys():
            field_counts[field] = field_counts.get(field, 0) + 1

    return {
        "batch_id": payload.get("batch_id"),
        "created_at": payload.get("created_at"),
        "rules_version": payload.get("rules_version"),
        "mode": payload.get("mode"),
        "automation_node": payload.get("automation_node"),
        "calibration_label": payload.get("calibration_label"),
        "variants": variants,
        "calls_attempted": payload.get("calls_attempted", 0),
        "updates_applied": payload.get("updates_applied", 0),
        "dry_run": payload.get("dry_run", False),
        "abstract_only": payload.get("abstract_only", True),
        "paper_count": len(results),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "high_level_changed": high_level_changed,
        "high_level_change_rate": round(high_level_changed / len(results), 3) if results else 0.0,
        "total_cost": round(total_cost, 4),
        "field_change_counts": dict(sorted(field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_field_counts": dict(
            sorted(high_level_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
        "variant_summaries": variant_summaries,
    }


def build_review_candidates(
    results: Sequence[Dict[str, Any]],
    confidence_threshold: float = 0.72,
) -> List[Dict[str, Any]]:
    """Builds prioritized expert-review candidates from calibration results."""
    candidates: List[Dict[str, Any]] = []
    for result in results:
        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count == 0:
            continue
        confidence = result.get("after_confidence")
        candidates.append({
            "paper_id": result.get("paper_id"),
            "pmid": result.get("pmid"),
            "title": result.get("title"),
            "variant": result.get("variant"),
            "confidence": confidence,
            "in_review_queue": confidence is not None and float(confidence) <= confidence_threshold,
            "high_level_fields": hl_fields,
            "high_level_field_count": hl_count,
            "changes": {
                field: result["changes"][field]
                for field in hl_fields
                if field in (result.get("changes") or {})
            },
        })

    candidates.sort(
        key=lambda row: (
            row.get("confidence") if row.get("confidence") is not None else 1.0,
            -row.get("high_level_field_count", 0),
        )
    )
    return candidates


def parse_review_markdown(review_path: Path) -> Dict[str, Any]:
    """Parses a calibration review markdown artifact for expert notes."""
    if not review_path.exists():
        return {"expert_notes": {}, "queue_overlap_count": None}

    text = review_path.read_text(encoding="utf-8")
    expert_notes: Dict[int, str] = {}
    sections = re.split(r"\n### Paper (\d+) \|", text)
    for index in range(1, len(sections), 2):
        paper_id = int(sections[index])
        body = sections[index + 1]
        note_match = re.search(r"- Expert status:\s*(.+)", body)
        if note_match:
            expert_notes[paper_id] = note_match.group(1).strip()

    overlap_match = re.search(r"overlap with calibration batch: `(\d+)`", text)
    return {
        "expert_notes": expert_notes,
        "queue_overlap_count": int(overlap_match.group(1)) if overlap_match else None,
    }


def compare_variants_across_batches(batch_summaries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregates A/B variant metrics across all calibration batches."""
    aggregate: Dict[str, Dict[str, Any]] = {}
    for batch in batch_summaries:
        for variant_summary in batch.get("variant_summaries") or []:
            variant = variant_summary["variant"]
            bucket = aggregate.setdefault(
                variant,
                {
                    "variant": variant,
                    "paper_count": 0,
                    "high_level_changed": 0,
                    "total_cost": 0.0,
                    "confidence_sum": 0.0,
                    "confidence_count": 0,
                    "high_level_field_counts": {},
                },
            )
            bucket["paper_count"] += variant_summary["paper_count"]
            bucket["high_level_changed"] += variant_summary["high_level_changed"]
            bucket["total_cost"] += variant_summary["total_cost"]
            if variant_summary.get("avg_confidence") is not None:
                bucket["confidence_sum"] += variant_summary["avg_confidence"] * variant_summary["paper_count"]
                bucket["confidence_count"] += variant_summary["paper_count"]
            for field, count in (variant_summary.get("high_level_field_counts") or {}).items():
                bucket["high_level_field_counts"][field] = (
                    bucket["high_level_field_counts"].get(field, 0) + count
                )

    comparison: List[Dict[str, Any]] = []
    for variant, bucket in sorted(aggregate.items()):
        paper_count = bucket["paper_count"]
        comparison.append({
            "variant": variant,
            "paper_count": paper_count,
            "avg_confidence": round(bucket["confidence_sum"] / bucket["confidence_count"], 3)
            if bucket["confidence_count"]
            else None,
            "high_level_changed": bucket["high_level_changed"],
            "high_level_change_rate": round(bucket["high_level_changed"] / paper_count, 3)
            if paper_count
            else 0.0,
            "total_cost": round(bucket["total_cost"], 4),
            "avg_cost": round(bucket["total_cost"] / paper_count, 4) if paper_count else 0.0,
            "high_level_field_counts": dict(
                sorted(bucket["high_level_field_counts"].items(), key=lambda item: item[1], reverse=True)
            ),
        })
    return {"variants": comparison}


def _normalize_field_for_agreement(value: Any) -> Any:
    """Normalizes field values for Maude vs LLM agreement statistics."""
    if isinstance(value, list):
        return tuple(sorted(str(item).lower() for item in value))
    if value is None:
        return None
    return str(value).strip().lower()


def _dedupe_maude_nodes(nodes: Optional[Sequence[str]]) -> List[str]:
    """Deduplicates Maude node path labels for dashboard display."""
    if not nodes:
        return []
    seen: set = set()
    ordered: List[str] = []
    for node in nodes:
        label = str(node)
        if label not in seen:
            seen.add(label)
            ordered.append(label)
    return ordered


def build_maude_comparison(
    batch_payloads: Sequence[Dict[str, Any]],
    rules_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Aggregates Maude vs LLM paired classification metrics from calibration batches."""
    try:
        import maude_classifier as maude_mod
    except ImportError:
        maude_mod = None

    rules_config = rules_config or load_rules_config()
    maude_cfg = rules_config.get("maude") or {}
    promotion_cfg = maude_cfg.get("promotion") or {}
    max_gap_pct = float(promotion_cfg.get("max_reliability_gap_pct", 10))
    min_batches = int(promotion_cfg.get("min_batches_within_gap", 3))
    promotion_fields = ("publication_type", "study_type")

    field_totals: Dict[str, Dict[str, int]] = {}
    batch_timeline: List[Dict[str, Any]] = []
    disagreement_rows: List[Dict[str, Any]] = []
    paired_records: List[Dict[str, Any]] = []
    paired_count = 0
    flagged_count = 0
    promotion_disagreements = 0

    for payload in batch_payloads:
        batch_id = payload.get("batch_id") or "unknown"
        results = payload.get("results") or []
        batch_paired = 0
        batch_flagged = 0
        batch_promotion_disagree = 0

        for result in results:
            llm = result.get("llm") or {}
            maude = result.get("maude") or {}
            if not llm and result.get("after_publication_type") is not None:
                llm = {
                    "publication_type": result.get("after_publication_type"),
                    "study_type": result.get("after_study_type"),
                    "exposure_method": (result.get("changes") or {}).get("exposure_method", {}).get("new"),
                    "cannabis_type": (result.get("changes") or {}).get("cannabis_type", {}).get("new"),
                    "outcome_domain": (result.get("changes") or {}).get("outcome_domain", {}).get("new"),
                    "classification_confidence": result.get("after_confidence"),
                }
            if not maude and maude_mod and result.get("title"):
                rules_version = payload.get("rules_version")
                maude_out = maude_mod.classify_paper(
                    result.get("title") or "",
                    result.get("abstract") or result.get("title") or "",
                    rules_version=rules_version,
                )
                maude = {
                    "publication_type": maude_out.get("publication_type"),
                    "study_type": maude_out.get("study_type"),
                    "exposure_method": maude_out.get("exposure_method"),
                    "cannabis_type": maude_out.get("cannabis_type"),
                    "outcome_domain": maude_out.get("outcome_domain"),
                    "ingestion_status": maude_out.get("ingestion_status"),
                    "species": maude_out.get("species"),
                    "classification_confidence": maude_out.get("classification_confidence"),
                    "nodes_visited": (maude_out.get("_maude_meta") or {}).get("nodes_visited"),
                    "backfilled": True,
                }
                if not result.get("disagreement"):
                    result = dict(result)
                    result["disagreement"] = maude_mod.compare_maude_llm(maude_out, llm)
            if not maude or not llm:
                continue

            paired_count += 1
            batch_paired += 1
            disagreement = result.get("disagreement") or {}
            routing_subnode = infer_result_subnode(payload, result)
            high_level_fields = maude_cfg.get("high_level_fields") or list(promotion_fields)
            fields_disagreeing: List[str] = []

            for field in high_level_fields:
                stats = field_totals.setdefault(field, {"agree": 0, "disagree": 0})
                if _normalize_field_for_agreement(maude.get(field)) == _normalize_field_for_agreement(llm.get(field)):
                    stats["agree"] += 1
                else:
                    stats["disagree"] += 1
                    fields_disagreeing.append(field)
                    if field in promotion_fields:
                        disagreement_rows.append({
                            "paper_id": result.get("paper_id"),
                            "pmid": result.get("pmid"),
                            "title": result.get("title"),
                            "batch_id": batch_id,
                            "routing_subnode": routing_subnode,
                            "field": field,
                            "maude_value": maude.get(field),
                            "llm_value": llm.get(field),
                            "maude_confidence": maude.get("classification_confidence"),
                            "llm_confidence": llm.get("classification_confidence"),
                            "nodes_visited": _dedupe_maude_nodes(maude.get("nodes_visited")),
                        })

            paired_records.append({
                "paper_id": result.get("paper_id"),
                "pmid": result.get("pmid"),
                "title": result.get("title"),
                "batch_id": batch_id,
                "routing_subnode": routing_subnode,
                "maude": {field: maude.get(field) for field in high_level_fields},
                "llm": {field: llm.get(field) for field in high_level_fields},
                "maude_confidence": maude.get("classification_confidence"),
                "llm_confidence": llm.get("classification_confidence"),
                "nodes_visited": _dedupe_maude_nodes(maude.get("nodes_visited")),
                "fields_disagreeing": fields_disagreeing,
                "flagged_for_review": bool(disagreement.get("flagged_for_review")),
                "backfilled": bool(maude.get("backfilled")),
            })

            if disagreement.get("flagged_for_review"):
                flagged_count += 1
                batch_flagged += 1
            if disagreement.get("promotion_field_count", 0) > 0:
                promotion_disagreements += 1
                batch_promotion_disagree += 1

        if batch_paired:
            batch_timeline.append({
                "batch_id": batch_id,
                "created_at": payload.get("created_at"),
                "paired_count": batch_paired,
                "flagged_count": batch_flagged,
                "flagged_rate": round(batch_flagged / batch_paired, 3),
                "promotion_disagree_count": batch_promotion_disagree,
                "promotion_disagree_rate": round(batch_promotion_disagree / batch_paired, 3),
                "promotion_agreement_rate": round(1 - (batch_promotion_disagree / batch_paired), 3),
            })

    field_agreement: Dict[str, Any] = {}
    for field, stats in field_totals.items():
        total = stats["agree"] + stats["disagree"]
        field_agreement[field] = {
            "agree": stats["agree"],
            "disagree": stats["disagree"],
            "agreement_rate": round(stats["agree"] / total, 3) if total else None,
            "disagreement_rate": round(stats["disagree"] / total, 3) if total else None,
        }

    promotion_rates = [row["promotion_disagree_rate"] for row in batch_timeline if row.get("paired_count")]
    recent_rates = promotion_rates[-min_batches:] if promotion_rates else []
    within_gap = all(rate <= (max_gap_pct / 100.0) for rate in recent_rates) if recent_rates else False
    promotion_ready = bool(len(recent_rates) >= min_batches and within_gap and paired_count > 0)

    pub_stats = field_agreement.get("publication_type") or {}
    study_stats = field_agreement.get("study_type") or {}
    avg_promotion_disagreement = None
    if pub_stats.get("disagreement_rate") is not None and study_stats.get("disagreement_rate") is not None:
        avg_promotion_disagreement = round(
            (pub_stats["disagreement_rate"] + study_stats["disagreement_rate"]) / 2,
            3,
        )

    return {
        "paired_papers": paired_count,
        "flagged_for_review": flagged_count,
        "flagged_rate": round(flagged_count / paired_count, 3) if paired_count else None,
        "promotion_disagreement_rate": avg_promotion_disagreement,
        "promotion_threshold_pct": max_gap_pct,
        "promotion_ready": promotion_ready,
        "promotion_status": (
            "ready_for_eval_signoff"
            if promotion_ready
            else ("collecting_batches" if paired_count else "no_maude_data")
        ),
        "promotion_detail": (
            f"Last {len(recent_rates)}/{min_batches} batches within {max_gap_pct}% disagreement "
            f"on publication_type + study_type"
            if paired_count
            else "Run calibration with Maude paired logging enabled"
        ),
        "backfilled_from_title": any(
            (r.get("maude") or {}).get("backfilled")
            for payload in batch_payloads
            for r in (payload.get("results") or [])
        ),
        "field_agreement": field_agreement,
        "high_level_fields": maude_cfg.get("high_level_fields") or list(promotion_fields),
        "batch_timeline": batch_timeline,
        "paired_records": paired_records,
        "disagreement_queue": sorted(
            disagreement_rows,
            key=lambda row: (row.get("field") != "publication_type", row.get("maude_confidence") or 1.0),
        )[:40],
    }


def build_automation_readiness(
    batch_summaries: Sequence[Dict[str, Any]],
    rules_config: Dict[str, Any],
    expert_notes: Dict[int, str],
) -> Dict[str, Any]:
    """Builds automation readiness checklist items for pre-expert-guideline phase."""
    agent_cfg = rules_config.get("agent_automation") or {}
    decision_boundaries = rules_config.get("decision_boundaries") or {}
    decision_nodes = rules_config.get("decision_nodes") or {}
    live_batches = [batch for batch in batch_summaries if not batch.get("dry_run")]
    total_papers = sum(batch.get("paper_count", 0) for batch in live_batches)

    boundary_status, boundary_detail, boundary_ready_when = _decision_boundaries_readiness(
        decision_boundaries, decision_nodes, expert_notes
    )

    checklist = [
        {
            "id": "calibration_runner",
            "label": "Bounded calibration runner exercised",
            "status": "complete" if live_batches else "pending",
            "detail": f"{len(live_batches)} live batch(es), {total_papers} papers",
            "ready_when": "Run calibration_agent.py batches (node1 or full) against production DB.",
        },
        {
            "id": "variant_ab",
            "label": "Prompt variant A/B comparison captured",
            "status": "complete"
            if live_batches and all(len(batch.get("variants") or []) >= 2 for batch in live_batches)
            else "pending",
            "detail": "control vs decision_checklist",
            "ready_when": "Each live batch must include control + decision_checklist variants.",
        },
        {
            "id": "review_artifacts",
            "label": "High-level review walkthroughs produced",
            "status": "complete" if expert_notes else "in_progress",
            "detail": f"{len(expert_notes)} expert note(s) captured",
            "ready_when": "Add *_review.md alongside calibration JSON with per-paper expert notes.",
        },
        {
            "id": "decision_chart",
            "label": "Expert decision chart received",
            "status": "pending"
            if "awaiting" in str(agent_cfg.get("decision_chart_status", "")).lower()
            else "complete",
            "detail": agent_cfg.get("decision_chart_status", "unknown"),
            "ready_when": "Set agent_automation.decision_chart_status and encode decision_nodes in rules_config.",
        },
        {
            "id": "decision_boundaries",
            "label": "Learned decision boundaries encoded in rules_config",
            "status": boundary_status,
            "detail": boundary_detail,
            "ready_when": boundary_ready_when,
        },
        {
            "id": "expert_corrections",
            "label": "Expert-approved corrections applied via edit-classification",
            "status": "in_progress" if expert_notes else "pending",
            "detail": "Use /api/papers/<paper_id>/edit-classification after review",
            "ready_when": "Submit corrections via edit-classification so feedback_audit and BM25 few-shot index populate.",
        },
    ]

    completed = sum(1 for item in checklist if item["status"] == "complete")
    return {
        "checklist": checklist,
        "completed_count": completed,
        "total_count": len(checklist),
        "ready_for_full_automation": completed == len(checklist),
    }


def build_dashboard_metrics(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rules_config: Optional[Dict[str, Any]] = None,
    confidence_threshold: float = 0.72,
) -> Dict[str, Any]:
    """Builds aggregated learning metrics from all calibration artifacts."""
    rules_config = rules_config or load_rules_config()
    batch_paths = discover_calibration_batches(output_dir)

    batches: List[Dict[str, Any]] = []
    batch_payloads: List[Dict[str, Any]] = []
    expert_notes: Dict[int, str] = {}
    queue_overlap_total = 0

    for path in batch_paths:
        payload = load_calibration_batch(path)
        payload["artifact_path"] = str(path)
        batch_payloads.append(payload)
        summary = summarize_batch(payload)
        review_meta = parse_review_markdown(path.with_name(f"{path.stem}_review.md"))
        summary["review_path"] = str(path.with_name(f"{path.stem}_review.md"))
        summary["walkthrough_path"] = str(path.with_name(f"{path.stem}_walkthrough.md"))
        summary["artifact_path"] = str(path)
        summary["expert_notes"] = review_meta["expert_notes"]
        expert_notes.update(review_meta["expert_notes"])
        if review_meta.get("queue_overlap_count") is not None:
            queue_overlap_total += review_meta["queue_overlap_count"]
        batches.append(summary)

    maude_ab_payloads = filter_maude_ab_payloads(batch_payloads)
    maude_ab_epoch = build_maude_ab_epoch(batch_payloads)
    maude_ab_overview, maude_ab_priority_review, maude_ab_totals = summarize_maude_ab_batches(
        batch_payloads,
        confidence_threshold,
    )
    for candidate in maude_ab_priority_review:
        note = expert_notes.get(candidate["paper_id"])
        if note:
            candidate["expert_status"] = note

    rules_version = rules_config.get("version")
    if batches and batches[-1].get("rules_version"):
        rules_version = batches[-1]["rules_version"]

    overview_summary = maude_ab_totals["summary"]
    overview_summary["queue_overlap_total"] = queue_overlap_total
    overview_summary["expert_notes_count"] = len(expert_notes)

    metrics = {
        "generated_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "confidence_threshold": confidence_threshold,
        "maude_ab_epoch": maude_ab_epoch,
        "summary": overview_summary,
        "batches": maude_ab_overview,
        "variant_comparison": compare_variants_across_batches(maude_ab_overview),
        "field_change_totals": maude_ab_totals["field_change_totals"],
        "priority_review": maude_ab_priority_review,
        "decision_boundaries": rules_config.get("decision_boundaries") or {},
        "decision_nodes": rules_config.get("decision_nodes") or {},
        "calibration_variants": rules_config.get("calibration_variants") or {},
        "node_traversal": build_node_traversal(maude_ab_payloads),
        "node_downstream": NODE_DOWNSTREAM,
        "node_characteristics": NODE_CHARACTERISTICS,
        "automation_readiness": build_automation_readiness(batches, rules_config, expert_notes),
        "automation_layers": build_automation_layers(batch_payloads, rules_config),
        "maude_comparison": build_maude_comparison(maude_ab_payloads, rules_config),
        "maude_feedback": _build_maude_feedback_metrics(maude_ab_payloads, output_dir),
        "calibration_lock": calibration_coordinator.get_lock_status(db=_get_database_manager(), rules_config=rules_config),
        "subnode_promotion": build_subnode_promotion_readiness(maude_ab_payloads, rules_config),
        "rl_node_progress": build_rl_node_progress(maude_ab_payloads, rules_config, output_dir),
        "staged_patches": load_staged_patches(output_dir),
        "handoff_learning_log": handoff_learning_log.load_handoff_learning_log(output_dir),
        "subnode_field_scopes": SUBNODE_FIELD_SCOPES,
    }

    session_path = output_dir / "rl_session.json"
    if session_path.exists():
        try:
            with open(session_path, encoding="utf-8") as handle:
                session = json.load(handle)
            metrics["rl_node_progress"]["reset_at"] = session.get("reset_at")
            metrics["rl_node_progress"]["session"] = session
        except Exception:
            pass

    return metrics


def _build_maude_feedback_metrics(
    batch_payloads: Sequence[Dict[str, Any]],
    output_dir: Path,
) -> Dict[str, Any]:
    """Builds Maude disagreement queue and learned-cue feedback metrics."""
    db = _get_database_manager()
    store = maude_feedback.load_learned_cues_store(maude_feedback.resolve_learned_cues_path(output_dir))
    queue = maude_feedback.build_disagreement_paper_queue(batch_payloads, output_dir, db=db)
    recent_feedback: List[Dict[str, Any]] = []
    if db is not None:
        try:
            recent_feedback = [
                row for row in db.get_recent_feedback(limit=10)
                if str(row.get("field_name") or "").startswith("maude:")
            ]
        except Exception:
            recent_feedback = []
    return {
        "disagreement_queue": queue,
        "open_count": len(queue),
        "resolved_count": len(store.get("resolutions") or []),
        "cue_updates": store.get("cue_updates") or [],
        "recent_resolutions": (store.get("resolutions") or [])[-10:],
        "recent_feedback": recent_feedback,
    }


def write_dashboard_data(metrics: Dict[str, Any], output_path: Path) -> Path:
    """Writes dashboard metrics JSON for static or API consumption."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2, default=str)
    return output_path


def write_dashboard_html(metrics: Dict[str, Any], output_path: Path) -> Path:
    """Writes a standalone HTML dashboard with embedded metrics."""
    payload = json.dumps(metrics, default=str)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Calibration Learning Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2"></script>
  <style>
    :root {{
      --bg: #0b0d14;
      --panel: rgba(255,255,255,0.03);
      --border: rgba(255,255,255,0.08);
      --text: #e8edf2;
      --muted: #90a4ae;
      --cyan: #22d3ee;
      --green: #34d399;
      --amber: #fbbf24;
      --red: #f87171;
      --indigo: #818cf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, sans-serif;
      background: radial-gradient(circle at top, #121826, var(--bg));
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      padding: 28px 32px 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      align-items: center;
    }}
    header h1 {{
      margin: 0;
      font-family: Outfit, sans-serif;
      font-size: 1.6rem;
    }}
    header p {{ margin: 6px 0 0; color: var(--muted); font-size: 0.92rem; }}
    .wrap {{ padding: 24px 32px 48px; display: flex; flex-direction: column; gap: 20px; }}
    .grid {{ display: grid; gap: 16px; }}
    .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
    }}
    .stat-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 600;
    }}
    .stat-value {{
      margin-top: 8px;
      font-size: 1.8rem;
      font-weight: 800;
      font-family: Outfit, sans-serif;
    }}
    h2 {{
      margin: 0 0 14px;
      font-family: Outfit, sans-serif;
      font-size: 1.05rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.86rem;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .chart-box {{ height: 280px; position: relative; }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
    }}
    .badge-complete {{ background: rgba(52,211,153,0.15); color: var(--green); }}
    .badge-progress {{ background: rgba(251,191,36,0.15); color: var(--amber); }}
    .badge-pending {{ background: rgba(248,113,113,0.12); color: var(--red); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }}
    .muted {{ color: var(--muted); }}
    .paper-title {{ max-width: 520px; }}
    .checklist-item {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
    }}
    .checklist-item:last-child {{ border-bottom: none; }}
    footer {{
      padding: 0 32px 32px;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    code {{ color: var(--cyan); }}
    .layout-main {{
      display: grid;
      grid-template-columns: minmax(240px, 280px) minmax(0, 1fr);
      gap: 20px;
      align-items: start;
    }}
    .node-tree {{
      position: sticky;
      top: 16px;
    }}
    .node-tree h2 {{ margin-bottom: 10px; }}
    .node-item {{
      display: block;
      width: 100%;
      text-align: left;
      border: none;
      background: transparent;
      color: var(--text);
      padding: 8px 10px;
      border-radius: 8px;
      cursor: pointer;
      font: inherit;
      font-size: 0.84rem;
      margin-bottom: 2px;
    }}
    .node-item:hover {{ background: rgba(255,255,255,0.04); }}
    .node-item.active {{
      background: rgba(34,211,238,0.12);
      box-shadow: inset 3px 0 0 var(--cyan);
    }}
    .node-item.depth-1 {{ padding-left: 22px; font-size: 0.8rem; color: #c5d0db; }}
    .node-meta {{
      display: block;
      font-size: 0.72rem;
      color: var(--muted);
      margin-top: 2px;
    }}
    .breadcrumb {{
      color: var(--muted);
      font-size: 0.85rem;
      margin-bottom: 12px;
    }}
    .breadcrumb strong {{ color: var(--cyan); }}
    .node-detail-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      align-items: baseline;
      margin-bottom: 14px;
    }}
    .node-detail-head h2 {{ margin: 0; }}
    .layer-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .layer-card {{
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
      background: rgba(255,255,255,0.02);
    }}
    .layer-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .collapse-panel > summary {{
      list-style: none;
      cursor: pointer;
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 16px;
      user-select: none;
    }}
    .collapse-panel > summary::-webkit-details-marker {{ display: none; }}
    .collapse-panel > summary::before {{
      content: '▸';
      display: inline-block;
      margin-right: 8px;
      color: var(--muted);
      transition: transform 0.15s ease;
    }}
    .collapse-panel[open] > summary::before {{ transform: rotate(90deg); }}
    .collapse-panel-title {{
      font-family: Outfit, sans-serif;
      font-size: 1.05rem;
      font-weight: 600;
    }}
    .collapse-panel-hint {{
      font-size: 0.82rem;
      flex: 1 1 220px;
      text-align: right;
    }}
    .resolve-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 12px;
    }}
    .resolve-btn {{
      border: 1px solid var(--border);
      background: rgba(34,211,238,0.12);
      color: var(--text);
      border-radius: 8px;
      padding: 8px 14px;
      cursor: pointer;
      font: inherit;
      font-size: 0.84rem;
    }}
    .resolve-btn:hover {{ background: rgba(34,211,238,0.22); }}
    .resolve-btn.primary {{
      background: rgba(52,211,153,0.18);
      border-color: rgba(52,211,153,0.35);
    }}
    #rl-node-progress-table tbody tr.rl-node-row {{
      cursor: pointer;
      transition: background 0.12s ease;
    }}
    #rl-node-progress-table tbody tr.rl-node-row:hover {{
      background: rgba(34,211,238,0.08);
    }}
    #rl-node-progress-table tbody tr.rl-node-row.selected {{
      background: rgba(34,211,238,0.14);
      outline: 1px solid rgba(34,211,238,0.35);
    }}
    .rl-learning-timeline {{
      margin: 0 0 16px;
      padding: 14px 16px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: rgba(255,255,255,0.02);
    }}
    .rl-learning-event {{
      position: relative;
      padding: 0 0 16px 18px;
      border-left: 2px solid rgba(34,211,238,0.35);
      margin-left: 6px;
    }}
    .rl-learning-event:last-child {{
      padding-bottom: 0;
    }}
    .rl-learning-event::before {{
      content: '';
      position: absolute;
      left: -7px;
      top: 4px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #22d3ee;
      border: 2px solid var(--bg);
    }}
    .rl-learning-event.kind-batch_run::before {{
      background: #a78bfa;
    }}
    .disagreement-card {{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
      background: rgba(255,255,255,0.02);
    }}
    .disagreement-card.resolved {{
      opacity: 0.72;
      border-color: rgba(52,211,153,0.35);
    }}
    .field-row-head,
    .field-row {{
      display: grid;
      grid-template-columns: 108px minmax(100px, 1fr) minmax(100px, 1fr) minmax(140px, 0.9fr) minmax(220px, 1.5fr);
      gap: 8px 10px;
      align-items: center;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      font-size: 0.82rem;
    }}
    .field-row:last-child {{ border-bottom: none; }}
    .field-row-head {{
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding-top: 0;
    }}
    .field-value-cell {{
      padding: 6px 8px;
      border-radius: 6px;
      background: rgba(255,255,255,0.03);
      line-height: 1.35;
      word-break: break-word;
    }}
    .field-value-cell.llm {{ border-left: 2px solid #22d3ee; }}
    .field-value-cell.maude {{ border-left: 2px solid #34d399; }}
    .agreed-fields-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 10px 0 12px;
      padding: 10px 12px;
      border-radius: 8px;
      background: rgba(52,211,153,0.08);
      border: 1px solid rgba(52,211,153,0.18);
      font-size: 0.82rem;
    }}
    .agreed-field-chip {{
      display: inline-flex;
      gap: 6px;
      align-items: baseline;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
    }}
    .agreed-field-chip .label {{
      color: var(--muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .field-resolve-select,
    .field-resolve-comment {{
      width: 100%;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,0.18);
      color: var(--text);
      padding: 7px 8px;
      font: inherit;
      font-size: 0.82rem;
    }}
    .field-resolve-comment::placeholder {{ color: var(--muted); }}
    .feedback-feed {{
      margin-top: 12px;
      font-size: 0.82rem;
    }}
    .feedback-feed-item {{
      padding: 8px 0;
      border-bottom: 1px solid var(--border);
    }}
    .serve-banner {{
      margin: 0 32px 16px;
      padding: 12px 16px;
      border-radius: 10px;
      border: 1px solid rgba(251,191,36,0.35);
      background: rgba(251,191,36,0.12);
      color: #fde68a;
      font-size: 0.88rem;
      line-height: 1.5;
    }}
    .serve-banner a {{ color: #67e8f9; }}
    .layer-head h3 {{
      margin: 0;
      font-size: 0.95rem;
      color: var(--cyan);
    }}
    .layer-stat-row {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: 0.82rem;
      padding: 4px 0;
    }}
    .progress-track {{
      height: 8px;
      background: rgba(255,255,255,0.08);
      border-radius: 999px;
      overflow: hidden;
      margin: 8px 0;
    }}
    .progress-fill {{
      height: 100%;
      background: linear-gradient(90deg, var(--indigo), var(--cyan));
      border-radius: 999px;
    }}
    .ready-when {{
      margin-top: 8px;
      padding: 8px 10px;
      border-left: 3px solid var(--amber);
      background: rgba(251,191,36,0.08);
      font-size: 0.78rem;
      color: #e8d5a3;
    }}
    .badge-eval {{ background: rgba(251,191,36,0.2); color: #fcd34d; }}
    .badge-blocked {{ background: rgba(248,113,113,0.18); color: #fca5a5; }}
    .chart-box-sm {{ height: 180px; position: relative; }}
    @media (max-width: 960px) {{
      .layout-main {{ grid-template-columns: 1fr; }}
      .node-tree {{ position: static; }}
      .layer-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Calibration Learning Dashboard</h1>
      <p>Decision-tree traversal · rules v<span id="rules-version"></span> · generated <span id="generated-at"></span></p>
    </div>
    <div class="mono muted">Refresh: <code>python3 calibration_metrics.py --build-dashboard</code></div>
  </header>
  <div id="serve-mode-banner" class="serve-banner" style="display:none"></div>
  <div id="auth-banner" class="serve-banner" style="display:none"></div>
  <div id="calibration-lock-banner" class="serve-banner" style="display:none"></div>
  <div class="wrap">
    <div class="layout-main">
      <aside class="card node-tree">
        <h2>Decision Tree</h2>
        <div class="muted" style="font-size:0.8rem;margin-bottom:10px">Select a node — all panels filter to that node and downstream papers.</div>
        <div id="node-tree"></div>
      </aside>
      <div class="dashboard-main">
        <div class="breadcrumb" id="node-breadcrumb"></div>
        <div class="node-detail-head">
          <h2 id="node-title">All calibration runs</h2>
          <div class="mono muted" id="node-subtitle"></div>
        </div>
        <div class="grid grid-4" id="summary-cards"></div>

        <div class="card" id="rl-progress-section">
          <h2>RL Progress by Node <span class="muted" style="font-size:0.82rem;font-weight:400">· alignment + Maude recall · 90% gate</span></h2>
          <div class="muted" style="font-size:0.82rem;margin-bottom:12px" id="rl-progress-summary">
            Track Maude vs Claude field alignment and Maude recall on Claude-populated fields per sub-node run.
          </div>
          <div class="muted" style="font-size:0.78rem;margin-bottom:8px">Click an active node row to view its learning sequence (handoffs and batch runs).</div>
          <div style="overflow-x:auto;margin-bottom:16px">
            <table id="rl-node-progress-table">
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Phase</th>
                  <th>Status</th>
                  <th>Runs</th>
                  <th>Latest alignment</th>
                  <th>Latest Maude recall</th>
                  <th>Consecutive pass</th>
                </tr>
              </thead>
              <tbody id="rl-node-progress-body">
                <tr><td colspan="7" class="muted">No RL runs yet — execute a sub-node batch to populate this table.</td></tr>
              </tbody>
            </table>
          </div>
          <div id="rl-node-learning-detail" class="rl-learning-timeline" style="display:none;margin-bottom:16px"></div>
          <div class="grid grid-2" style="margin-bottom:12px">
            <div>
              <h3 style="font-size:0.9rem;margin:0 0 8px">Field Alignment Over Runs (Maude vs Claude)</h3>
              <div class="muted" style="font-size:0.78rem;margin-bottom:8px">Percent of in-scope fields where Maude matches Claude ground truth.</div>
              <div class="chart-box-sm"><canvas id="chart-rl-alignment"></canvas></div>
            </div>
            <div>
              <h3 style="font-size:0.9rem;margin:0 0 8px">Maude Recall vs Claude Fields</h3>
              <div class="muted" style="font-size:0.78rem;margin-bottom:8px">Percent of Claude-populated in-scope fields where Maude extracted any value (includes misses).</div>
              <div class="chart-box-sm"><canvas id="chart-rl-maude-recall"></canvas></div>
            </div>
          </div>
          <h3 style="font-size:0.9rem;margin:0 0 8px">Alignment by Content Tier</h3>
          <div class="muted" style="font-size:0.78rem;margin-bottom:8px">PDF-extracted batches use full field scope; abstract tiers use routing/coarse fields only.</div>
          <div class="grid grid-2" style="margin-bottom:12px">
            <div>
              <h4 style="font-size:0.85rem;margin:0 0 6px">PDF extracted (llm-pdf-reclassify)</h4>
              <div class="chart-box-sm"><canvas id="chart-rl-tier-pdf-alignment"></canvas></div>
            </div>
            <div>
              <h4 style="font-size:0.85rem;margin:0 0 6px">Abstract reclassify (llm-reclassify)</h4>
              <div class="chart-box-sm"><canvas id="chart-rl-tier-abstract-alignment"></canvas></div>
            </div>
          </div>
          <div id="subnode-promotion-panel" class="muted"></div>
          <div id="handoff-learning-log-panel" style="margin-top:12px"></div>
          <div id="staged-patches-panel" style="margin-top:12px"></div>
        </div>

        <div class="card">
          <details class="collapse-panel" id="automation-layers-section">
            <summary>
              <span class="collapse-panel-title">Automation Layers <span class="muted" style="font-size:0.82rem;font-weight:400">· agent_automation_plan.md</span></span>
              <span class="muted collapse-panel-hint" id="automation-layers-summary">Static until automations run</span>
            </summary>
            <div class="collapse-panel-body">
              <div class="layer-grid" id="automation-layers"></div>
              <div class="grid grid-2" style="margin-top:16px">
                <div>
                  <h3 style="font-size:0.9rem;margin:0 0 8px">BM25 Upward Propagation Over Time</h3>
                  <div class="chart-box-sm"><canvas id="chart-bm25-timeline"></canvas></div>
                </div>
                <div>
                  <h3 style="font-size:0.9rem;margin:0 0 8px">Optimization Log (Hamming / Gate)</h3>
                  <div style="overflow-x:auto" id="optimization-log-table"></div>
                </div>
              </div>
            </div>
          </details>
        </div>

        <div class="card">
          <h2>Maude vs LLM <span class="muted" style="font-size:0.82rem;font-weight:400">· parallel rule classifier A/B</span></h2>
          <div class="grid grid-4" id="maude-summary-cards"></div>
          <div class="muted" id="maude-promotion-detail" style="margin:8px 0 16px;font-size:0.85rem"></div>
          <div class="grid grid-2" style="margin-bottom:16px">
            <div>
              <h3 style="font-size:0.9rem;margin:0 0 8px">Promotion Fields Agreement (publication_type + study_type)</h3>
              <div class="chart-box-sm"><canvas id="chart-maude-batch-agreement"></canvas></div>
            </div>
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
                <h3 style="font-size:0.9rem;margin:0">Fields Agreement Over Time</h3>
                <label class="muted" style="font-size:0.82rem;display:flex;align-items:center;gap:6px">
                  Characteristic
                  <select id="maude-field-agreement-select" class="field-resolve-select" style="min-width:160px" onchange="onAgreementFieldChange(this.value)"></select>
                </label>
              </div>
              <div class="muted" style="font-size:0.78rem;margin:-2px 0 8px">Agreement rate for the selected field · papers in current node scope · tracked per batch</div>
              <div class="chart-box-sm"><canvas id="chart-maude-field-agreement-timeline"></canvas></div>
            </div>
          </div>
          <h3 style="font-size:0.9rem;margin:0 0 8px">Disagreement Review Queue</h3>
          <div class="muted" style="font-size:0.82rem;margin-bottom:10px">Pick the correct value per field, add a field-specific comment for each row you want to teach Maude, then resolve — only commented rows are submitted.</div>
          <div id="maude-feedback-feed" class="feedback-feed"></div>
          <div id="maude-disagreement-queue"></div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h2>Maude vs LLM Confidence</h2>
            <div class="muted" style="font-size:0.82rem;margin-bottom:8px">Average classifier confidence for papers in the selected node scope.</div>
            <div class="chart-box"><canvas id="chart-maude-confidence"></canvas></div>
          </div>
          <div class="card">
            <h2>Field Agreement Explorer</h2>
            <div class="muted" style="font-size:0.82rem;margin-bottom:8px">Per-field Maude vs LLM agreement for the selected node scope.</div>
            <div class="chart-box"><canvas id="chart-maude-field-agreement"></canvas></div>
          </div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h2>Batch Timeline</h2>
            <div style="overflow-x:auto">
              <table id="batch-table">
                <thead>
                  <tr>
                    <th>Batch</th>
                    <th>Node</th>
                    <th>Papers</th>
                    <th>HL Changed</th>
                    <th>Avg Conf</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
          <div class="card">
            <h2>Automation Readiness</h2>
            <div id="readiness-list"></div>
          </div>
        </div>

        <div class="card">
          <h2 id="node-papers-heading">Maude A/B papers</h2>
          <div class="muted" id="node-papers-subtitle" style="font-size:0.82rem;margin:-6px 0 12px"></div>
          <div style="overflow-x:auto">
            <table id="node-papers-table">
              <thead>
                <tr>
                  <th>Paper</th>
                  <th>Sub-node</th>
                  <th>Conf</th>
                  <th>Variant</th>
                  <th>Pub type</th>
                  <th>HL Fields</th>
                  <th>Batch</th>
                  <th>Title</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>

        <div class="card">
          <h2>Learned Decision Boundaries</h2>
          <div id="boundaries"></div>
        </div>

        <div class="card">
          <h2>Priority Expert Review Queue</h2>
          <div style="overflow-x:auto">
            <table id="review-table">
              <thead>
                <tr>
                  <th>Paper</th>
                  <th>Conf</th>
                  <th>Variant</th>
                  <th>HL Fields</th>
                  <th>Title</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
  <footer>
    API: <code>/api/calibration/dashboard-metrics</code> · Open from the catalog <strong>Learning Dashboard</strong> when signed in as admin
  </footer>
  <script>
    const METRICS = {payload};
    let selectedNodeId = 'all';
    let chartMaudeBatch = null;
    let chartMaudeFieldTimeline = null;
    let chartMaudeConfidence = null;
    let chartMaudeFieldAgreement = null;
    let chartBm25 = null;
    let chartRlAlignment = null;
    let chartRlExtraction = null;
    let chartRlTierPdf = null;
    let chartRlTierAbstract = null;
    let authStatus = {{ logged_in: false, is_admin: false, login_url: '/login?next=/calibration/dashboard' }};
    let selectedAgreementField = null;

    const NODE_DOWNSTREAM = METRICS.node_downstream || (METRICS.node_traversal && METRICS.node_traversal.downstream) || {{}};
    const NODE_CHARACTERISTICS = METRICS.node_characteristics || {{}};

    function isLiveDashboard() {{
      return window.location.protocol === 'http:' || window.location.protocol === 'https:';
    }}

    function apiUrl(path) {{
      return `${{window.location.origin}}${{path.startsWith('/') ? path : `/${{path}}`}}`;
    }}

    function renderServeModeBanner() {{
      const banner = document.getElementById('serve-mode-banner');
      if (!banner) return;
      if (isLiveDashboard()) {{
        banner.style.display = 'none';
        return;
      }}
      banner.style.display = 'block';
      banner.innerHTML =
        '<strong>Static file mode</strong> — Resolve &amp; teach Maude requires the running app (not <code>open dashboard.html</code>). ' +
        'Use <a href="http://127.0.0.1:5001/calibration/dashboard">http://127.0.0.1:5001/calibration/dashboard</a> locally ' +
        'or <a href="https://cannabis-paper-scraper.fly.dev/calibration/dashboard">cannabis-paper-scraper.fly.dev/calibration/dashboard</a> in production. ' +
        'You must be logged in as admin.';
    }}

    async function refreshAuthStatus() {{
      if (!isLiveDashboard()) return;
      try {{
        const response = await fetch(apiUrl('/api/calibration/auth-status'), {{ credentials: 'same-origin' }});
        if (!response.ok) return;
        authStatus = await response.json();
      }} catch (error) {{
        // Keep default login_url when auth probe fails.
      }}
    }}

    function renderAuthBanner() {{
      const banner = document.getElementById('auth-banner');
      if (!banner || !isLiveDashboard()) {{
        if (banner) banner.style.display = 'none';
        return;
      }}
      if (authStatus.logged_in && authStatus.is_admin) {{
        banner.style.display = 'none';
        return;
      }}
      banner.style.display = 'block';
      const loginUrl = authStatus.login_url || '/login?next=/calibration/dashboard';
      if (!authStatus.logged_in) {{
        banner.innerHTML =
          '<strong>Sign in required</strong> — Resolve &amp; teach Maude needs an admin session. ' +
          `<a href="${{loginUrl}}">Sign in</a> (use an admin email), then return here.`;
        return;
      }}
      banner.innerHTML =
        '<strong>Admin access required</strong> — You are signed in as ' +
        `${{escapeHtml(authStatus.email || 'unknown')}} but this action is restricted to administrators.`;
    }}

    function getScopeSubnodes(nodeId) {{
      if (nodeId === 'all') return null;
      return new Set(NODE_DOWNSTREAM[nodeId] || [nodeId]);
    }}

    function recordInNodeScope(record, nodeId) {{
      const scope = getScopeSubnodes(nodeId);
      if (!scope) return true;
      return scope.has(record.routing_subnode);
    }}

    function filteredPairedRecords() {{
      const records = ((METRICS.maude_comparison || {{}}).paired_records) || [];
      if (selectedNodeId === 'all') return records;
      return records.filter(row => recordInNodeScope(row, selectedNodeId));
    }}

    function normalizeFieldValue(value) {{
      if (value == null) return null;
      if (Array.isArray(value)) return value.map(item => String(item).toLowerCase()).sort().join('|');
      return String(value).trim().toLowerCase();
    }}

    function fieldsForSelectedNode() {{
      const fields = NODE_CHARACTERISTICS[selectedNodeId] || NODE_CHARACTERISTICS.all || [];
      return fields.length ? fields : ['publication_type', 'study_type'];
    }}

    function groupRecordsByBatch(records) {{
      const grouped = {{}};
      records.forEach(row => {{
        if (!grouped[row.batch_id]) grouped[row.batch_id] = [];
        grouped[row.batch_id].push(row);
      }});
      return grouped;
    }}

    function computeNodeScopedBatchFieldTimeline(field) {{
      const mc = METRICS.maude_comparison || {{}};
      const timeline = mc.batch_timeline || [];
      const allRecords = mc.paired_records || [];
      return timeline.map(batch => {{
        const batchRecords = allRecords.filter(row =>
          row.batch_id === batch.batch_id && recordInNodeScope(row, selectedNodeId)
        );
        if (!batchRecords.length) return null;
        const stats = computeFieldAgreementFromRecords(batchRecords, [field]);
        const agreement = stats[field]?.agreement_rate;
        return {{
          batch_id: batch.batch_id,
          created_at: batch.created_at,
          paired_count: batchRecords.length,
          agreement_rate: agreement == null ? null : agreement,
        }};
      }}).filter(Boolean);
    }}

    function onAgreementFieldChange(field) {{
      selectedAgreementField = field;
      renderMaudeFieldAgreementTimeline();
    }}

    function renderMaudeFieldAgreementTimeline() {{
      const fields = fieldsForSelectedNode();
      if (!selectedAgreementField || !fields.includes(selectedAgreementField)) {{
        selectedAgreementField = fields[0] || 'publication_type';
      }}
      const select = document.getElementById('maude-field-agreement-select');
      if (select) {{
        select.innerHTML = fields.map(field =>
          `<option value="${{field}}"${{field === selectedAgreementField ? ' selected' : ''}}>${{field}}</option>`
        ).join('');
      }}
      const rows = computeNodeScopedBatchFieldTimeline(selectedAgreementField);
      if (chartMaudeFieldTimeline) chartMaudeFieldTimeline.destroy();
      const canvas = document.getElementById('chart-maude-field-agreement-timeline');
      if (!canvas) return;
      if (!rows.length) {{
        chartMaudeFieldTimeline = new Chart(canvas, {{
          type: 'line',
          data: {{ labels: ['No batches in scope'], datasets: [{{ data: [0] }}] }},
          options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }},
        }});
        return;
      }}
      chartMaudeFieldTimeline = new Chart(canvas, {{
        type: 'line',
        data: {{
          labels: rows.map(row => (row.batch_id || '').replace(/^node1_calibration_/, 'n1_').slice(-14)),
          datasets: [{{
            label: `${{selectedAgreementField}} agreement rate`,
            data: rows.map(row => row.agreement_rate ?? 0),
            borderColor: '#22d3ee',
            backgroundColor: 'rgba(34,211,238,0.15)',
            tension: 0.25,
            fill: true,
          }}],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
          plugins: {{
            tooltip: {{
              callbacks: {{
                afterLabel: ctx => {{
                  const row = rows[ctx.dataIndex];
                  return row ? `Papers in scope: ${{row.paired_count}}` : '';
                }},
              }},
            }},
          }},
        }},
      }});
    }}

    function computeFieldAgreementFromRecords(records, fields) {{
      const totals = {{}};
      fields.forEach(field => {{ totals[field] = {{ agree: 0, disagree: 0 }}; }});
      records.forEach(row => {{
        fields.forEach(field => {{
          const maudeVal = row.maude && row.maude[field];
          const llmVal = row.llm && row.llm[field];
          if (normalizeFieldValue(maudeVal) === normalizeFieldValue(llmVal)) totals[field].agree += 1;
          else totals[field].disagree += 1;
        }});
      }});
      const out = {{}};
      fields.forEach(field => {{
        const stats = totals[field];
        const total = stats.agree + stats.disagree;
        out[field] = {{
          agree: stats.agree,
          disagree: stats.disagree,
          agreement_rate: total ? stats.agree / total : null,
          disagreement_rate: total ? stats.disagree / total : null,
        }};
      }});
      return out;
    }}

    function formatMaudeNodes(nodes) {{
      const deduped = [...new Set((nodes || []).map(node => String(node).replace('node0_ingestion', 'node0')))];
      return deduped.slice(-4).join(' → ') || '—';
    }}

    function filteredBatchIds() {{
      return new Set(filteredBatches().map(batch => batch.batch_id));
    }}

    function formatMaudeAbEpoch() {{
      const epoch = METRICS.maude_ab_epoch || {{}};
      if (!epoch.started_at) return 'No native Maude A/B batches yet';
      const batchLabel = (epoch.first_batch_id || '').replace(/^node1_calibration_/, 'n1_').replace(/^calibration_/, '');
      return `Since ${{epoch.started_at.replace('T', ' ').slice(0, 16)}} (${{batchLabel}}) · ${{epoch.paired_papers}} paired papers · ${{epoch.batch_count}} batch(es)`;
    }}

    function pct(value) {{
      return value == null ? '—' : (value * 100).toFixed(1) + '%';
    }}

    function layerBadge(status) {{
      const map = {{
        complete: 'badge-complete',
        active: 'badge-complete',
        current: 'badge-complete',
        idle: 'badge-pending',
        pending: 'badge-pending',
        in_progress: 'badge-progress',
        eval_due: 'badge-eval',
        re_eval_recommended: 'badge-eval',
        needs_human_review: 'badge-blocked',
        blocked: 'badge-blocked',
        stale: 'badge-pending',
      }};
      const cls = map[status] || 'badge-pending';
      return `<span class="badge ${{cls}}">${{(status || 'unknown').replace(/_/g, ' ')}}</span>`;
    }}

    function badge(status) {{
      const cls = status === 'complete' ? 'badge-complete' : (status === 'in_progress' ? 'badge-progress' : 'badge-pending');
      return `<span class="badge ${{cls}}">${{status.replace('_', ' ')}}</span>`;
    }}

    function getNode(id) {{
      return (METRICS.node_traversal && METRICS.node_traversal.nodes && METRICS.node_traversal.nodes[id])
        || (METRICS.node_traversal && METRICS.node_traversal.nodes && METRICS.node_traversal.nodes.all)
        || {{ stats: {{}}, papers: [], batch_ids: [], label: 'All calibration runs' }};
    }}

    function filteredBatches() {{
      const node = getNode(selectedNodeId);
      const allowed = new Set(node.batch_ids || []);
      if (selectedNodeId === 'all') return METRICS.batches || [];
      return (METRICS.batches || []).filter(batch => allowed.has(batch.batch_id));
    }}

    function filteredReviewRows() {{
      const node = getNode(selectedNodeId);
      const allowed = new Set((node.papers || []).map(row => row.paper_id));
      if (selectedNodeId === 'all') return METRICS.priority_review || [];
      return (METRICS.priority_review || []).filter(row => allowed.has(row.paper_id));
    }}

    function nodeLabel(id) {{
      const node = getNode(id);
      return node.label || id;
    }}

    function selectNode(id) {{
      selectedNodeId = id;
      renderDashboard();
    }}

    function originalResearchCount() {{
      const traversal = METRICS.node_traversal || {{}};
      if (traversal.original_research_paper_count) return traversal.original_research_paper_count;
      const node2Stats = ((traversal.nodes || {{}}).node2 || {{}}).stats || {{}};
      return node2Stats.original_research_count || 0;
    }}

    function nodeTreeVisible(node) {{
      if (node.id === 'all') return true;
      const stats = (METRICS.node_traversal.nodes[node.id] || {{}}).stats || {{}};
      if ((stats.paper_count || 0) > 0) return true;
      if (node.id === 'node2') return originalResearchCount() > 0;
      if (node.parent === 'node2' && originalResearchCount() > 0) {{
        return (stats.paper_count || 0) > 0;
      }}
      return false;
    }}

    function renderNodeTree() {{
      const tree = (METRICS.node_traversal && METRICS.node_traversal.tree) || [];
      document.getElementById('node-tree').innerHTML = tree.map(node => {{
        if (!nodeTreeVisible(node)) return '';
        const stats = (METRICS.node_traversal.nodes[node.id] || {{}}).stats || {{}};
        const count = stats.paper_count || 0;
        const active = node.id === selectedNodeId ? ' active' : '';
        const depthClass = node.depth ? ` depth-${{node.depth}}` : '';
        const originalHint = node.id === 'node2' && count === 0 && originalResearchCount() > 0
          ? ` · ${{originalResearchCount()}} original`
          : '';
        return `<button type="button" class="node-item${{depthClass}}${{active}}" data-node="${{node.id}}" onclick="selectNode('${{node.id}}')">
          ${{node.label}}
          <span class="node-meta">${{count}} papers · ${{stats.batch_count || 0}} batches${{originalHint}}</span>
        </button>`;
      }}).join('');
    }}

    function renderBreadcrumb() {{
      const node = getNode(selectedNodeId);
      const crumbs = [node.label || selectedNodeId];
      let parent = node.parent;
      while (parent) {{
        crumbs.unshift(nodeLabel(parent));
        parent = (getNode(parent).parent || null);
      }}
      document.getElementById('node-breadcrumb').innerHTML = crumbs.map((part, idx) =>
        idx === crumbs.length - 1 ? `<strong>${{part}}</strong>` : part
      ).join(' › ');
      document.getElementById('node-title').textContent = node.label || selectedNodeId;
      const stats = node.stats || {{}};
      document.getElementById('node-subtitle').textContent =
        `${{stats.paper_count || 0}} papers · ${{stats.batch_count || 0}} batches · avg conf ${{pct(stats.avg_confidence)}}`;
    }}

    function renderSummary() {{
      const stats = getNode(selectedNodeId).stats || METRICS.summary;
      document.getElementById('rules-version').textContent = METRICS.rules_version || '—';
      document.getElementById('generated-at').textContent = (METRICS.generated_at || '').replace('T', ' ').slice(0, 19);
      const cards = [
        ['Maude A/B papers', stats.paper_count ?? METRICS.summary.total_papers, 'var(--cyan)'],
        ['Maude A/B batches', stats.batch_count ?? METRICS.summary.batch_count, 'var(--indigo)'],
        ['HL Field Changes', stats.high_level_changed ?? METRICS.summary.high_level_changed, 'var(--amber)'],
        ['Avg Confidence', pct(stats.avg_confidence ?? METRICS.summary.avg_confidence), 'var(--green)'],
      ];
      document.getElementById('summary-cards').innerHTML = cards.map(([label, value, color]) => `
        <div class="card">
          <div class="stat-label">${{label}}</div>
          <div class="stat-value" style="color:${{color}}">${{value}}</div>
        </div>`).join('');
    }}

    function renderBatchTable() {{
      const tbody = document.querySelector('#batch-table tbody');
      const batches = filteredBatches();
      tbody.innerHTML = batches.length ? batches.map(batch => `
        <tr>
          <td class="mono">${{batch.batch_id.replace(/^node1_calibration_/, 'n1_').replace(/^calibration_/, '')}}</td>
          <td class="mono">${{batch.automation_node || batch.mode || '—'}}</td>
          <td>${{batch.paper_count}}</td>
          <td>${{batch.high_level_changed}} (${{pct(batch.high_level_change_rate)}})</td>
          <td>${{pct(batch.avg_confidence)}}</td>
          <td>$${{(batch.total_cost || 0).toFixed(2)}}</td>
        </tr>`).join('') : '<tr><td colspan="6" class="muted">No batches for this node.</td></tr>';
    }}

    function renderNodePapersTable() {{
      const papers = getNode(selectedNodeId).papers || [];
      const tbody = document.querySelector('#node-papers-table tbody');
      document.getElementById('node-papers-heading').textContent =
        `Maude A/B papers in ${{nodeLabel(selectedNodeId)}} (${{papers.length}})`;
      const subtitle = document.getElementById('node-papers-subtitle');
      if (subtitle) subtitle.textContent = formatMaudeAbEpoch();
      tbody.innerHTML = papers.length ? papers.map(row => `
        <tr>
          <td class="mono">${{row.paper_id}}<div class="muted">${{row.pmid || ''}}</div></td>
          <td class="mono">${{row.routing_subnode || '—'}}</td>
          <td>${{pct(row.confidence)}}</td>
          <td class="mono">${{row.variant || '—'}}</td>
          <td class="mono">${{row.publication_type || '—'}}</td>
          <td class="mono">${{(row.high_level_fields || []).join(', ') || '—'}}</td>
          <td class="mono">${{(row.batch_id || '').replace(/^node1_calibration_/, 'n1_').replace(/^calibration_/, '')}}</td>
          <td class="paper-title">${{row.title || ''}}</td>
        </tr>`).join('') : '<tr><td colspan="8" class="muted">No papers calibrated under this node yet.</td></tr>';
    }}

    function renderReadiness() {{
      const readiness = METRICS.automation_readiness;
      document.getElementById('readiness-list').innerHTML = `
        <div class="muted" style="margin-bottom:12px">${{readiness.completed_count}} / ${{readiness.total_count}} complete · full automation ${{readiness.ready_for_full_automation ? 'ready' : 'blocked pending expert guidelines'}}</div>
        ${{readiness.checklist.map(item => `
          <div class="checklist-item">
            <div>
              <div>${{item.label}}</div>
              <div class="muted" style="font-size:0.82rem;margin-top:4px">${{item.detail}}</div>
              ${{item.ready_when && item.status !== 'complete' ? `<div class="ready-when">${{item.ready_when}}</div>` : ''}}
            </div>
            ${{badge(item.status)}}
          </div>`).join('')}}`;
    }}

    function renderAutomationLayers() {{
      const layers = METRICS.automation_layers || {{}};
      const feedback = layers.feedback_loop || {{}};
      const propagation = layers.upward_propagation || {{}};
      const optimization = layers.optimization_logging || {{}};
      const reliability = layers.reliability_eval || {{}};

      const cards = [
        {{
          title: '1 · Feedback Loop',
          status: feedback.status,
          rows: [
            ['Total corrections', feedback.total_corrections ?? 0],
            ['Papers corrected', feedback.unique_papers_corrected ?? 0],
            ['Since last eval', `${{feedback.corrections_since_eval ?? 0}} / ${{feedback.eval_threshold ?? 10}}`],
            ['FTS index ready', feedback.fts_index_ready ? 'yes' : 'no'],
            ['Last feedback', (feedback.last_feedback_timestamp || '—').replace('T', ' ').slice(0, 19)],
          ],
          progress: feedback.eval_progress_pct ?? 0,
          note: feedback.eval_due ? 'Eval threshold reached — run /api/classification/run-eval' : '',
        }},
        {{
          title: '2 · Upward Propagation (BM25)',
          status: propagation.status,
          rows: [
            ['Data source', propagation.source || '—'],
            ['Batches tracked', propagation.batch_count ?? 0],
            ['BM25 usage rate', pct(propagation.overall_bm25_rate)],
            ['Avg few-shot similarity', propagation.avg_few_shot_similarity ?? '—'],
          ],
          note: propagation.overall_bm25_rate > 0
            ? 'Few-shot context retrieved from feedback_audit via BM25 during classification.'
            : 'No BM25 retrievals yet — corrections must populate feedback_audit first.',
        }},
        {{
          title: '3 · Optimization Logging',
          status: optimization.status,
          rows: [
            ['Total runs', optimization.total_runs ?? 0],
            ['needs_human_review', optimization.needs_human_review_count ?? 0],
            ['Statuses', Object.entries(optimization.by_status || {{}}).map(([k,v]) => `${{k}}:${{v}}`).join(', ') || '—'],
          ],
          note: optimization.needs_human_review_count > 0
            ? 'Rule patches failed gate 3× — expert review required before promotion.'
            : 'Hamming distance + reward gate logged in optimization_log on rule_optimizer runs.',
        }},
        {{
          title: '4 · Reliability Eval',
          status: reliability.status,
          rows: [
            ['Manifest updated', (reliability.last_updated || '—').replace('T', ' ').slice(0, 19)],
            ['Reliable fields', `${{reliability.reliable_field_count ?? 0}} / ${{reliability.total_field_count ?? 0}}`],
            ['Threshold', pct(reliability.threshold)],
            ['Last eval (DB)', (reliability.last_eval_timestamp || '—').replace('T', ' ').slice(0, 19)],
          ],
          note: reliability.status === 're_eval_recommended'
            ? 'Feedback threshold crossed since last eval — re-run eval_reliability.py.'
            : `Manifest: ${{reliability.manifest_path || 'reliability_manifest.json'}}`,
        }},
      ];

      document.getElementById('automation-layers').innerHTML = cards.map(card => `
        <div class="layer-card">
          <div class="layer-head">
            <h3>${{card.title}}</h3>
            ${{layerBadge(card.status)}}
          </div>
          ${{card.rows.map(([label, value]) => `
            <div class="layer-stat-row"><span class="muted">${{label}}</span><span class="mono">${{value}}</span></div>
          `).join('')}}
          ${{card.progress != null ? `
            <div class="progress-track"><div class="progress-fill" style="width:${{Math.min(100, card.progress)}}%"></div></div>
            <div class="muted" style="font-size:0.75rem">Eval trigger progress: ${{card.progress}}%</div>
          ` : ''}}
          ${{card.note ? `<div class="muted" style="font-size:0.78rem;margin-top:8px">${{card.note}}</div>` : ''}}
        </div>`).join('');

      const summaryEl = document.getElementById('automation-layers-summary');
      if (summaryEl) {{
        const statusLine = cards.map(card => (card.status || 'pending').replace(/_/g, ' ')).join(' · ');
        summaryEl.textContent = `${{statusLine}} — expand when feedback loop / rule optimizer is active`;
      }}
    }}

    function setupAutomationLayersCollapse() {{
      const section = document.getElementById('automation-layers-section');
      if (!section || section.dataset.bound) return;
      section.dataset.bound = '1';
      section.addEventListener('toggle', () => {{
        if (section.open) renderBm25Chart();
      }});
    }}

    function renderOptimizationTable() {{
      const runs = ((METRICS.automation_layers || {{}}).optimization_logging || {{}}).recent_runs || [];
      const container = document.getElementById('optimization-log-table');
      if (!runs.length) {{
        container.innerHTML = '<div class="muted">No optimization_log entries yet. Runs appear after rule_optimizer executes post-feedback.</div>';
        return;
      }}
      container.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Reward</th>
              <th>Gate</th>
              <th>Attempts</th>
              <th>Field-group Hamming</th>
            </tr>
          </thead>
          <tbody>
            ${{runs.map(run => {{
              const scores = run.field_group_scores || {{}};
              const hamming = Object.entries(scores).map(([group, val]) => {{
                const score = typeof val === 'object' ? (val.score ?? val.hamming ?? JSON.stringify(val)) : val;
                return `${{group}}:${{score}}`;
              }}).join(', ') || '—';
              return `<tr>
                <td class="mono">${{(run.run_id || '').slice(0, 24)}}<div class="muted">${{(run.timestamp || '').replace('T',' ').slice(0,19)}}</div></td>
                <td>${{layerBadge(run.status)}}</td>
                <td>${{run.reward != null ? Number(run.reward).toFixed(3) : '—'}}</td>
                <td>${{run.gate_passed ? 'pass' : 'fail'}}</td>
                <td>${{run.failed_attempts ?? 0}}</td>
                <td class="mono" style="font-size:0.75rem">${{hamming}}</td>
              </tr>`;
            }}).join('')}}
          </tbody>
        </table>`;
    }}

    function renderBm25Chart() {{
      const allowed = filteredBatchIds();
      const timeline = (((METRICS.automation_layers || {{}}).upward_propagation || {{}}).timeline || [])
        .filter(row => selectedNodeId === 'all' || allowed.has(row.batch_id));
      if (chartBm25) chartBm25.destroy();
      const labels = timeline.map(row => (row.batch_id || '').replace(/^node1_calibration_/, 'n1_').replace(/^calibration_/, '').slice(-12));
      chartBm25 = new Chart(document.getElementById('chart-bm25-timeline'), {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{
              label: 'BM25 usage rate',
              data: timeline.map(row => row.bm25_usage_rate || 0),
              borderColor: '#22d3ee',
              backgroundColor: 'rgba(34,211,238,0.15)',
              tension: 0.25,
              yAxisID: 'y',
            }},
            {{
              label: 'Avg few-shot similarity',
              data: timeline.map(row => row.avg_few_shot_similarity || 0),
              borderColor: '#818cf8',
              backgroundColor: 'rgba(129,140,248,0.12)',
              tension: 0.25,
              yAxisID: 'y',
            }},
          ],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
        }},
      }});
    }}

    function renderBoundaries() {{
      const boundaries = METRICS.decision_boundaries || {{}};
      const entries = Object.entries(boundaries);
      if (!entries.length) {{
        document.getElementById('boundaries').innerHTML = '<div class="muted">No decision boundaries encoded yet.</div>';
        return;
      }}
      document.getElementById('boundaries').innerHTML = entries.map(([key, rule]) => `
        <div style="padding:12px 0;border-bottom:1px solid var(--border)">
          <div class="mono" style="color:var(--cyan);margin-bottom:6px">${{key}}</div>
          <div>${{rule.rule || ''}}</div>
          <div class="muted" style="margin-top:6px;font-size:0.82rem">Example: ${{rule.example || '—'}} · Source: ${{rule.source || '—'}}</div>
        </div>`).join('');
    }}

    function renderReviewTable() {{
      const rows = filteredReviewRows();
      const tbody = document.querySelector('#review-table tbody');
      tbody.innerHTML = rows.length ? rows.map(row => `
        <tr>
          <td class="mono">${{row.paper_id}}<div class="muted">${{row.pmid || ''}}</div></td>
          <td>${{pct(row.confidence)}}${{row.in_review_queue ? ' <span class="badge badge-progress">queue</span>' : ''}}</td>
          <td class="mono">${{row.variant}}</td>
          <td class="mono">${{(row.high_level_fields || []).join(', ')}}</td>
          <td class="paper-title">${{row.title || ''}}${{row.expert_status ? `<div class="muted" style="margin-top:4px">${{row.expert_status}}</div>` : ''}}</td>
        </tr>`).join('') : '<tr><td colspan="5" class="muted">No priority review papers in this node.</td></tr>';
    }}

    function renderMaudeFieldExplorer() {{
      const records = filteredPairedRecords();
      const mc = METRICS.maude_comparison || {{}};
      const fields = mc.high_level_fields || Object.keys(mc.field_agreement || {{}});
      const fieldAgreement = computeFieldAgreementFromRecords(records, fields);

      const maudeConf = records.filter(row => row.maude_confidence != null).map(row => Number(row.maude_confidence));
      const llmConf = records.filter(row => row.llm_confidence != null).map(row => Number(row.llm_confidence));
      const avgMaude = maudeConf.length ? maudeConf.reduce((a, b) => a + b, 0) / maudeConf.length : 0;
      const avgLlm = llmConf.length ? llmConf.reduce((a, b) => a + b, 0) / llmConf.length : 0;

      if (chartMaudeConfidence) chartMaudeConfidence.destroy();
      chartMaudeConfidence = new Chart(document.getElementById('chart-maude-confidence'), {{
        type: 'bar',
        data: {{
          labels: ['Maude', 'LLM'],
          datasets: [{{
            label: 'Avg confidence',
            data: [avgMaude, avgLlm],
            backgroundColor: ['rgba(52,211,153,0.55)', 'rgba(34,211,238,0.55)'],
            borderColor: ['#34d399', '#22d3ee'],
            borderWidth: 1,
          }}],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
          plugins: {{ legend: {{ display: false }} }},
        }},
      }});

      const fieldLabels = fields.filter(field => fieldAgreement[field]);
      if (chartMaudeFieldAgreement) chartMaudeFieldAgreement.destroy();
      chartMaudeFieldAgreement = new Chart(document.getElementById('chart-maude-field-agreement'), {{
        type: 'bar',
        data: {{
          labels: fieldLabels,
          datasets: [
            {{
              label: 'Agreement rate',
              data: fieldLabels.map(field => fieldAgreement[field].agreement_rate || 0),
              backgroundColor: 'rgba(52,211,153,0.55)',
              borderColor: '#34d399',
              borderWidth: 1,
            }},
            {{
              label: 'Disagreement rate',
              data: fieldLabels.map(field => fieldAgreement[field].disagreement_rate || 0),
              backgroundColor: 'rgba(248,113,113,0.45)',
              borderColor: '#f87171',
              borderWidth: 1,
            }},
          ],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, stacked: false, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
        }},
      }});
    }}

    function renderMaudeComparison() {{
      const mc = METRICS.maude_comparison || {{}};
      const records = filteredPairedRecords();
      const fields = mc.high_level_fields || Object.keys(mc.field_agreement || {{}});
      const fieldAgreement = computeFieldAgreementFromRecords(records, fields);
      const flagged = records.filter(row => row.flagged_for_review).length;
      const promotionDisagree = records.filter(row =>
        (row.fields_disagreeing || []).some(field => field === 'publication_type' || field === 'study_type')
      ).length;

      const openDisagreements = ((METRICS.maude_feedback || {{}}).open_count);
      const cards = [
        ['Paired papers', records.length, 'var(--cyan)'],
        ['Open disagreements', openDisagreements ?? flagged, 'var(--amber)'],
        ['Flagged rate', records.length ? ((flagged / records.length) * 100).toFixed(1) + '%' : '—', 'var(--amber)'],
        ['Promotion status', mc.promotion_status || 'no_maude_data', mc.promotion_ready ? 'var(--green)' : 'var(--muted)'],
      ];
      document.getElementById('maude-summary-cards').innerHTML = cards.map(([label, value, color]) => `
        <div class="card">
          <div class="stat-label">${{label}}</div>
          <div class="stat-value" style="color:${{color}};font-size:1.1rem">${{value}}</div>
        </div>`).join('');
      const threshold = mc.promotion_threshold_pct ?? 10;
      const promoRate = records.length
        ? promotionDisagree / records.length
        : mc.promotion_disagreement_rate;
      document.getElementById('maude-promotion-detail').innerHTML =
        `${{mc.promotion_detail || ''}}` +
        (promoRate != null ? ` · Promotion-field disagreement in scope: <strong>${{(promoRate * 100).toFixed(1)}}%</strong> (target ≤ ${{threshold}}%)` : '') +
        (selectedNodeId !== 'all' ? ` · Filtered to <strong>${{nodeLabel(selectedNodeId)}}</strong> + downstream` : '') +
        (mc.backfilled_from_title ? ' · <span style="color:var(--amber)">Some historical rows were backfilled from title-only Maude runs.</span>' : '');

      const allowed = filteredBatchIds();
      const allRecords = mc.paired_records || [];
      const timeline = (mc.batch_timeline || []).map(batch => {{
        const batchRecords = allRecords.filter(row =>
          row.batch_id === batch.batch_id && recordInNodeScope(row, selectedNodeId)
        );
        if (!batchRecords.length) return null;
        const promoDisagree = batchRecords.filter(row =>
          (row.fields_disagreeing || []).some(field => field === 'publication_type' || field === 'study_type')
        ).length;
        const flagged = batchRecords.filter(row => row.flagged_for_review).length;
        return {{
          batch_id: batch.batch_id,
          created_at: batch.created_at,
          paired_count: batchRecords.length,
          flagged_count: flagged,
          flagged_rate: flagged / batchRecords.length,
          promotion_disagree_count: promoDisagree,
          promotion_disagree_rate: promoDisagree / batchRecords.length,
          promotion_agreement_rate: 1 - (promoDisagree / batchRecords.length),
        }};
      }}).filter(Boolean);
      if (chartMaudeBatch) chartMaudeBatch.destroy();
      chartMaudeBatch = new Chart(document.getElementById('chart-maude-batch-agreement'), {{
        type: 'line',
        data: {{
          labels: timeline.map(row => (row.batch_id || '').replace(/^node1_calibration_/, 'n1_').slice(-14)),
          datasets: [
            {{
              label: 'Promotion agreement rate',
              data: timeline.map(row => row.promotion_agreement_rate || 0),
              borderColor: '#34d399',
              backgroundColor: 'rgba(52,211,153,0.15)',
              tension: 0.25,
            }},
            {{
              label: 'Flagged rate',
              data: timeline.map(row => 1 - (row.promotion_agreement_rate || 0)),
              borderColor: '#f87171',
              backgroundColor: 'rgba(248,113,113,0.12)',
              tension: 0.25,
            }},
          ],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
        }},
      }});

      renderMaudeFieldAgreementTimeline();
      renderDisagreementQueue();
    }}

    function escapeHtml(value) {{
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function formatFieldValue(value) {{
      if (value == null) return '—';
      if (Array.isArray(value)) return value.join(', ') || '—';
      return String(value);
    }}

    const FIELD_CANONICAL = {{
      ingestion_status: ['relevant', 'tangential', 'irrelevant', 'not_cannabis_related'],
      publication_type: ['original research', 'review', 'case study'],
      study_type: [
        'review', 'systematic review', 'meta-analysis', 'editorial', 'comment',
        'letter to the editor', 'perspectives paper', 'case study',
        'Clinical (RCT)', 'Clinical (observational)', 'Animal Models (Mouse)',
        'Animal Models (Rat)', 'Cell Culture (Cell Lines)',
      ],
      exposure_method: ['unknown', 'oral administration', 'injection cannabinoids', 'inhaled'],
      cannabis_type: ['unknown', 'pure cannabinoid', 'whole plant', 'synthetic cannabinoid'],
      outcome_domain: ['pain', 'oncology', 'other', 'anxiety', 'inflammation'],
      species: ['mouse', 'rat', 'non_human_primate'],
    }};

    function serializeFieldValue(value) {{
      return encodeURIComponent(JSON.stringify(value));
    }}

    function deserializeFieldValue(encoded) {{
      return JSON.parse(decodeURIComponent(encoded));
    }}

    function buildFieldOptions(field) {{
      const options = [];
      const seen = new Set();
      const isListField = Array.isArray(field.llm_value) || Array.isArray(field.maude_value);
      const addOption = (prefix, raw) => {{
        const key = normalizeFieldValue(raw);
        if (key == null || seen.has(key)) return;
        seen.add(key);
        options.push({{
          label: prefix ? `${{prefix}}: ${{formatFieldValue(raw)}}` : formatFieldValue(raw),
          raw,
        }});
      }};
      addOption('LLM', field.llm_value);
      addOption('Maude', field.maude_value);
      (FIELD_CANONICAL[field.field] || []).forEach(value => {{
        const raw = isListField ? [value] : value;
        addOption('', raw);
      }});
      return options;
    }}

    function renderFieldSelectOptions(field) {{
      return buildFieldOptions(field).map((option, idx) =>
        `<option value="${{serializeFieldValue(option.raw)}}"${{idx === 0 ? ' selected' : ''}}>${{escapeHtml(option.label)}}</option>`
      ).join('');
    }}

    function renderMaudeFeedbackFeed() {{
      const feedback = ((METRICS.maude_feedback || {{}}).recent_feedback) || [];
      const resolutions = ((METRICS.maude_feedback || {{}}).recent_resolutions) || [];
      const container = document.getElementById('maude-feedback-feed');
      if (!container) return;
      const rows = [];
      resolutions.slice(-3).reverse().forEach(row => {{
        rows.push(`<div class="feedback-feed-item"><strong>Resolved paper ${{row.paper_id}}</strong> · ${{escapeHtml(row.title || '')}}<div class="muted">${{(row.resolved_at || '').replace('T',' ').slice(0,19)}}</div></div>`);
      }});
      feedback.slice(0, 5).forEach(row => {{
        rows.push(`<div class="feedback-feed-item"><strong>${{escapeHtml(row.field_name)}}</strong> · paper ${{row.paper_id}}<div class="muted">${{escapeHtml(row.old_value || '')}} → ${{escapeHtml(row.new_value || '')}}</div></div>`);
      }});
      container.innerHTML = rows.length
        ? `<div class="muted" style="margin-bottom:6px">Feedback loop</div>${{rows.join('')}}`
        : '';
    }}

    function renderAgreedFields(paper) {{
      const agreed = paper.agreed_fields || [];
      if (!agreed.length) return '';
      const chips = agreed.map(row => `
        <span class="agreed-field-chip">
          <span class="label">${{escapeHtml(row.field)}}</span>
          <span>${{escapeHtml(formatFieldValue(row.value))}}</span>
        </span>`).join('');
      return `<div class="agreed-fields-row"><strong style="margin-right:6px">Agreed</strong>${{chips}}</div>`;
    }}

    function renderDisagreementQueue() {{
      renderMaudeFeedbackFeed();
      const queue = ((METRICS.maude_feedback || {{}}).disagreement_queue) || [];
      const container = document.getElementById('maude-disagreement-queue');
      if (!container) return;
      const scoped = queue.filter(row => recordInNodeScope(row, selectedNodeId));
      if (!scoped.length) {{
        container.innerHTML = '<div class="muted">No open disagreements in this node scope.</div>';
        return;
      }}
      container.innerHTML = scoped.map((paper, index) => `
        <div class="disagreement-card" id="disagreement-card-${{paper.paper_id}}">
          <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
            <div>
              <div class="mono" style="color:var(--cyan)">#${{index + 1}} · Paper ${{paper.paper_id}} · ${{paper.pmid || ''}}</div>
              <div style="font-weight:600;margin-top:4px">${{escapeHtml(paper.title || '')}}</div>
            </div>
            <div class="mono muted">${{formatMaudeNodes(paper.nodes_visited)}}</div>
          </div>
          <div class="muted" style="font-size:0.82rem;margin:10px 0;line-height:1.45">${{escapeHtml(paper.abstract_excerpt || paper.abstract || '')}}</div>
          ${{renderAgreedFields(paper)}}
          <div style="margin-top:8px">
            <div class="field-row-head">
              <div>Field</div>
              <div>LLM</div>
              <div>Maude</div>
              <div>Correct value</div>
              <div>Comment (for Maude learning)</div>
            </div>
            ${{ (paper.fields || []).map(field => `
              <div class="field-row">
                <div class="mono">${{field.field}}</div>
                <div class="field-value-cell llm">${{escapeHtml(formatFieldValue(field.llm_value))}}</div>
                <div class="field-value-cell maude">${{escapeHtml(formatFieldValue(field.maude_value))}}</div>
                <select class="field-resolve-select" id="resolve-select-${{paper.paper_id}}-${{field.field}}">
                  ${{renderFieldSelectOptions(field)}}
                </select>
                <input type="text" class="field-resolve-comment" id="resolve-comment-${{paper.paper_id}}-${{field.field}}"
                  placeholder="Why ${{field.field}} = … (e.g. abstract says 'overview paper')">
              </div>`).join('') }}
          </div>
          <div class="resolve-actions">
            <button type="button" class="resolve-btn primary" onclick="submitDisagreementResolution(${{paper.paper_id}}, '${{paper.batch_id}}')">Resolve commented fields &amp; teach Maude</button>
            <span class="muted" id="resolve-status-${{paper.paper_id}}" style="align-self:center"></span>
          </div>
        </div>`).join('');
    }}

    async function refreshMetrics() {{
      if (!isLiveDashboard()) return;
      try {{
        const response = await fetch(apiUrl('/api/calibration/dashboard-metrics'), {{ credentials: 'same-origin' }});
        if (!response.ok) return;
        const payload = await response.json();
        Object.keys(METRICS).forEach(key => delete METRICS[key]);
        Object.assign(METRICS, payload);
      }} catch (error) {{
        // Keep embedded metrics when API is unreachable.
      }}
    }}

    async function submitDisagreementResolution(paperId, batchId) {{
      const card = document.getElementById(`disagreement-card-${{paperId}}`);
      const statusEl = document.getElementById(`resolve-status-${{paperId}}`);
      if (!isLiveDashboard()) {{
        if (statusEl) {{
          statusEl.textContent = 'Open /calibration/dashboard in the running app (see banner above).';
        }}
        return;
      }}
      const paper = (((METRICS.maude_feedback || {{}}).disagreement_queue) || []).find(row => row.paper_id === paperId);
      const fields = (paper?.fields || []).flatMap(field => {{
        const comment = (document.getElementById(`resolve-comment-${{paperId}}-${{field.field}}`) || {{}}).value?.trim();
        if (!comment) return [];
        const select = document.getElementById(`resolve-select-${{paperId}}-${{field.field}}`);
        if (!select) return [];
        let resolved_value;
        try {{
          resolved_value = deserializeFieldValue(select.value);
        }} catch (error) {{
          return [];
        }}
        return [{{
          field: field.field,
          source: 'expert',
          resolved_value,
          explanation: comment,
        }}];
      }});
      if (!fields.length) {{
        if (statusEl) statusEl.textContent = 'Add a comment on at least one field row to submit.';
        return;
      }}
      if (statusEl) statusEl.textContent = 'Saving...';
      try {{
        const response = await fetch(apiUrl('/api/calibration/resolve-disagreement'), {{
          method: 'POST',
          credentials: 'same-origin',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ paper_id: paperId, batch_id: batchId, fields }}),
        }});
        let payload = {{}};
        try {{
          payload = await response.json();
        }} catch (parseError) {{
          throw new Error(`Server returned ${{response.status}} (not JSON). Are you logged in as admin?`);
        }}
        if (!response.ok) {{
          if (response.status === 401 || response.status === 403) {{
            const loginUrl = payload.login_url || authStatus.login_url || '/login?next=/calibration/dashboard';
            if (statusEl) {{
              statusEl.innerHTML = `${{escapeHtml(payload.error || 'Authentication required')}} — <a href="${{loginUrl}}">Sign in as admin</a>`;
            }}
            await refreshAuthStatus();
            renderAuthBanner();
            return;
          }}
          throw new Error(payload.error || `Request failed (${{response.status}})`);
        }}
        if (card) card.classList.add('resolved');
        if (statusEl) {{
          const reclassified = payload.maude_reclassified || {{}};
          const count = (payload.resolution?.fields || []).length;
          statusEl.innerHTML = `Resolved ${{count}} field(s) · Maude now: <strong>${{escapeHtml(reclassified.publication_type || '—')}}</strong>`;
        }}
        await refreshMetrics();
        renderDashboard();
      }} catch (error) {{
        if (statusEl) {{
          statusEl.textContent = error.message || 'Resolution failed';
          if (String(error.message || '').includes('fetch')) {{
            statusEl.textContent = 'Cannot reach API — open /calibration/dashboard in the running app while logged in as admin.';
          }}
        }}
      }}
    }}

    function renderCalibrationLockBanner() {{
      const el = document.getElementById('calibration-lock-banner');
      if (!el) return;
      const lock = METRICS.calibration_lock || {{}};
      if (!lock.is_blocked) {{
        el.style.display = 'none';
        return;
      }}
      el.style.display = 'block';
      el.innerHTML = `<strong>Calibration lock active:</strong> ${{
        lock.state
      }} · owner ${{
        lock.owner || 'n/a'
      }} · since ${{
        lock.since || 'n/a'
      }} · sub-node ${{
        lock.active_subnode || 'n/a'
      }}`;
    }}

    function rlStatusBadge(status) {{
      if (status === 'passed') return 'badge badge-pass';
      if (status === 'in_progress') return 'badge badge-progress';
      if (status === 'deferred') return 'badge';
      return 'badge badge-progress';
    }}

    let selectedRlNodeId = null;

    function renderRlNodeLearningDetail(nodeId) {{
      const panel = document.getElementById('rl-node-learning-detail');
      if (!panel) return;
      const progress = METRICS.rl_node_progress || {{}};
      const nodes = progress.nodes || {{}};
      const row = nodes[nodeId] || {{}};
      const timeline = (row.learning_timeline || []).filter(event =>
        event.kind === 'handoff' && (event.source_subnode || '') === nodeId
      );
      if (!nodeId || !timeline.length) {{
        panel.style.display = 'none';
        panel.innerHTML = '';
        return;
      }}
      panel.style.display = 'block';
      const label = row.label || nodeId;
      panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px">`
        + `<h3 style="font-size:0.95rem;margin:0">Learning sequence · ${{escapeHtml(label)}}</h3>`
        + `<span class="mono muted" style="font-size:0.75rem">${{nodeId}}</span></div>`
        + `<div class="muted" style="font-size:0.78rem;margin-bottom:14px">Handoffs and batch runs in chronological order. Learning notes capture classifier improvements applied after each feedback cycle.</div>`
        + timeline.map(event => {{
          const when = (event.occurred_at || '').replace('T', ' ').slice(0, 19);
          const kind = event.kind || 'handoff';
          const metrics = kind === 'batch_run'
            ? `<div class="mono muted" style="font-size:0.75rem;margin-top:4px">`
              + (event.alignment_pct != null ? `alignment ${{Number(event.alignment_pct).toFixed(1)}}%` : '')
              + (event.maude_recall_pct != null ? ` · recall ${{Number(event.maude_recall_pct).toFixed(1)}}%` : '')
              + `</div>`
            : '';
          const notes = (event.learning_notes || []).map(note =>
            `<li style="margin-bottom:4px">${{escapeHtml(note)}}</li>`
          ).join('');
          const notesBlock = notes
            ? `<ul style="margin:8px 0 0;padding-left:20px;font-size:0.84rem;line-height:1.45;list-style:disc">${{notes}}</ul>`
            : `<div class="muted" style="font-size:0.78rem;margin-top:6px">No learning notes recorded for this step yet.</div>`;
          return `<div class="rl-learning-event kind-${{kind}}">`
            + `<div style="font-weight:600;font-size:0.88rem">${{escapeHtml(event.title || 'Event')}}</div>`
            + `<div class="mono muted" style="font-size:0.75rem;margin-top:2px">${{kind === 'handoff' ? 'Handoff' : 'Batch run'}} · ${{when || '—'}}</div>`
            + metrics
            + notesBlock
            + `</div>`;
        }}).join('');
    }}

    function selectRlNodeRow(nodeId) {{
      selectedRlNodeId = nodeId;
      document.querySelectorAll('#rl-node-progress-body tr.rl-node-row').forEach(tr => {{
        tr.classList.toggle('selected', tr.dataset.nodeId === nodeId);
      }});
      renderRlNodeLearningDetail(nodeId);
    }}

    function pctOrDash(value) {{
      return value != null ? (Number(value).toFixed(1) + '%') : '—';
    }}

    function renderRlNodeProgress() {{
      const progress = METRICS.rl_node_progress || {{}};
      const nodes = progress.nodes || {{}};
      const ordered = progress.ordered_nodes || Object.keys(nodes);
      const tbody = document.getElementById('rl-node-progress-body');
      const summary = document.getElementById('rl-progress-summary');
      const threshold = progress.threshold_pct != null ? progress.threshold_pct : 90;

      if (summary) {{
        const runCount = (progress.combined_runs || []).length;
        const resetNote = progress.reset_at ? ` · session reset ${{progress.reset_at}}` : '';
        summary.textContent = runCount
          ? `${{runCount}} RL batch run(s) tracked · ${{threshold}}% alignment gate · prerequisites: ${{(progress.prerequisites_passed || []).join(', ') || 'none'}}${{resetNote}}`
          : 'No RL batch runs yet — dashboard reset complete. Execute sub-node batches to populate alignment and Maude recall timelines.' + resetNote;
      }}

      if (tbody) {{
        if (!ordered.length) {{
          tbody.innerHTML = '<tr><td colspan="7" class="muted">No RL node registry configured.</td></tr>';
        }} else {{
          tbody.innerHTML = ordered.map(nodeId => {{
            const row = nodes[nodeId] || {{}};
            const selected = selectedRlNodeId === nodeId ? ' selected' : '';
            const hasTimeline = (row.learning_timeline || []).length ? ' rl-node-row' : '';
            return `<tr class="${{hasTimeline ? 'rl-node-row' + selected : ''}}" data-node-id="${{nodeId}}">
              <td><strong>${{row.label || nodeId}}</strong><div class="mono muted">${{nodeId}}</div></td>
              <td>${{row.phase || '—'}}</td>
              <td><span class="${{rlStatusBadge(row.status)}}">${{row.status || 'pending'}}</span></td>
              <td>${{row.run_count || 0}}</td>
              <td>${{pctOrDash(row.latest_alignment_pct)}}</td>
              <td>${{pctOrDash(row.latest_maude_recall_pct)}}</td>
              <td>${{row.consecutive_pass_batches || 0}} / ${{row.min_consecutive_pass_batches || 2}}</td>
            </tr>`;
          }}).join('');
          tbody.querySelectorAll('tr.rl-node-row').forEach(tr => {{
            tr.addEventListener('click', () => selectRlNodeRow(tr.dataset.nodeId));
          }});
          if (!selectedRlNodeId) {{
            const defaultNode = ordered.find(nodeId => ((nodes[nodeId] || {{}}).learning_timeline || []).length)
              || null;
            if (defaultNode) selectRlNodeRow(defaultNode);
          }} else {{
            selectRlNodeRow(selectedRlNodeId);
          }}
        }}
      }}

      const activeNodes = ordered.filter(nodeId => (nodes[nodeId] || {{}}).phase === 'active');
      const palette = ['#34d399', '#22d3ee', '#a78bfa', '#fbbf24', '#fb7185', '#60a5fa'];
      const alignmentDatasets = activeNodes.map((nodeId, index) => {{
        const row = nodes[nodeId] || {{}};
        const runs = row.runs || [];
        return {{
          label: row.label || nodeId,
          data: runs.map(run => run.alignment_pct),
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length] + '33',
          tension: 0.25,
          spanGaps: true,
        }};
      }}).filter(dataset => dataset.data.length);

      const recallDatasets = activeNodes.map((nodeId, index) => {{
        const row = nodes[nodeId] || {{}};
        const runs = row.runs || [];
        return {{
          label: row.label || nodeId,
          data: runs.map(run => run.maude_recall_pct),
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length] + '33',
          tension: 0.25,
          spanGaps: true,
        }};
      }}).filter(dataset => dataset.data.length);

      const maxRuns = Math.max(0, ...activeNodes.map(nodeId => ((nodes[nodeId] || {{}}).runs || []).length));
      const runLabels = Array.from({{ length: maxRuns }}, (_, idx) => 'Run ' + (idx + 1));

      const chartOptions = {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          y: {{
            beginAtZero: true,
            max: 100,
            ticks: {{ callback: value => value + '%' }},
          }},
        }},
        plugins: {{
          legend: {{ position: 'bottom' }},
        }},
      }};

      const alignmentCanvas = document.getElementById('chart-rl-alignment');
      if (alignmentCanvas) {{
        if (chartRlAlignment) chartRlAlignment.destroy();
        chartRlAlignment = new Chart(alignmentCanvas, {{
          type: 'line',
          data: {{
            labels: runLabels,
            datasets: alignmentDatasets.length ? alignmentDatasets : [{{
              label: 'Awaiting runs',
              data: [],
            }}],
          }},
          options: chartOptions,
        }});
      }}

      const extractionCanvas = document.getElementById('chart-rl-maude-recall');
      if (extractionCanvas) {{
        if (chartRlExtraction) chartRlExtraction.destroy();
        chartRlExtraction = new Chart(extractionCanvas, {{
          type: 'line',
          data: {{
            labels: runLabels,
            datasets: recallDatasets.length ? recallDatasets : [{{
              label: 'Awaiting runs',
              data: [],
            }}],
          }},
          options: chartOptions,
        }});
      }}

      const panel = document.getElementById('subnode-promotion-panel');
      const staged = document.getElementById('staged-patches-panel');
      const promo = METRICS.subnode_promotion || {{}};
      const subnodes = promo.subnodes || {{}};
      const rows = Object.values(subnodes);
      if (panel) {{
        panel.innerHTML = rows.length
          ? '<h3 style="font-size:0.9rem;margin:16px 0 8px">Active Queue Gate Detail</h3>'
            + rows.map(row => {{
              const pct = row.latest_agreement_rate != null ? (row.latest_agreement_rate * 100).toFixed(1) + '%' : 'n/a';
              const badge = row.promotion_ready ? 'badge badge-pass' : 'badge badge-progress';
              return `<div style="margin-bottom:8px"><span class="${{badge}}">${{row.target_subnode}}</span> `
                + `latest alignment ${{pct}} · threshold ${{row.threshold_pct}}% · `
                + `consecutive pass batches ${{row.consecutive_pass_batches}}/${{row.min_consecutive_pass_batches}}</div>`;
            }}).join('')
          : '';
      }}
      if (staged) {{
        const patches = METRICS.staged_patches || [];
        staged.innerHTML = patches.length
          ? `<h3 style="font-size:0.9rem;margin:0 0 8px">Staged Code Proposals</h3>`
            + patches.slice(0, 5).map(row => `<div class="mono muted" style="margin-bottom:6px">${{
              row.target_subnode || 'unknown'
            }} · ${{
              (row.proposed_rules_changes || []).length
            }} proposal(s) · ${{
              row.created_at || ''
            }}${{ row.status === 'applied' ? ' · applied' : '' }}</div>`).join('')
          : '';
      }}

      const handoffLog = document.getElementById('handoff-learning-log-panel');
      if (handoffLog) {{
        const entries = METRICS.handoff_learning_log || [];
        handoffLog.innerHTML = entries.length
          ? `<h3 style="font-size:0.9rem;margin:0 0 8px">Applied Maude Learning Handoffs</h3>`
            + `<div class="muted" style="font-size:0.78rem;margin-bottom:10px">Human-readable summaries of classifier patches applied from RL feedback.</div>`
            + entries.slice(0, 8).map(entry => {{
              const notes = (entry.learning_notes || []).map(note =>
                `<li style="margin-bottom:4px">${{escapeHtml(note)}}</li>`
              ).join('');
              const beneficiaries = (entry.beneficiary_nodes || entry.beneficiary_subnodes || []).join(', ');
              const when = (entry.applied_at || '').replace('T', ' ').slice(0, 19);
              return `<div style="margin-bottom:14px;padding:10px 12px;border:1px solid var(--border);border-radius:8px">`
                + `<div style="font-weight:600;margin-bottom:4px">${{escapeHtml(entry.summary_title || entry.source_subnode || 'Handoff')}}</div>`
                + `<div class="mono muted" style="font-size:0.75rem;margin-bottom:8px">${{escapeHtml(entry.source_subnode || '')}} · ${{when}}`
                + (beneficiaries ? ` · also benefits: ${{escapeHtml(beneficiaries)}}` : '')
                + `</div>`
                + `<ul style="margin:0;padding-left:18px;font-size:0.84rem;line-height:1.45">${{notes}}</ul>`
                + `</div>`;
            }}).join('')
          : '';
      }}

      renderRlTierChart(
        'chart-rl-tier-pdf-alignment',
        'chartRlTierPdf',
        progress.tier_timelines && progress.tier_timelines.pdf_extracted,
        activeNodes,
        nodes,
        'PDF extracted alignment',
      );
      renderRlTierChart(
        'chart-rl-tier-abstract-alignment',
        'chartRlTierAbstract',
        progress.tier_timelines && progress.tier_timelines.abstract_reclassify,
        activeNodes,
        nodes,
        'Abstract reclassify alignment',
      );
    }}

    function renderRlTierChart(canvasId, chartVarName, timeline, activeNodes, nodes, emptyLabel) {{
      const canvas = document.getElementById(canvasId);
      if (!canvas) return;
      const palette = ['#34d399', '#22d3ee', '#a78bfa', '#fbbf24'];
      const datasets = (activeNodes || []).map((nodeId, index) => {{
        const row = nodes[nodeId] || {{}};
        const runs = row.runs || [];
        const data = runs.map(run => {{
          const tierMetrics = (run.tier_metrics || {{}})[
            canvasId.includes('pdf') ? 'pdf_extracted' : 'abstract_reclassify'
          ];
          return tierMetrics ? tierMetrics.alignment_pct : null;
        }});
        return {{
          label: row.label || nodeId,
          data,
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length] + '33',
          tension: 0.25,
          spanGaps: true,
        }};
      }}).filter(dataset => dataset.data.some(value => value != null));

      const maxRuns = Math.max(0, ...(activeNodes || []).map(nodeId => ((nodes[nodeId] || {{}}).runs || []).length));
      const runLabels = Array.from({{ length: maxRuns }}, (_, idx) => 'Run ' + (idx + 1));
      const chartOptions = {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          y: {{
            beginAtZero: true,
            max: 100,
            ticks: {{ callback: value => value + '%' }},
          }},
        }},
        plugins: {{ legend: {{ position: 'bottom' }} }},
      }};

      if (chartVarName === 'chartRlTierPdf' && chartRlTierPdf) chartRlTierPdf.destroy();
      if (chartVarName === 'chartRlTierAbstract' && chartRlTierAbstract) chartRlTierAbstract.destroy();
      const chart = new Chart(canvas, {{
        type: 'line',
        data: {{
          labels: runLabels,
          datasets: datasets.length ? datasets : [{{ label: emptyLabel + ' — awaiting runs', data: [] }}],
        }},
        options: chartOptions,
      }});
      if (chartVarName === 'chartRlTierPdf') chartRlTierPdf = chart;
      if (chartVarName === 'chartRlTierAbstract') chartRlTierAbstract = chart;
    }}

    function renderSubnodePromotion() {{
      renderRlNodeProgress();
    }}

    function renderDashboard() {{
      renderServeModeBanner();
      renderAuthBanner();
      renderCalibrationLockBanner();
      renderSubnodePromotion();
      renderNodeTree();
      renderBreadcrumb();
      renderSummary();
      renderAutomationLayers();
      renderOptimizationTable();
      setupAutomationLayersCollapse();
      renderMaudeComparison();
      renderBatchTable();
      renderNodePapersTable();
      renderReadiness();
      renderBoundaries();
      renderReviewTable();
      renderMaudeFieldExplorer();
      if (document.getElementById('automation-layers-section')?.open) {{
        renderBm25Chart();
      }}
    }}

    refreshAuthStatus()
      .then(() => refreshMetrics())
      .finally(() => renderDashboard());
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def build_dashboard(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rules_path: Path = DEFAULT_RULES_PATH,
    confidence_threshold: float = 0.72,
) -> Tuple[Path, Path]:
    """Builds JSON metrics and standalone HTML dashboard artifacts."""
    rules_config = load_rules_config(rules_path)
    metrics = build_dashboard_metrics(
        output_dir=output_dir,
        rules_config=rules_config,
        confidence_threshold=confidence_threshold,
    )
    data_path = write_dashboard_data(metrics, output_dir / "calibration_dashboard_data.json")
    html_path = write_dashboard_html(metrics, output_dir / "dashboard.html")
    return data_path, html_path


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds CLI parser for calibration dashboard generation."""
    parser = argparse.ArgumentParser(description="Build calibration learning dashboard artifacts.")
    parser.add_argument("--output-dir", default=None, help="Calibration artifacts directory (default: Fly volume or scratch/calibration_runs).")
    parser.add_argument("--rules-path", default=str(DEFAULT_RULES_PATH))
    parser.add_argument("--confidence-threshold", type=float, default=0.72)
    parser.add_argument("--build-dashboard", action="store_true", help="Write JSON + HTML dashboard artifacts.")
    parser.add_argument("--reset-dashboard", action="store_true", help="Archive calibration batches and rebuild an empty RL dashboard.")
    parser.add_argument("--print-json", action="store_true", help="Print aggregated metrics JSON to stdout.")
    return parser


def main() -> None:
    """CLI entry point for dashboard generation."""
    parser = build_arg_parser()
    args = parser.parse_args()
    output_dir = resolve_calibration_output_dir(Path(args.output_dir) if args.output_dir else None)

    if args.reset_dashboard:
        import calibration_reset

        result = calibration_reset.reset_calibration_dashboard(output_dir)
        print(json.dumps(result, indent=2))
        return

    if args.build_dashboard:
        data_path, html_path = build_dashboard(
            output_dir=output_dir,
            rules_path=Path(args.rules_path),
            confidence_threshold=args.confidence_threshold,
        )
        print(f"Dashboard data: {data_path}")
        print(f"Dashboard HTML: {html_path}")
        return

    metrics = build_dashboard_metrics(
        output_dir=output_dir,
        rules_config=load_rules_config(Path(args.rules_path)),
        confidence_threshold=args.confidence_threshold,
    )
    if args.print_json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
