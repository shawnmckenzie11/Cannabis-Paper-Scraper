# audit_tier_field_gaps.py
"""Automated tier/field gap audit for RL calibration holdouts."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import classification_schema
import content_tiers
import subnode_field_scopes
from calibration_metrics import field_is_populated, score_paper_rl_metrics

DEFAULT_HOLDOUTS = {
    "node2a": "node2a_calibration_20260622_203356_002",
    "node2b": "node2b_calibration_20260622_200722_248",
    "node2c": "node2c_calibration_20260622_201423_837",
}


def resolve_output_dir(explicit: Optional[Path] = None) -> Path:
    """Returns calibration artifacts directory."""
    if explicit is not None:
        return explicit
    from maude_cues import resolve_calibration_output_dir

    return resolve_calibration_output_dir()


def load_batch(output_dir: Path, batch_id: str) -> Dict[str, Any]:
    """Loads a calibration batch JSON by batch id stem."""
    path = output_dir / f"{batch_id}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _disagreement_type(field: str, llm_val: Any, maude_val: Any) -> str:
    """Classifies a field disagreement for audit tagging."""
    llm_pop = field_is_populated(llm_val)
    maude_pop = field_is_populated(maude_val)
    if not llm_pop and maude_pop:
        return "maude_only_populated"
    if llm_pop and not maude_pop:
        return "claude_only_populated"
    if field in content_tiers.OPTIONAL_RECALL_FIELDS:
        return "optional_recall_only"
    return "value_mismatch"


def audit_batch(batch_payload: Dict[str, Any], subnode: str) -> Dict[str, Any]:
    """Audits one batch for tier scope, field gaps, and counterfactual exclusions."""
    tier_counts: Counter[str] = Counter()
    field_disagrees: Counter[str] = Counter()
    full_scope_disagrees: Counter[str] = Counter()
    disagreement_types: Counter[str] = Counter()
    optional_recall_hits = 0
    optional_recall_total = 0
    paper_rows: List[Dict[str, Any]] = []
    align_rates: List[float] = []

    for result in batch_payload.get("results") or []:
        llm = result.get("llm") or {}
        maude = result.get("maude") or {}
        if not llm or not maude:
            continue

        tier = result.get("content_tier") or content_tiers.infer_content_tier({
            **llm,
            "classifier_version": llm.get("classifier_version") or result.get("before_classifier_version"),
            "full_text_link": result.get("full_text_link"),
        })
        tier_counts[tier] += 1

        scored = score_paper_rl_metrics(result, subnode)
        if scored and scored.get("alignment_rate") is not None:
            align_rates.append(float(scored["alignment_rate"]))

        alignment_fields = content_tiers.alignment_fields_in_scope_for_tier(subnode, tier, llm)
        full_fields = content_tiers.fields_in_scope_for_tier(subnode, tier, llm)
        align_scoped = subnode_field_scopes.compare_scoped_fields(
            maude,
            llm,
            subnode,
            classification_schema.compare_field_values,
            scope_fields=alignment_fields,
        )
        full_scoped = subnode_field_scopes.compare_scoped_fields(
            maude,
            llm,
            subnode,
            classification_schema.compare_field_values,
            scope_fields=full_fields,
        )

        row_disagrees: Dict[str, str] = {}
        for field, payload in (align_scoped.get("fields") or {}).items():
            field_disagrees[field] += 1
            dtype = _disagreement_type(field, payload.get("llm"), payload.get("maude"))
            disagreement_types[dtype] += 1
            row_disagrees[field] = dtype
        for field in (full_scoped.get("fields") or {}):
            full_scope_disagrees[field] += 1

        for field in content_tiers.OPTIONAL_RECALL_FIELDS:
            if not field_is_populated(llm.get(field)):
                continue
            optional_recall_total += 1
            if classification_schema.compare_field_values(maude.get(field), llm.get(field)):
                optional_recall_hits += 1

        if row_disagrees:
            paper_rows.append({
                "paper_id": result.get("paper_id"),
                "title": (result.get("title") or "")[:80],
                "content_tier": tier,
                "scope_key": align_scoped.get("scope_key"),
                "disagreements": row_disagrees,
            })

    counterfactual: Dict[str, float] = {}
    if align_rates:
        base = round(100 * sum(align_rates) / len(align_rates), 1)
        counterfactual["alignment_pct_current"] = base
        for field, count in field_disagrees.most_common(8):
            if count <= 0:
                continue
            # Approximate upper bound if field were perfectly aligned.
            total_fields = sum(
                len(content_tiers.alignment_fields_in_scope_for_tier(
                    subnode,
                    row.get("content_tier") or content_tiers.CONTENT_TIER_PDF_EXTRACTED,
                ))
                for row in paper_rows
            ) or len(align_rates) * 10
            counterfactual[f"if_{field}_fixed_upper_bound"] = round(
                min(100.0, base + (100 * count / max(total_fields, 1))),
                1,
            )

    return {
        "subnode": subnode,
        "batch_id": batch_payload.get("batch_id"),
        "paper_count": len(align_rates),
        "alignment_pct": round(100 * sum(align_rates) / len(align_rates), 1) if align_rates else None,
        "optional_strain_recall_pct": (
            round(100 * optional_recall_hits / optional_recall_total, 1)
            if optional_recall_total
            else None
        ),
        "tier_counts": dict(tier_counts),
        "top_alignment_disagrees": field_disagrees.most_common(10),
        "top_full_scope_disagrees": full_scope_disagrees.most_common(10),
        "disagreement_types": dict(disagreement_types),
        "counterfactual": counterfactual,
        "sample_papers": paper_rows[:12],
    }


def run_audit(
    holdouts: Dict[str, str],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Runs tier/field gap audit across configured holdout batches."""
    output_dir = resolve_output_dir(output_dir)
    nodes: Dict[str, Any] = {}
    for subnode, batch_id in holdouts.items():
        payload = load_batch(output_dir, batch_id)
        nodes[subnode] = audit_batch(payload, subnode)
    return {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "gate_mode": "holdout_field_subset",
        "alignment_excluded_fields": sorted(content_tiers.ALIGNMENT_EXCLUDED_FIELDS),
        "optional_recall_fields": sorted(content_tiers.OPTIONAL_RECALL_FIELDS),
        "nodes": nodes,
    }


def write_audit_report(
    holdouts: Optional[Dict[str, str]] = None,
    output_dir: Optional[Path] = None,
    report_path: Optional[Path] = None,
) -> Path:
    """Writes tier/field gap audit JSON and returns its path."""
    output_dir = resolve_output_dir(output_dir)
    holdouts = holdouts or DEFAULT_HOLDOUTS
    report = run_audit(holdouts, output_dir)
    dest = report_path or (output_dir / "tier_field_gap_audit.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    return dest


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI parser for tier/field gap audit."""
    parser = argparse.ArgumentParser(description="Audit tier-scoped RL field gaps on holdout batches.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Calibration artifacts directory (default: scratch or Fly /data).",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Where to write tier_field_gap_audit.json.",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    args = build_arg_parser().parse_args()
    dest = write_audit_report(output_dir=args.output_dir, report_path=args.report_path)
    report = json.loads(dest.read_text(encoding="utf-8"))
    print(f"Wrote {dest}")
    for subnode, node in (report.get("nodes") or {}).items():
        print(
            f"{subnode}: alignment={node.get('alignment_pct')}% "
            f"strain_recall={node.get('optional_strain_recall_pct')}% "
            f"top={node.get('top_alignment_disagrees', [])[:3]}"
        )


if __name__ == "__main__":
    main()
