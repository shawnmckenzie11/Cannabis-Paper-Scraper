"""Pre-harvest manual edit cycle: expert drawer corrections → RL patch + cue overlay."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import calibration_agent
import calibration_feedback_agent as cfa
import classification_schema
import content_tiers
import handoff_learning_log
import maude_classifier
import maude_confidence
import maude_feedback
import patch_blast_radius
import rules_version
import subnode_field_scopes
from calibration_agent import (
    MAUDE_AB_COMPARE_FIELDS,
    get_rules_version,
    maude_output_to_compare_block,
    paper_row_to_llm_block,
)
from calibration_pdf import resolve_classification_full_text
from db_manager import DatabaseManager

logger = logging.getLogger("manual_edit_cycle")

METADATA_LAST_CYCLE = "last_manual_edit_cycle_at"
METADATA_LAST_REPORT = "last_manual_edit_cycle_report"
METADATA_LAST_HARVEST = "last_daily_harvest_timestamp"
DEFAULT_OUTPUT_DIR = Path("scratch/manual_edit_runs")
CONFIDENCE_DELTA_PER_EDIT = 2.0


def utc_now_iso() -> str:
    """Returns current UTC timestamp as ISO string."""
    return datetime.utcnow().isoformat() + "Z"


def resolve_since_timestamp(db: DatabaseManager, since: Optional[str]) -> str:
    """Resolves a since argument to an ISO timestamp watermark."""
    if since in ("last-harvest", "harvest", "daily-harvest"):
        return db.get_metadata(METADATA_LAST_HARVEST) or "1970-01-01T00:00:00"
    if since in (None, "", "last-cycle", "last"):
        return db.get_metadata(METADATA_LAST_CYCLE) or "1970-01-01T00:00:00"
    return str(since)


def dedupe_expert_edits(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keeps the latest audit row per (paper_id, field_name)."""
    latest: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for row in rows:
        paper_id = int(row["paper_id"])
        field_name = str(row["field_name"])
        key = (paper_id, field_name)
        existing = latest.get(key)
        if existing is None or str(row.get("timestamp") or "") >= str(existing.get("timestamp") or ""):
            latest[key] = dict(row)
    return sorted(latest.values(), key=lambda item: (str(item.get("timestamp")), int(item["id"])))


def fetch_expert_edits_since(
    db: DatabaseManager,
    since_ts: str,
    *,
    paper_ids: Optional[Sequence[int]] = None,
) -> List[Dict[str, Any]]:
    """Returns deduplicated expert drawer edits since a timestamp."""
    rows = db.fetch_feedback_audit_since(
        since_ts,
        expert_drawer_only=True,
        paper_ids=paper_ids,
    )
    return dedupe_expert_edits(rows)


def _parse_audit_value(raw: Any) -> Any:
    """Parses a feedback_audit old/new value into a Python object."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, dict, int, float, bool)):
        return raw
    text = str(raw).strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            return text
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def infer_routing_subnode_for_paper(paper: Dict[str, Any]) -> str:
    """Infers routing sub-node from expert (current) paper classification fields."""
    block = paper_row_to_llm_block(paper, paper.get("title") or "", paper.get("abstract") or "")
    return classification_schema.infer_routing_subnode("node1_routing", block)


def build_miss_reason(
    field_name: str,
    old_value: Any,
    new_value: Any,
    classifier_version: Optional[str],
) -> str:
    """Builds a short explanation of why the classifier missed the expert correction."""
    version = str(classifier_version or "unknown")
    if version.startswith("maude"):
        source = "Maude"
    elif version.startswith("llm"):
        source = "LLM"
    elif version.startswith("heuristic"):
        source = "Heuristic"
    else:
        source = "Classifier"
    return (
        f"{source} ({version}) had {field_name}={old_value!r}; "
        f"expert corrected to {new_value!r}"
    )


def reconstruct_pre_edit_paper(paper: Dict[str, Any], audit_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Reconstructs paper classification state before expert edits using audit old_values."""
    restored = dict(paper)
    for row in audit_rows:
        field = str(row.get("field_name") or "")
        if not field:
            continue
        restored[field] = _parse_audit_value(row.get("old_value"))
    return restored


def run_maude_compare_block(
    paper: Dict[str, Any],
    db: DatabaseManager,
    pdf_cache: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    """Re-runs Maude on a paper and returns a normalized compare block."""
    context = calibration_agent.fetch_paper_calibration_context(db, int(paper["id"]))
    title = context.get("title") or paper.get("title") or ""
    abstract = context.get("abstract") or paper.get("abstract") or ""
    cache = pdf_cache if pdf_cache is not None else {}
    full_text, _source = resolve_classification_full_text(
        full_text_link=context.get("full_text_link") or paper.get("full_text_link"),
        pmid=context.get("pmid") or paper.get("pmid"),
        doi=context.get("doi") or paper.get("doi"),
        cache=cache,
    )
    rules_version_label = get_rules_version()
    maude_out = maude_classifier.classify_paper(
        title,
        abstract,
        full_text=full_text,
        rules_version=rules_version_label,
    )
    return maude_output_to_compare_block(maude_out, rules_version_label)


def build_disagreement_fields(
    expert_block: Dict[str, Any],
    maude_block: Dict[str, Any],
    edited_fields: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Builds disagreement payload for fields the expert edited where Maude still differs."""
    disagreements: Dict[str, Dict[str, Any]] = {}
    for field in edited_fields:
        if field not in MAUDE_AB_COMPARE_FIELDS and field not in {"study_type", "thc_pct", "cbd_pct"}:
            continue
        expert_val = expert_block.get(field)
        maude_val = maude_block.get(field)
        if not classification_schema.compare_field_values(expert_val, maude_val):
            disagreements[field] = {
                "llm": expert_val,
                "maude": maude_val,
                "expert": expert_val,
            }
    return disagreements


def pull_affected_papers(sqlite_path: str, paper_ids: Sequence[int]) -> Dict[str, Any]:
    """Pulls specific paper ids from Postgres into local SQLite when DATABASE_URL is set."""
    if not os.getenv("DATABASE_URL"):
        return {"pulled": 0, "skipped": True, "reason": "DATABASE_URL not set"}
    if not paper_ids:
        return {"pulled": 0, "skipped": True, "reason": "no paper ids"}

    import subprocess
    import sys

    root = Path(__file__).resolve().parent
    cmd = [
        sys.executable,
        str(root / "scripts" / "pull_papers_from_postgres.py"),
        "--sqlite-path",
        sqlite_path,
        "--skip-init",
    ]
    for pid in sorted({int(p) for p in paper_ids}):
        cmd.extend(["--paper-id", str(pid)])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(root),
        check=False,
    )
    if proc.returncode != 0:
        return {
            "pulled": 0,
            "skipped": False,
            "error": (proc.stderr or proc.stdout or "").strip(),
            "returncode": proc.returncode,
        }
    return {"pulled": len(set(int(p) for p in paper_ids)), "skipped": False, "stdout": proc.stdout.strip()}


def build_manual_edit_batch(
    audit_groups: Dict[int, List[Dict[str, Any]]],
    db: DatabaseManager,
) -> Tuple[Dict[str, Any], List[str], str]:
    """Builds a calibration-compatible batch JSON from expert edit groups."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"manual_edit_{timestamp}"
    results: List[Dict[str, Any]] = []
    miss_reasons: List[str] = []
    subnode_counts: Dict[str, int] = defaultdict(int)
    pdf_cache: Dict[str, Optional[str]] = {}

    for paper_id, edits in sorted(audit_groups.items()):
        paper = db.get_paper(paper_id)
        if not paper:
            logger.warning("Paper %s not found; skipping manual edit batch row.", paper_id)
            continue

        edited_fields = [str(row["field_name"]) for row in edits]
        expert_block = paper_row_to_llm_block(
            paper,
            paper.get("title") or "",
            paper.get("abstract") or "",
            full_fields=True,
        )
        pre_edit_paper = reconstruct_pre_edit_paper(paper, edits)
        before_classifier_version = edits[-1].get("classifier_version") or paper.get("classifier_version")
        maude_block = run_maude_compare_block(paper, db, pdf_cache=pdf_cache)
        disagreement_fields = build_disagreement_fields(expert_block, maude_block, edited_fields)

        routing_subnode = infer_routing_subnode_for_paper(paper)
        subnode_counts[routing_subnode] += 1
        content_tier = content_tiers.infer_content_tier({
            **expert_block,
            "classifier_version": expert_block.get("classifier_version") or before_classifier_version,
        })

        for row in edits:
            miss_reasons.append(
                build_miss_reason(
                    str(row["field_name"]),
                    _parse_audit_value(row.get("old_value")),
                    _parse_audit_value(row.get("new_value")),
                    row.get("classifier_version") or before_classifier_version,
                )
            )

        if not disagreement_fields:
            for row in edits:
                field = str(row["field_name"])
                disagreement_fields[field] = {
                    "llm": expert_block.get(field),
                    "maude": _parse_audit_value(row.get("old_value")),
                    "expert": expert_block.get(field),
                }

        results.append({
            "paper_id": paper_id,
            "pmid": paper.get("pmid"),
            "title": paper.get("title"),
            "abstract": paper.get("abstract"),
            "full_text_link": paper.get("full_text_link"),
            "status": "maude_paired",
            "content_tier": content_tier,
            "routing_subnode": routing_subnode,
            "before_classifier_version": before_classifier_version,
            "miss_reasons": [
                build_miss_reason(
                    str(row["field_name"]),
                    _parse_audit_value(row.get("old_value")),
                    _parse_audit_value(row.get("new_value")),
                    row.get("classifier_version") or before_classifier_version,
                )
                for row in edits
            ],
            "llm": expert_block,
            "maude": maude_block,
            "disagreement": {"fields": disagreement_fields},
            "scoped_disagreement": {"fields": disagreement_fields},
        })

    target_subnode = max(subnode_counts.items(), key=lambda item: item[1])[0] if subnode_counts else "node2b"
    automation_node = target_subnode if target_subnode.startswith("node") else "node2b"
    fields_in_scope = subnode_field_scopes.fields_in_scope(automation_node)

    batch_payload = {
        "batch_id": batch_id,
        "created_at": datetime.now().isoformat(),
        "mode": "manual_edit",
        "rules_version": get_rules_version(),
        "automation_node": automation_node,
        "target_subnode": automation_node,
        "fields_in_scope": fields_in_scope,
        "compare_fields": list(MAUDE_AB_COMPARE_FIELDS),
        "full_fields_compare": True,
        "miss_reasons": miss_reasons,
        "expert_edit_count": sum(len(rows) for rows in audit_groups.values()),
        "results": results,
    }
    return batch_payload, miss_reasons, batch_id


def apply_cues_from_expert_edits(
    audit_groups: Dict[int, List[Dict[str, Any]]],
    db: DatabaseManager,
    output_dir: Path,
) -> Dict[str, Any]:
    """Applies runtime cue updates from expert edit miss explanations."""
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for paper_id, edits in audit_groups.items():
        paper = db.get_paper(paper_id) or {}
        abstract = paper.get("abstract") or ""
        for row in edits:
            field = str(row.get("field_name") or "")
            if not field:
                continue
            explanation = build_miss_reason(
                field,
                _parse_audit_value(row.get("old_value")),
                _parse_audit_value(row.get("new_value")),
                row.get("classifier_version"),
            )
            cue = maude_feedback.extract_cue_from_explanation(explanation, abstract=abstract)
            if not cue:
                skipped.append({"paper_id": paper_id, "field": field, "reason": "no cue extracted"})
                continue
            node_id = maude_feedback.FIELD_TO_NODE.get(field, "node1a_original")
            maude_feedback.apply_cue_update(
                node_id,
                cue,
                field,
                int(paper_id),
                explanation,
                output_dir=output_dir,
            )
            applied.append({"paper_id": paper_id, "field": field, "cue": cue, "node_id": node_id})
    return {
        "applied_cue_count": len(applied),
        "skipped_cue_count": len(skipped),
        "applied_cues": applied,
        "skipped_cues": skipped,
    }


def run_manual_edit_cycle(
    db: Optional[DatabaseManager] = None,
    *,
    since: Optional[str] = None,
    sqlite_path: Optional[str] = None,
    paper_ids: Optional[Sequence[int]] = None,
    dry_run: bool = False,
    apply_cues: bool = True,
    bump_version: bool = True,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detects expert edits, syncs locally, builds RL patch, bumps version/confidence."""
    db = db or DatabaseManager()
    if sqlite_path and os.getenv("DATABASE_URL"):
        from feedback_audit_sync import sync_feedback_audit_from_postgres

        sync_feedback_audit_from_postgres(sqlite_path)
    since_ts = resolve_since_timestamp(db, since)
    edits = fetch_expert_edits_since(db, since_ts, paper_ids=paper_ids)
    if not edits:
        report = {
            "edits_found": False,
            "status": "skipped",
            "reason": "no_expert_edits",
            "since": since_ts,
            "checked_at": utc_now_iso(),
        }
        db.set_metadata(METADATA_LAST_REPORT, json.dumps(report))
        return report

    audit_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in edits:
        audit_groups[int(row["paper_id"])].append(row)

    affected_ids = sorted(audit_groups.keys())
    pull_summary: Dict[str, Any] = {"skipped": True}
    if sqlite_path and os.getenv("DATABASE_URL"):
        pull_summary = pull_affected_papers(sqlite_path, affected_ids)

    resolved_output = output_dir or DEFAULT_OUTPUT_DIR
    resolved_output.mkdir(parents=True, exist_ok=True)

    batch_payload, miss_reasons, batch_id = build_manual_edit_batch(audit_groups, db)
    batch_path = resolved_output / f"{batch_id}.json"
    if dry_run:
        report = {
            "edits_found": True,
            "status": "dry_run",
            "since": since_ts,
            "paper_ids": affected_ids,
            "field_edit_count": len(edits),
            "miss_reasons": miss_reasons,
            "pull_summary": pull_summary,
            "batch_preview": batch_payload,
        }
        db.set_metadata(METADATA_LAST_REPORT, json.dumps(report, default=str))
        return report

    with open(batch_path, "w", encoding="utf-8") as handle:
        json.dump(batch_payload, handle, indent=2, default=str)

    feedback_result = cfa.run_feedback_cycle(
        batch_path,
        output_dir=resolved_output,
        db=db,
        skip_lock=True,
        local_only=True,
        skip_refresh=True,
    )

    cue_result: Dict[str, Any] = {"applied_cue_count": 0}
    if apply_cues:
        cue_result = apply_cues_from_expert_edits(audit_groups, db, resolved_output)

    version_before = version_after = get_rules_version()
    if bump_version:
        version_before, version_after = rules_version.bump_rules_patch_version()

    confidence_deltas: Dict[str, Dict[str, float]] = {}
    subnode_edit_counts: Dict[str, int] = defaultdict(int)
    for paper_id in affected_ids:
        paper = db.get_paper(paper_id)
        if not paper:
            continue
        subnode = infer_routing_subnode_for_paper(paper)
        subnode_edit_counts[subnode] += len(audit_groups.get(paper_id, []))

    for subnode, count in subnode_edit_counts.items():
        previous, new_value = maude_confidence.bump_alignment_for_subnode(
            subnode,
            count * CONFIDENCE_DELTA_PER_EDIT,
            output_dir=handoff_learning_log.resolve_log_path().parent,
        )
        confidence_deltas[subnode] = {"before": previous, "after": new_value, "edit_count": count}

    learning_notes = [
        f"Processed {len(edits)} expert field edits across {len(affected_ids)} papers since {since_ts}.",
        f"Top miss fields: {', '.join(sorted({str(r['field_name']) for r in edits})[:8])}.",
        f"Rules version {version_before} → {version_after}; staged patch: {feedback_result.get('staged_patch_path')}.",
    ]
    for reason in miss_reasons[:3]:
        learning_notes.append(reason)
    while len(learning_notes) < 3:
        learning_notes.append("Manual edit cycle completed; review staged patch before deploy.")

    handoff_entry = {
        "entry_type": "manual_edit",
        "source_subnode": batch_payload.get("target_subnode"),
        "beneficiary_nodes": list(subnode_edit_counts.keys()) or [batch_payload.get("target_subnode")],
        "post_patch_alignment_pct": max(
            (values["after"] for values in confidence_deltas.values()),
            default=None,
        ),
        "summary_title": f"Manual edit cycle ({batch_id})",
        "staged_patch_path": feedback_result.get("staged_patch_path"),
        "paper_ids": affected_ids,
        "field_edit_count": len(edits),
        "rules_version_before": version_before,
        "rules_version_after": version_after,
        "learning_notes": learning_notes[:8],
    }

    blast_payload: Dict[str, Any] = {}
    if sqlite_path and not dry_run:
        try:
            from reingest_heuristic_papers import run_two_pass_reingest

            os.environ.setdefault("DATABASE_PATH", sqlite_path)
            target_subnode = str(batch_payload.get("target_subnode") or "node2b")
            reingest_summary = run_two_pass_reingest(
                scope_subnode=target_subnode,
                paper_ids=affected_ids,
                skip_current_version=False,
                refresh_maude_confidence=True,
            )
            blast_payload = patch_blast_radius.run_finish_reporting(
                loop_type="manual_edit",
                patch_id=batch_id,
                reingest_summary=reingest_summary,
                scope_subnode=target_subnode,
                sqlite_path=sqlite_path,
                run_cohort=False,
            )
            handoff_entry["blast_radius_report_path"] = (
                (blast_payload.get("report_paths") or {}).get("html")
            )
            handoff_entry["papers_scanned"] = blast_payload.get("papers_scanned")
            handoff_entry["papers_changed"] = blast_payload.get("papers_changed")
            learning_notes.append(
                f"Blast-radius: scanned {blast_payload.get('papers_scanned')}, "
                f"changed {blast_payload.get('papers_changed')} on {target_subnode}."
            )
            handoff_entry["learning_notes"] = learning_notes[:8]
        except Exception as exc:
            logger.warning("Manual edit blast-radius finish failed: %s", exc)

    handoff_learning_log.append_handoff_entry(handoff_entry)

    now = utc_now_iso()
    db.set_metadata(METADATA_LAST_CYCLE, now)
    report = {
        "edits_found": True,
        "status": feedback_result.get("status", "completed"),
        "since": since_ts,
        "processed_at": now,
        "paper_ids": affected_ids,
        "field_edit_count": len(edits),
        "batch_path": str(batch_path),
        "staged_patch_path": feedback_result.get("staged_patch_path"),
        "feedback_report_path": feedback_result.get("report_path"),
        "agent_handoff_prompt": feedback_result.get("agent_handoff_prompt"),
        "pull_summary": pull_summary,
        "cue_result": cue_result,
        "rules_version_before": version_before,
        "rules_version_after": version_after,
        "confidence_deltas": confidence_deltas,
        "miss_reasons": miss_reasons,
        "blast_radius": blast_payload,
    }
    db.set_metadata(METADATA_LAST_REPORT, json.dumps(report, default=str))
    return report


def pending_edit_count(
    db: Optional[DatabaseManager] = None,
    *,
    since: Optional[str] = "last-harvest",
) -> int:
    """Returns expert drawer edits since the given watermark (default: last daily harvest)."""
    db = db or DatabaseManager()
    since_ts = resolve_since_timestamp(db, since)
    return db.count_expert_edits_since(since_ts, expert_drawer_only=True)


def pre_harvest_processing_since(db: DatabaseManager) -> str:
    """Watermark for pre-harvest processing: max(last harvest, last manual edit cycle)."""
    harvest_ts = db.get_metadata(METADATA_LAST_HARVEST) or "1970-01-01T00:00:00"
    cycle_ts = db.get_metadata(METADATA_LAST_CYCLE) or "1970-01-01T00:00:00"
    return max(harvest_ts, cycle_ts)


def should_run_pre_harvest_cycle(db: Optional[DatabaseManager] = None) -> bool:
    """True when expert edits exist since last daily harvest and are not yet processed."""
    db = db or DatabaseManager()
    harvest_ts = db.get_metadata(METADATA_LAST_HARVEST) or "1970-01-01T00:00:00"
    if db.count_expert_edits_since(harvest_ts, expert_drawer_only=True) == 0:
        return False
    since_ts = pre_harvest_processing_since(db)
    return db.count_expert_edits_since(since_ts, expert_drawer_only=True) > 0


def load_last_cycle_report(db: Optional[DatabaseManager] = None) -> Dict[str, Any]:
    """Loads the JSON summary from the last manual edit cycle."""
    db = db or DatabaseManager()
    raw = db.get_metadata(METADATA_LAST_REPORT) or "{}"
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def build_parser() -> argparse.ArgumentParser:
    """Builds the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Run manual expert-edit RL cycle.")
    parser.add_argument("--since", default="last-cycle", help="ISO timestamp or 'last-cycle' (default).")
    parser.add_argument("--sqlite-path", default="cannabis_papers.db", help="Local SQLite path for Postgres pull.")
    parser.add_argument("--paper-id", type=int, action="append", dest="paper_ids", help="Limit to paper id(s).")
    parser.add_argument("--dry-run", action="store_true", help="Detect and preview without writing artifacts.")
    parser.add_argument("--no-cues", action="store_true", help="Skip runtime cue overlay application.")
    parser.add_argument("--no-version-bump", action="store_true", help="Skip rules_config.json patch bump.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Artifact output directory.")
    return parser


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run_manual_edit_cycle(
        since=args.since,
        sqlite_path=args.sqlite_path,
        paper_ids=args.paper_ids,
        dry_run=args.dry_run,
        apply_cues=not args.no_cues,
        bump_version=not args.no_version_bump,
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
