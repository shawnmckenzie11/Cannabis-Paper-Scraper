# calibration_feedback_agent.py
"""Claude meta-feedback on Maude vs LLM calibration disagreements."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import calibration_coordinator
import calibration_metrics
import classifier
import content_tiers
import maude_feedback
import subnode_field_scopes
import classification_schema
from calibration_agent import refresh_maude_batch, resolve_calibration_output_dir
from db_manager import DatabaseManager

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

SKIPPED_FEEDBACK_STATUSES = frozenset({
    "candidate_only",
    "no_extraction",
    "claude_no_extraction",
})


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


def _result_is_scorable(result: Dict[str, Any]) -> bool:
    """Returns True when a batch result has paired Maude/Claude data worth analyzing."""
    if result.get("status") in SKIPPED_FEEDBACK_STATUSES:
        return False
    llm = result.get("llm") or {}
    maude = result.get("maude") or {}
    return bool(llm and maude)


def summarize_batch_gaps(
    batch_payload: Dict[str, Any],
    target_subnode: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarizes field-level disagreement rates and extraction gaps for a batch."""
    target_subnode = target_subnode or batch_payload.get("target_subnode") or batch_payload.get("automation_node")
    field_disagree = Counter()
    field_compared = Counter()
    paper_scores: List[float] = []
    fill_rates: List[float] = []
    papers_with_disagreements = 0
    scored_count = 0

    for result in batch_payload.get("results") or []:
        if not _result_is_scorable(result):
            continue
        scored_count += 1
        if target_subnode:
            metrics = calibration_metrics.score_paper_rl_metrics(result, target_subnode)
            if metrics:
                if metrics.get("alignment_rate") is not None:
                    paper_scores.append(float(metrics["alignment_rate"]))
                if metrics.get("maude_recall_rate") is not None:
                    fill_rates.append(float(metrics["maude_recall_rate"]))

        scoped = result.get("scoped_disagreement") or {}
        disagree_fields = (scoped.get("fields") or {})
        if disagree_fields:
            papers_with_disagreements += 1
        scope_fields = scoped.get("fields_in_scope") or subnode_field_scopes.fields_in_scope(
            target_subnode or "",
            result.get("llm") or {},
        )
        for field in scope_fields:
            field_compared[field] += 1
            if field in disagree_fields:
                field_disagree[field] += 1

    field_stats: List[Dict[str, Any]] = []
    for field, compared in field_compared.most_common():
        disagree = field_disagree.get(field, 0)
        field_stats.append({
            "field": field,
            "compared_count": compared,
            "disagree_count": disagree,
            "disagree_pct": round(disagree / compared * 100, 1) if compared else None,
        })
    field_stats.sort(key=lambda row: (row["disagree_count"], row["disagree_pct"] or 0), reverse=True)

    alignment_rate = (
        round(sum(paper_scores) / len(paper_scores), 4) if paper_scores else None
    )
    maude_recall_rate = (
        round(sum(fill_rates) / len(fill_rates), 4) if fill_rates else None
    )

    return {
        "target_subnode": target_subnode,
        "scored_paper_count": scored_count,
        "papers_with_disagreements": papers_with_disagreements,
        "batch_alignment_rate": alignment_rate,
        "batch_alignment_pct": round(alignment_rate * 100, 1) if alignment_rate is not None else None,
        "batch_maude_recall_rate": maude_recall_rate,
        "batch_maude_recall_pct": round(maude_recall_rate * 100, 1) if maude_recall_rate is not None else None,
        "batch_extraction_fill_rate": maude_recall_rate,
        "batch_extraction_fill_pct": round(maude_recall_rate * 100, 1) if maude_recall_rate is not None else None,
        "field_disagreement_stats": field_stats[:25],
        "top_disagreement_fields": [row["field"] for row in field_stats[:10] if row["disagree_count"]],
    }


def collect_disagreement_rows(
    batch_payload: Dict[str, Any],
    target_subnode: Optional[str] = None,
    db: Optional[DatabaseManager] = None,
) -> List[Dict[str, Any]]:
    """Returns paper-level disagreement rows scoped to branch fields."""
    rows: List[Dict[str, Any]] = []
    subnode = target_subnode or batch_payload.get("target_subnode")
    db = db or DatabaseManager()

    for result in batch_payload.get("results") or []:
        if not _result_is_scorable(result):
            continue
        llm = result.get("llm") or {}
        maude = result.get("maude") or {}

        scoped = result.get("scoped_disagreement")
        if scoped is None and subnode:
            paper_tier = result.get("content_tier") or content_tiers.infer_content_tier({
                **llm,
                "classifier_version": llm.get("classifier_version") or result.get("before_classifier_version"),
            })
            scope_fields = content_tiers.fields_in_scope_for_tier(subnode, paper_tier, llm)
            scoped = subnode_field_scopes.compare_scoped_fields(
                maude,
                llm,
                subnode,
                classification_schema.compare_field_values,
                scope_fields=scope_fields,
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

        abstract = result.get("abstract") or ""
        if not abstract:
            paper = db.get_paper(int(result["paper_id"]))
            abstract = (paper or {}).get("abstract") or ""

        rows.append({
            "paper_id": result.get("paper_id"),
            "title": result.get("title"),
            "abstract": abstract[:800],
            "content_tier": result.get("content_tier"),
            "routing_subnode": result.get("routing_subnode"),
            "node7_path": result.get("node7_path"),
            "fields": unresolved,
            "llm": {field: llm.get(field) for field in unresolved},
            "maude": {field: maude.get(field) for field in unresolved},
            "nodes_visited": maude.get("nodes_visited"),
        })
    return rows


def _node_config_for_subnode(rules_config: Dict[str, Any], target_subnode: str) -> Dict[str, Any]:
    """Returns decision_nodes config for a dashboard sub-node id."""
    mapping = {
        "node2a": "node2a_clinical",
        "node2b": "node2b_in_vivo",
        "node2c": "node2c_in_vitro",
    }
    node_key = mapping.get(target_subnode, target_subnode)
    return (rules_config.get("decision_nodes") or {}).get(node_key) or {}


def _build_feedback_prompt(
    batch_payload: Dict[str, Any],
    disagreement_rows: Sequence[Dict[str, Any]],
    gap_summary: Dict[str, Any],
    rules_config: Dict[str, Any],
    *,
    include_handoff: bool = False,
) -> str:
    """Builds the Claude meta-feedback prompt for a calibration batch."""
    target_subnode = batch_payload.get("target_subnode") or "unknown"
    fields_in_scope = batch_payload.get("fields_in_scope") or subnode_field_scopes.fields_in_scope(
        target_subnode,
    )
    node_cfg = _node_config_for_subnode(rules_config, target_subnode)
    rl_cfg = load_rl_config(rules_config)
    threshold_pct = float(rl_cfg.get("agreement_threshold_pct") or 90)

    examples = disagreement_rows[:8]
    handoff_note = (
        "Do NOT include agent_handoff_prompt in your JSON (it is requested in a follow-up call).\n"
        if not include_handoff
        else "Include a concise agent_handoff_prompt (max 3000 characters) in maude_improvement_brief.\n"
    )
    return (
        "You are the calibration analyst for the Maude rule-based cannabis paper classifier.\n"
        "Claude (llm) PDF/abstract classifications are GROUND TRUTH. Maude must be improved via cues and logic.\n\n"
        f"Active sub-node: {target_subnode}\n"
        f"Agreement gate: {threshold_pct}% field alignment\n"
        f"Batch alignment: {gap_summary.get('batch_alignment_pct')}% "
        f"(Maude recall {gap_summary.get('batch_maude_recall_pct')}%)\n"
        f"Papers with disagreements: {gap_summary.get('papers_with_disagreements')} / "
        f"{gap_summary.get('scored_paper_count')}\n"
        f"Fields in scope: {json.dumps(fields_in_scope)}\n"
        f"Decision node purpose: {node_cfg.get('purpose', 'n/a')}\n\n"
        "Analyze the batch gap summary and per-paper disagreements. For each pattern:\n"
        "1) Decide whether Claude (source=llm) or Maude is correct (prefer Claude unless Maude is clearly right).\n"
        "2) Propose short positive cues (quoted phrases from abstracts) for maude_cues.json / decision_nodes.\n"
        "3) Propose concrete code/logic changes for maude_classifier.py / extractor.py when rules cannot fix the gap.\n"
        f"{handoff_note}\n"
        "Keep field_resolutions to at most 12 highest-impact rows. Prioritize top_disagreement_fields.\n"
        "Keep explanations under 200 characters each. Escape double quotes inside strings.\n"
        "Return ONLY valid JSON (no markdown fences):\n"
        "{\n"
        '  "pattern_summary": "2-4 sentences on dominant error patterns",\n'
        '  "field_resolutions": [\n'
        '    {"paper_id": 1, "field": "exposure_method", "source": "llm", '
        '"resolved_value": ["oral administration"], '
        '"explanation": "Methods says oral gavage."}\n'
        "  ],\n"
        '  "proposed_cues": [\n'
        '    {"node_id": "node2b_in_vivo", "field": "exposure_method", "cue": "oral gavage", '
        '"explanation": "why this cue helps"}\n'
        "  ],\n"
        '  "proposed_rules_changes": [\n'
        '    {"type": "classifier_logic", "description": "...", "patch_hint": "extractor.py function", '
        '"priority": "high|medium|low"}\n'
        "  ],\n"
        '  "maude_improvement_brief": {\n'
        '    "summary": "one paragraph",\n'
        '    "top_gap_patterns": ["pattern 1", "pattern 2"]'
        + (',\n    "agent_handoff_prompt": "..."' if include_handoff else "")
        + "\n  }\n"
        "}\n\n"
        f"Batch gap summary:\n{json.dumps(gap_summary, indent=2, default=str)}\n\n"
        f"Disagreements ({len(disagreement_rows)} papers, showing {len(examples)}):\n"
        f"{json.dumps(examples, indent=2, default=str)}"
    )


def _strip_json_fence(text: str) -> str:
    """Removes markdown code fences from Claude output."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _close_open_json_string(text: str) -> str:
    """Closes an unterminated JSON string at the end of truncated model output."""
    if not text:
        return text
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
    if in_string:
        return text + '"'
    return text


def _salvage_truncated_json(text: str) -> Optional[Dict[str, Any]]:
    """Attempts to recover Maude-learning fields from truncated Claude JSON."""
    text = _strip_json_fence(text)
    if not text:
        return None

    salvaged: Dict[str, Any] = {}
    for key in ("pattern_summary",):
        match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if match:
            salvaged[key] = json.loads(f'"{match.group(1)}"')

    for key in ("field_resolutions", "proposed_cues", "proposed_rules_changes"):
        match = re.search(rf'"{key}"\s*:\s*(\[[\s\S]*?\])(?=\s*,\s*"\w+"|\s*\}})', text)
        if match:
            try:
                salvaged[key] = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    brief_match = re.search(r'"maude_improvement_brief"\s*:\s*(\{[\s\S]*?\})(?=\s*\})', text)
    if brief_match:
        try:
            salvaged["maude_improvement_brief"] = json.loads(brief_match.group(1))
        except json.JSONDecodeError:
            pass

    if salvaged.get("field_resolutions") or salvaged.get("proposed_cues") or salvaged.get("proposed_rules_changes"):
        salvaged["_salvaged_from_truncated_response"] = True
        return salvaged
    return None


def _parse_claude_json(text: str) -> Dict[str, Any]:
    """Extracts JSON object from Claude response text."""
    text = _strip_json_fence(text)
    candidates = [text]
    match = re.search(r"\{[\s\S]*", text)
    if match:
        candidates.insert(0, match.group(0))

    last_error: Optional[Exception] = None
    for candidate in candidates:
        for attempt in (candidate, _close_open_json_string(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError as exc:
                last_error = exc
                trimmed = attempt[: exc.pos].rstrip()
                if trimmed.endswith(","):
                    trimmed = trimmed[:-1]
                for suffix in ("", "]", "}]}", "}", '"}'):
                    try:
                        return json.loads(trimmed + suffix)
                    except json.JSONDecodeError:
                        continue

    salvaged = _salvage_truncated_json(text)
    if salvaged:
        return salvaged
    raise ValueError(f"Claude feedback response did not contain valid JSON: {last_error}")


def synthesize_agent_handoff_prompt(
    feedback: Dict[str, Any],
    target_subnode: str,
    gap_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Builds a handoff brief from proposed_rules_changes when Claude omits or truncates it."""
    brief = feedback.get("maude_improvement_brief") or {}
    existing = (brief.get("agent_handoff_prompt") or "").strip()
    if existing:
        return existing

    lines = [
        f"Implement Maude/extractor fixes for {target_subnode} from calibration feedback.",
        "",
        brief.get("summary") or feedback.get("pattern_summary") or "",
        "",
        "Top gap patterns:",
    ]
    for pattern in brief.get("top_gap_patterns") or []:
        lines.append(f"- {pattern}")
    if gap_summary:
        lines.append("")
        lines.append(
            f"Batch alignment: {gap_summary.get('batch_alignment_pct')}% · "
            f"top fields: {', '.join(gap_summary.get('top_disagreement_fields') or [])}"
        )
    lines.append("")
    lines.append("Proposed classifier changes:")
    for idx, change in enumerate(feedback.get("proposed_rules_changes") or [], start=1):
        lines.append(
            f"{idx}. [{change.get('priority', 'medium')}] {change.get('description', '')} "
            f"(hint: {change.get('patch_hint', 'extractor.py')})"
        )
    lines.append("")
    lines.append("Also apply proposed_cues to maude_cues.json for adjacent node2 branches where relevant.")
    return "\n".join(line for line in lines if line is not None).strip()


def _request_agent_handoff_brief(
    feedback: Dict[str, Any],
    batch_payload: Dict[str, Any],
    gap_summary: Dict[str, Any],
    rules_config: Dict[str, Any],
    client: Any,
    model: str,
) -> str:
    """Second Claude call for a compact coding-agent handoff (avoids truncating learning JSON)."""
    target_subnode = batch_payload.get("target_subnode") or "unknown"
    rules = feedback.get("proposed_rules_changes") or []
    if not rules:
        return synthesize_agent_handoff_prompt(feedback, target_subnode, gap_summary)

    prompt = (
        f"You are writing a coding-agent handoff for the Cannabis Paper Scraper repo.\n"
        f"Sub-node: {target_subnode}\n"
        f"Alignment: {gap_summary.get('batch_alignment_pct')}%\n\n"
        "Turn these proposed_rules_changes into a concise implementation brief (max 2500 words) "
        "covering extractor.py, maude_classifier.py, and maude_cues.json. Include test ideas.\n"
        "Return plain text only (no JSON, no markdown fences).\n\n"
        f"{json.dumps(rules, indent=2, default=str)}\n\n"
        f"Gap patterns: {json.dumps((feedback.get('maude_improvement_brief') or {}).get('top_gap_patterns') or [], indent=2)}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    blocks = [
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    text = "\n".join(blocks).strip()
    return text or synthesize_agent_handoff_prompt(feedback, target_subnode, gap_summary)


def _estimate_feedback_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns a rough USD cost estimate for a feedback API call."""
    if "haiku" in model.lower():
        return round(input_tokens * 0.00000025 + output_tokens * 0.00000125, 6)
    return round(input_tokens * 0.000003 + output_tokens * 0.000015, 6)


def request_claude_feedback(
    batch_payload: Dict[str, Any],
    disagreement_rows: Sequence[Dict[str, Any]],
    gap_summary: Dict[str, Any],
    rules_config: Optional[Dict[str, Any]] = None,
    db: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """Calls Claude to produce structured feedback for batch disagreements."""
    rules_config = rules_config or classifier.load_rules_config()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or Anthropic is None:
        raise RuntimeError("ANTHROPIC_API_KEY and anthropic package are required for Claude feedback.")

    prompt = _build_feedback_prompt(
        batch_payload, disagreement_rows, gap_summary, rules_config, include_handoff=False,
    )
    client = Anthropic(api_key=api_key)
    model = os.getenv("ANTHROPIC_CALIBRATION_MODEL", "claude-sonnet-4-6")
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    feedback = _parse_claude_json("\n".join(text_blocks))

    handoff_prompt = _request_agent_handoff_brief(
        feedback,
        batch_payload,
        gap_summary,
        rules_config,
        client,
        model,
    )
    feedback.setdefault("maude_improvement_brief", {})
    if isinstance(feedback["maude_improvement_brief"], dict):
        feedback["maude_improvement_brief"]["agent_handoff_prompt"] = handoff_prompt
    feedback["agent_handoff_prompt"] = handoff_prompt

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    metrics = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": _estimate_feedback_cost(model, input_tokens, output_tokens),
        "prompt_chars": len(prompt),
        "disagreement_rows_sent": len(disagreement_rows),
    }
    feedback["_llm_call_metrics"] = metrics

    if db is not None:
        try:
            db.log_llm_call(
                paper_id=None,
                metrics={
                    **metrics,
                    "classifier_version": f"calibration-feedback-{rules_config.get('version', 'unknown')}",
                },
                batch_id=batch_payload.get("batch_id"),
            )
        except Exception:
            pass

    return feedback


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
        if not item.get("field"):
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


def apply_proposed_cues(
    feedback: Dict[str, Any],
    disagreement_rows: Sequence[Dict[str, Any]],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Auto-applies proposed_cues from Claude feedback into the runtime cue overlay."""
    output_dir = output_dir or resolve_calibration_output_dir()
    example_paper_id = int(disagreement_rows[0]["paper_id"]) if disagreement_rows else 0
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for item in feedback.get("proposed_cues") or []:
        node_id = (item.get("node_id") or "").strip()
        cue = (item.get("cue") or "").strip()
        field = (item.get("field") or "study_type").strip()
        explanation = (item.get("explanation") or f"Claude proposed cue: {cue}").strip()
        if not node_id or not cue:
            skipped.append({"item": item, "reason": "missing node_id or cue"})
            continue
        store = maude_feedback.apply_cue_update(
            node_id,
            cue,
            field,
            example_paper_id,
            explanation,
            output_dir=output_dir,
        )
        applied.append({
            "node_id": node_id,
            "field": field,
            "cue": cue,
            "store_path": str(maude_feedback.resolve_learned_cues_path(output_dir)),
        })

    return {
        "applied_cue_count": len(applied),
        "skipped_cue_count": len(skipped),
        "applied_cues": applied,
        "skipped_cues": skipped,
    }


def save_staged_patches(
    feedback: Dict[str, Any],
    target_subnode: str,
    output_dir: Path,
    gap_summary: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Persists proposed code/rules changes and agent handoff brief for human review."""
    proposals = feedback.get("proposed_rules_changes") or []
    brief = feedback.get("maude_improvement_brief") or {}
    proposed_cues = feedback.get("proposed_cues") or []
    if not proposals and not brief and not proposed_cues:
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
            "gap_summary": gap_summary,
            "proposed_rules_changes": proposals,
            "proposed_cues": proposed_cues,
            "maude_improvement_brief": brief,
            "agent_handoff_prompt": brief.get("agent_handoff_prompt"),
            "llm_call_metrics": feedback.get("_llm_call_metrics"),
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
    db = db or DatabaseManager()
    with open(batch_path, encoding="utf-8") as handle:
        batch_payload = json.load(handle)

    target_subnode = batch_payload.get("target_subnode") or batch_payload.get("automation_node")
    batch_id = batch_payload.get("batch_id") or batch_path.stem
    gap_summary = summarize_batch_gaps(batch_payload, target_subnode)
    disagreement_rows = collect_disagreement_rows(batch_payload, target_subnode, db=db)

    rl_cfg = load_rl_config()
    min_papers = int(rl_cfg.get("min_papers_per_feedback_cycle") or 30)
    threshold = float(rl_cfg.get("agreement_threshold_pct") or 90) / 100.0
    alignment = gap_summary.get("batch_alignment_rate")
    scored = gap_summary.get("scored_paper_count", 0)

    if not disagreement_rows and scored < min_papers:
        return {
            "status": "skipped",
            "reason": f"Fewer than {min_papers} scored papers and no disagreements.",
            "batch_id": batch_id,
            "gap_summary": gap_summary,
        }
    if not disagreement_rows and alignment is not None and alignment >= threshold:
        return {
            "status": "skipped",
            "reason": f"Alignment {gap_summary.get('batch_alignment_pct')}% meets gate; no disagreements to analyze.",
            "batch_id": batch_id,
            "gap_summary": gap_summary,
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
        try:
            feedback = request_claude_feedback(
                batch_payload,
                disagreement_rows,
                gap_summary,
                db=db,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "batch_id": batch_id,
                "gap_summary": gap_summary,
                "disagreement_count": len(disagreement_rows),
                "note": "Maude learning (cues/resolutions) not applied — Claude response could not be parsed.",
            }

        apply_result = apply_feedback_resolutions(batch_payload, feedback, output_dir=output_dir, db=db)
        cue_result = apply_proposed_cues(feedback, disagreement_rows, output_dir=output_dir)
        refresh_path, _ = refresh_maude_batch(batch_path, output_dir=output_dir)
        staged_path = save_staged_patches(
            feedback,
            target_subnode or "unknown",
            output_dir,
            gap_summary=gap_summary,
        )

        report_path = output_dir / f"{batch_id}_feedback_report.json"
        report = {
            "batch_id": batch_id,
            "target_subnode": target_subnode,
            "created_at": datetime.now().isoformat(),
            "disagreement_count": len(disagreement_rows),
            "gap_summary": gap_summary,
            "pattern_summary": feedback.get("pattern_summary"),
            "llm_call_metrics": feedback.get("_llm_call_metrics"),
            "apply_result": {
                "applied_count": apply_result.get("applied_count"),
                "error_count": apply_result.get("error_count"),
            },
            "cue_apply_result": cue_result,
            "refresh_batch_path": str(refresh_path),
            "staged_patch_path": str(staged_path) if staged_path else None,
            "agent_handoff_prompt": (feedback.get("maude_improvement_brief") or {}).get("agent_handoff_prompt"),
            "salvaged_response": bool(feedback.get("_salvaged_from_truncated_response")),
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
            "gap_summary": gap_summary,
            "llm_call_metrics": feedback.get("_llm_call_metrics"),
            "agent_handoff_prompt": report.get("agent_handoff_prompt"),
            "salvaged_response": bool(feedback.get("_salvaged_from_truncated_response")),
            **apply_result,
            **cue_result,
        }
    finally:
        if not skip_lock:
            calibration_coordinator.release_lock(db=db)
