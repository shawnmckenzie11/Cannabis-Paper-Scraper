# rule_optimizer.py
"""Field-group scoring, reward computation, and optimization logging for the RL loop."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import classifier

DEFAULT_FIELD_GROUPS = {
    "relevance": ["publication_type"],
    "extraction": [
        "study_type",
        "exposure_method",
        "cannabis_type",
        "outcome_domain",
        "thc_pct",
        "cbd_pct",
        "dose_mg",
        "strain_reported",
        "strain_normalized",
        "duration_days",
        "inhaled_exposure_duration",
        "administration_frequency",
        "treatment_duration",
        "sample_size",
        "puff_count",
        "thc_mg_ml",
        "thc_mg_g",
        "thc_mg_kg",
        "cbd_mg_ml",
        "cbd_mg_g",
        "cbd_mg_kg",
        "thc_uM",
        "cbd_uM",
    ],
}

DEFAULT_REWARD_WEIGHTS = {
    "lambda_cost": 0.1,
    "lambda_fallback": 0.5,
    "lambda_regression": 2.0,
    "zero_regression_gate": True,
    "failed_attempts_before_human_review": 3,
}


def load_field_groups(config: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Returns configured field groups for relevance vs extraction scoring."""
    config = config or classifier.load_rules_config()
    groups = config.get("field_groups") or DEFAULT_FIELD_GROUPS
    return {
        "relevance": list(groups.get("relevance") or DEFAULT_FIELD_GROUPS["relevance"]),
        "extraction": list(groups.get("extraction") or DEFAULT_FIELD_GROUPS["extraction"]),
    }


def load_reward_weights(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns reward-function weights used for optimization logging and future tuning."""
    config = config or classifier.load_rules_config()
    weights = dict(DEFAULT_REWARD_WEIGHTS)
    weights.update(config.get("reward_function") or {})
    return weights


def values_match(left: Any, right: Any) -> bool:
    """Returns True when two classification values are equivalent for Hamming loss."""
    return classifier.jaccard_similarity(left, right) >= 1.0


def field_group_hamming_loss(
    predictions: Dict[str, Any],
    ground_truth: Dict[str, Any],
    fields: Sequence[str],
) -> float:
    """Computes average Hamming loss across a field group within one classification pass."""
    if not fields:
        return 0.0
    mismatches = 0
    for field in fields:
        if not values_match(predictions.get(field), ground_truth.get(field)):
            mismatches += 1
    return mismatches / len(fields)


def score_field_groups(
    predictions: Dict[str, Any],
    ground_truth: Dict[str, Any],
    field_groups: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, float]:
    """Scores relevance and extraction Hamming loss for a single paper comparison."""
    groups = field_groups or load_field_groups()
    return {
        group: field_group_hamming_loss(predictions, ground_truth, fields)
        for group, fields in groups.items()
    }


def aggregate_field_group_scores(
    comparisons: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]],
    field_groups: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, float]:
    """Averages field-group Hamming loss across a holdout set."""
    groups = field_groups or load_field_groups()
    totals = {group: 0.0 for group in groups}
    if not comparisons:
        return totals
    for prediction, ground_truth in comparisons:
        paper_scores = score_field_groups(prediction, ground_truth, groups)
        for group, value in paper_scores.items():
            totals[group] += value
    return {group: totals[group] / len(comparisons) for group in groups}


def compute_reward(
    baseline_scores: Dict[str, float],
    candidate_scores: Dict[str, float],
    reward_weights: Optional[Dict[str, Any]] = None,
    cost_delta: float = 0.0,
    fallback_rate_delta: float = 0.0,
) -> float:
    """Computes a scalar reward for an optimization candidate using configured weights."""
    weights = reward_weights or load_reward_weights()
    relevance_gain = baseline_scores.get("relevance", 0.0) - candidate_scores.get("relevance", 0.0)
    extraction_gain = baseline_scores.get("extraction", 0.0) - candidate_scores.get("extraction", 0.0)
    improvement = relevance_gain + extraction_gain

    relevance_regression = max(0.0, candidate_scores.get("relevance", 0.0) - baseline_scores.get("relevance", 0.0))
    extraction_regression = max(0.0, candidate_scores.get("extraction", 0.0) - baseline_scores.get("extraction", 0.0))
    regression_penalty = relevance_regression + extraction_regression

    return (
        improvement
        - float(weights.get("lambda_regression", 2.0)) * regression_penalty
        - float(weights.get("lambda_cost", 0.1)) * cost_delta
        - float(weights.get("lambda_fallback", 0.5)) * fallback_rate_delta
    )


def zero_regression_gate_passed(
    baseline_scores: Dict[str, float],
    candidate_scores: Dict[str, float],
) -> bool:
    """Returns True when a candidate does not regress on either field group."""
    return (
        candidate_scores.get("relevance", 0.0) <= baseline_scores.get("relevance", 0.0)
        and candidate_scores.get("extraction", 0.0) <= baseline_scores.get("extraction", 0.0)
    )


def evaluate_optimization_candidate(
    baseline_scores: Dict[str, float],
    candidate_scores: Dict[str, float],
    reward_weights: Optional[Dict[str, Any]] = None,
    cost_delta: float = 0.0,
    fallback_rate_delta: float = 0.0,
) -> Dict[str, Any]:
    """Evaluates one optimization candidate and returns gate, reward, and field-group breakdown."""
    weights = reward_weights or load_reward_weights()
    gate_passed = zero_regression_gate_passed(baseline_scores, candidate_scores)
    if weights.get("zero_regression_gate", True) and not gate_passed:
        accepted = False
    else:
        accepted = gate_passed

    reward = compute_reward(
        baseline_scores,
        candidate_scores,
        reward_weights=weights,
        cost_delta=cost_delta,
        fallback_rate_delta=fallback_rate_delta,
    )
    return {
        "baseline_scores": baseline_scores,
        "candidate_scores": candidate_scores,
        "reward": round(reward, 6),
        "gate_passed": gate_passed,
        "accepted": accepted,
    }


def record_optimization_result(
    db,
    evaluation: Dict[str, Any],
    patch_summary: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    rules_version_before: Optional[str] = None,
    rules_version_after: Optional[str] = None,
) -> Dict[str, Any]:
    """Persists an optimization attempt and updates failed-attempt escalation state."""
    weights = load_reward_weights()
    run_id = run_id or f"opt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    accepted = bool(evaluation.get("accepted"))
    gate_passed = bool(evaluation.get("gate_passed"))

    if accepted:
        failed_attempts = 0
        status = "accepted"
    else:
        failed_attempts = db.increment_metadata("optimization_failed_attempts", 1)
        threshold = int(weights.get("failed_attempts_before_human_review", 3))
        status = "needs_human_review" if failed_attempts >= threshold else "rejected"

    field_group_scores = {
        "relevance": {
            "baseline_hamming": evaluation["baseline_scores"].get("relevance", 0.0),
            "candidate_hamming": evaluation["candidate_scores"].get("relevance", 0.0),
        },
        "extraction": {
            "baseline_hamming": evaluation["baseline_scores"].get("extraction", 0.0),
            "candidate_hamming": evaluation["candidate_scores"].get("extraction", 0.0),
        },
    }

    log_row = db.insert_optimization_log(
        run_id=run_id,
        field_group_scores=field_group_scores,
        reward=float(evaluation.get("reward", 0.0)),
        gate_passed=gate_passed,
        failed_attempts=failed_attempts,
        status=status,
        patch_summary=patch_summary or {},
        rules_version_before=rules_version_before,
        rules_version_after=rules_version_after,
    )

    if accepted:
        db.set_metadata("optimization_failed_attempts", "0")

    return {
        "run_id": run_id,
        "status": status,
        "failed_attempts": failed_attempts,
        "gate_passed": gate_passed,
        "accepted": accepted,
        "log_id": log_row.get("id"),
    }
