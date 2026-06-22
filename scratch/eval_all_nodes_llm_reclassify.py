#!/usr/bin/env python3
"""Evaluate Maude all-node alignment against llm-reclassify paper classifications."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import classification_schema as cs
import maude_classifier as mc
from calibration_agent import paper_row_to_llm_block, select_llm_pdf_reclassify_candidates

TARGET_AGREE_PCT = 85.0


def load_eval_papers(eval_path: Path | None) -> List[Dict[str, Any]]:
    """Loads llm-reclassify eval papers from JSON export or the active database."""
    if eval_path and eval_path.exists():
        payload = json.loads(eval_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload.get("papers") or []
        return payload

    papers: List[Dict[str, Any]] = []
    for row in select_llm_pdf_reclassify_candidates(
        fetch_limit=10000,
        include_abstract_reclassify=True,
        abstract_reclassify_only=True,
    ):
        title = row.get("title") or ""
        abstract = row.get("abstract") or ""
        papers.append({
            "paper_id": row.get("id"),
            "title": title,
            "abstract": abstract,
            "classifier_version": row.get("classifier_version"),
            "llm": paper_row_to_llm_block(row, title, abstract),
        })
    return papers


def summarize_alignment(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes per-field and all-field Maude vs LLM agreement metrics."""
    fields = cs.HIGH_LEVEL_COMPARE_FIELDS
    stats = {field: {"agree": 0, "disagree": 0} for field in fields}
    node_visits: Counter = Counter()
    study_type_patterns: Counter = Counter()
    all_fields_agree = 0
    total = len(papers) or 1

    for paper in papers:
        title = paper.get("title") or ""
        abstract = paper.get("abstract") or ""
        llm = paper.get("llm") or {}
        maude = mc.classify_paper(title, abstract)
        comparison = cs.compare_classifiers(maude, llm, title, abstract)
        disagree = set((comparison.get("fields") or {}).keys())
        if not disagree:
            all_fields_agree += 1
        for field in fields:
            if field in disagree:
                stats[field]["disagree"] += 1
                if field == "study_type":
                    field_data = comparison["fields"][field]
                    study_type_patterns[(str(field_data.get("maude")), str(field_data.get("llm")))] += 1
            else:
                stats[field]["agree"] += 1
        for node_id in (maude.get("_maude_meta") or {}).get("nodes_visited") or []:
            node_visits[node_id] += 1

    field_summary = {}
    for field in fields:
        agree_pct = stats[field]["agree"] / total * 100.0
        field_summary[field] = {
            "agree": stats[field]["agree"],
            "disagree": stats[field]["disagree"],
            "agree_pct": round(agree_pct, 1),
            "disagree_pct": round(100.0 - agree_pct, 1),
            "meets_target": agree_pct >= TARGET_AGREE_PCT,
        }

    return {
        "paper_count": len(papers),
        "target_agree_pct": TARGET_AGREE_PCT,
        "all_fields_agree_pct": round(all_fields_agree / total * 100.0, 1),
        "fields": field_summary,
        "nodes_visited": dict(node_visits.most_common()),
        "study_type_disagreement_patterns": [
            {"maude": maude, "llm": llm, "count": count}
            for (maude, llm), count in study_type_patterns.most_common(15)
        ],
        "all_fields_meet_target": all(
            field_summary[field]["meets_target"] for field in fields
        ),
    }


def main() -> int:
    """Print all-node Maude vs llm-reclassify alignment and optionally write JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=Path("scratch/calibration_runs/llm_reclassify_eval_set.json"),
        help="Path to exported llm-reclassify eval JSON (default: scratch export).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scratch/calibration_runs/all_nodes_llm_reclassify_eval.json"),
        help="Optional JSON output path for metrics payload.",
    )
    args = parser.parse_args()

    papers = load_eval_papers(args.eval_set if args.eval_set.exists() else None)
    if not papers:
        print("No llm-reclassify papers found.", file=sys.stderr)
        return 1

    metrics = summarize_alignment(papers)
    print(f"papers: {metrics['paper_count']}")
    print(f"target: >={TARGET_AGREE_PCT}% agree per field")
    for field, row in metrics["fields"].items():
        status = "OK" if row["meets_target"] else "MISS"
        print(f"{field}: {row['agree_pct']}% agree ({row['disagree_pct']}% disagree) [{status}]")
    print(f"all fields agree: {metrics['all_fields_agree_pct']}%")
    print(f"nodes visited: {metrics['nodes_visited']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0 if metrics["all_fields_meet_target"] else 2


if __name__ == "__main__":
    sys.exit(main())
