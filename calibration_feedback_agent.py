# calibration_feedback_agent.py
"""Claude meta-feedback on Maude vs LLM calibration disagreements."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import calibration_coordinator
import classifier
import maude_feedback
import subnode_field_scopes
import classification_schema
from calibration_agent import refresh_maude_batch, resolve_calibration_output_dir

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None


def load_rl_config(rules_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns calibration_rl settings from rules_config."""
    rules_config = rules_config or classifier.load_rules_config()
    defaults = {
        "agreement_threshold_pct": 90,
        "min_papers_per_feedback_cycle": 30,
        "min_consecutive_pass_batches": 2,
        "subnode_queue": list(calibration_coordinator.DEFAULT_SUBNODE_QUEUE),
        "subnode_reevaluate_later": list(calibration_coordinator.DEFAULT_REEVALUATE_LATER),
        "field_scope_source": "Cannabis_Classification_Decision_Tree",
    }
    cfg = dict(defaults)
    cfg.update((rules_config.get("agent_automation") or {}).get("calibration_rl") or {})
    return cfg


def collect_disagreement_rows(
    batch_payload: Dict[str, Any],
    target_subnode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Returns paper-level disagreement rows scoped to branch fields."""
    rows: List[Dict[str, Any]] = []
    subnode = target_subnode or batch_payload.get("target_subnode")
    for result in batch_payload.get("results") or []:
        if result.get("dry_run") or result.get("status") in {"candidate_only", "no_extraction"}:
            continue
        llm = result.get("llm") or {}
        maude = result.get("maude") or {}
        if not llm or not maude:
            continue
        scoped = result.get("scoped_disagreement")
        if scoped is None and subnode:
            scoped = subnode_field_scopes.compare_scoped_fields(
                maude,
                llm,
                subnode,
                classification_schema.compare_field_values,
            )
        disagreement_fields = (scoped or result.get("disagreement") or {}).get("fields") or {}
        if not disagreement_fields:
            continue
        resolved = set(result.get("expert_resolved_fields") or [])
        unresolved = {
            field: payload
            for field, payload in disagreement_fields.items()
            if field not in resolved
        }
        if not unresolved:
            continue
        rows.append({
            "paper_id": result.get("paper_id"),
            "title": result.get("title"),
            "abstract": (result.get("abstract") or "")[:1200],
            "routing_subnode": result.get("routing_subnode"),
            "node7_path": result.get("node7_path"),
            "fields": unresolved,
            "llm": {field: llm.get(field) for field in unresolved},
            "maude": {field: maude.get(field) for field in unresolved},
            "nodes_visited": maude.get("nodes_visited"),
        })
    return rows


def _build_feedback_prompt(
    batch_payload: Dict[str, Any],
    disagreement_rows: Sequence[Dict[str, Any]],
    rules_config: Dict[str, Any],
) -> str:
    """Builds the Claude meta-feedback prompt for a calibration batch."""
    target_subnode = batch_payload.get("target_subnode") or "unknown"
    fields_in_scope = batch_payload.get("fields_in_scope") or subnode_field_scopes.fields_in_scope(
        target_subnode,
    )
    node_cfg = (rules_config.get("decision_nodes") or {}).get(
        f"{target_subnode.replace('node2a', 'node2a_clinical').replace('node2b', 'node2b_in_vivo').replace('node2c', 'node2c_in_vitro')}"
    )
    if not node_cfg:
        mapping = {
            "node2a": "node2a_clinical",
            "node2b": "node2b_in_vivo",
            "node2c": "node2c_in_vitro",
        }
        node_cfg = (rules_config.get("decision_nodes") or {}).get(mapping.get(target_subnode, ""), {})

    examples = disagreement_rows[:20]
    return (
        "You are calibrating the Maude rule-based cannabis paper classifier against Claude ground truth.\n"
        f"Active sub-node: {target_subnode}\n"
        f"Fields in agreement scope: {json.dumps(fields_in_scope)}\n"
        f"Decision node purpose: {(node_cfg or {}).get('purpose', 'n/a')}\n\n"
        "For each disagreement below, decide whether Claude (llm) or Maude is correct. "
        "Prefer Claude (source=llm) unless Maude is clearly right.\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "pattern_summary": "string",\n'
        '  "field_resolutions": [\n'
        '    {"paper_id": 1, "field": "study_type", "source": "llm", "resolved_value": "...", '
        '"explanation": "short cue-friendly explanation with a quoted phrase when possible"}\n'
        "  ],\n"
        '  "proposed_cues": [{"node_id": "node2b_in_vivo", "cue": "oral gavage", "field": "exposure_method"}],\n'
        '  "proposed_rules_changes": [{"type": "classifier_logic", "description": "...", "patch_hint": "..."}]\n'
        "}\n\n"
        f"Disagreements ({len(disagreement_rows)} total, showing {len(examples)}):\n"
        f"{json.dumps(examples, indent=2, default=str)}"
    )


def _parse_claude_json(text: str) -> Dict[str, Any]:
    """Extracts JSON object from Claude response text."""
    text = (text or "").strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("Claude feedback response did not contain JSON.")
    return json.loads(match.group(0))


def request_claude_feedback(
    batch_payload: Dict[str, Any],
    disagreement_rows: Sequence[Dict[str, Any]],
    rules_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calls Claude to produce structured feedback for batch disagreements."""
    rules_config = rules_config or classifier.load_rules_config()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or Anthropic is None:
        raise RuntimeError("ANTHROPIC_API_KEY and anthropic package are required for Claude feedback.")

    prompt = _build_feedback_prompt(batch_payload, disagreement_rows, rules_config)
    client = Anthropic(api_key=api_key)
    model = os.getenv("ANTHROPIC_CALIBRATION_MODEL", "claude-sonnet-4-20250514")
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return _parse_claude_json("\n".join(text_blocks))


def apply_feedback_resolutions(
    batch_payload: Dict[str, Any],
    feedback: Dict[str, Any],
    output_dir: Optional[Path] = None,
    db=None,
) -> Dict[str, Any]:
    """Applies Claude field resolutions via maude_feedback with auto-audit settings."""
    output_dir = output_dir or resolve_calibration_output_dir()
    batch_id = batch_payload.get("batch_id")
    if not batch_id:
        raise ValueError("batch_payload is missing batch_id")

    resolutions_by_paper: Dict[int, List[Dict[str, Any]]] = {}
    for item in feedback.get("field_resolutions") or []:
        try:
            paper_id = int(item.get("paper_id"))
        except (TypeError, ValueError):
            continue
        resolutions_by_paper.setdefault(paper_id, []).append({
            **item,
            "resolution_source": "claude_auto",
        })

    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for paper_id, field_rows in resolutions_by_paper.items():
        try:
            result = maude_feedback.resolve_disagreement(
                paper_id=paper_id,
                batch_id=batch_id,
                field_resolutions=field_rows,
                output_dir=output_dir,
                db=db,
                skip_feedback_eval_counter=True,
                resolution_source="claude_auto",
            )
            applied.append(result)
        except Exception as exc:
            errors.append({"paper_id": paper_id, "error": str(exc)})

    return {
        "applied_count": len(applied),
        "error_count": len(errors),
        "applied": applied,
        "errors": errors,
    }


def save_staged_patches(
    feedback: Dict[str, Any],
    target_subnode: str,
    output_dir: Path,
) -> Optional[Path]:
    """Persists proposed code/rules changes for human review."""
    proposals = feedback.get("proposed_rules_changes") or []
    if not proposals:
        return None
    staged_dir = output_dir / "staged_patches"
    staged_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = staged_dir / f"{target_subnode}_{timestamp}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "target_subnode": target_subnode,
            "created_at": datetime.now().isoformat(),
            "pattern_summary": feedback.get("pattern_summary"),
            "proposed_rules_changes": proposals,
            "proposed_cues": feedback.get("proposed_cues") or [],
        }, handle, indent=2)
    return path


def run_feedback_cycle(
    batch_path: Path,
    output_dir: Optional[Path] = None,
    db=None,
    skip_lock: bool = False,
) -> Dict[str, Any]:
    """Runs a full Claude feedback cycle: analyze disagreements, apply cues, refresh Maude."""
    output_dir = output_dir or resolve_calibration_output_dir(str(batch_path.parent))
    with open(batch_path, encoding="utf-8") as handle:
        batch_payload = json.load(handle)

    target_subnode = batch_payload.get("target_subnode") or batch_payload.get("automation_node")
    batch_id = batch_payload.get("batch_id") or batch_path.stem
    disagreement_rows = collect_disagreement_rows(batch_payload, target_subnode)

    rl_cfg = load_rl_config()
    min_papers = int(rl_cfg.get("min_papers_per_feedback_cycle") or 30)
    scored = [
        row for row in (batch_payload.get("results") or [])
        if row.get("scoped_disagreement") or row.get("disagreement")
    ]
    if len(scored) < min_papers and len(disagreement_rows) == 0:
        return {
            "status": "skipped",
            "reason": f"Fewer than {min_papers} scored papers and no disagreements.",
            "batch_id": batch_id,
        }

    lock_owner = f"feedback-{batch_id}"
    if not skip_lock:
        calibration_coordinator.acquire_lock(
            "applying_feedback",
            lock_owner,
            subnode=target_subnode,
            db=db,
        )

    try:
        feedback = request_claude_feedback(batch_payload, disagreement_rows)
        apply_result = apply_feedback_resolutions(batch_payload, feedback, output_dir=output_dir, db=db)
        refresh_path, _ = refresh_maude_batch(batch_path, output_dir=output_dir)
        staged_path = save_staged_patches(feedback, target_subnode or "unknown", output_dir)

        report_path = output_dir / f"{batch_id}_feedback_report.json"
        report = {
            "batch_id": batch_id,
            "target_subnode": target_subnode,
            "created_at": datetime.now().isoformat(),
            "disagreement_count": len(disagreement_rows),
            "pattern_summary": feedback.get("pattern_summary"),
            "apply_result": {
                "applied_count": apply_result.get("applied_count"),
                "error_count": apply_result.get("error_count"),
            },
            "refresh_batch_path": str(refresh_path),
            "staged_patch_path": str(staged_path) if staged_path else None,
            "feedback": feedback,
        }
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)

        return {
            "status": "completed",
            "batch_id": batch_id,
            "report_path": str(report_path),
            "refresh_batch_path": str(refresh_path),
            "staged_patch_path": str(staged_path) if staged_path else None,
            **apply_result,
        }
    finally:
        if not skip_lock:
            calibration_coordinator.release_lock(db=db)
