#!/usr/bin/env python3
"""Regression guard: Maude output vs confirmed golden ground_truth (subnode-scoped)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibration_pdf
import classification_schema
import content_tiers
import golden_confirmed_store
import subnode_field_scopes

logger = logging.getLogger(__name__)

# Free-text criteria strings are LLM extraction detail; golden guard uses calibration-style
# structured field alignment (same papers that drive the patch), not verbatim criteria text.
GOLDEN_GUARD_EXCLUDED_FIELDS = frozenset({
    "inclusion_criteria",
    "exclusion_criteria",
})
GOLDEN_MIN_ALIGNMENT_PCT = float(os.getenv("GOLDEN_MIN_ALIGNMENT_PCT", "90"))


def guard_fields_for_paper(paper: Dict[str, Any]) -> List[str]:
    """Returns ordered field names checked by the golden regression guard."""
    scope_subnode = str(paper.get("scope_subnode") or "").strip()
    ground_truth = paper.get("ground_truth") or {}
    scope_fields = list(paper.get("scope_fields") or [])
    if not scope_fields and scope_subnode:
        scope_fields = subnode_field_scopes.fields_in_scope(scope_subnode, ground_truth)

    fields: List[str] = []
    seen: set = set()
    for field in list(golden_confirmed_store.ROUTING_GROUND_TRUTH_FIELDS) + list(scope_fields):
        if field in GOLDEN_GUARD_EXCLUDED_FIELDS or field in content_tiers.ALIGNMENT_EXCLUDED_FIELDS:
            continue
        if field in seen:
            continue
        if field in ground_truth:
            seen.add(field)
            fields.append(field)
    return fields


def compare_ground_truth(
    maude_out: Dict[str, Any],
    ground_truth: Dict[str, Any],
    *,
    scope_subnode: Optional[str] = None,
    scope_fields: Optional[Sequence[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Returns field-level disagreements using calibration-style scoped comparison."""
    normalized_gt = {
        key: golden_confirmed_store._parse_field_value(key, value)
        for key, value in ground_truth.items()
    }
    paper_stub = {
        "scope_subnode": scope_subnode,
        "scope_fields": list(scope_fields or []),
        "ground_truth": normalized_gt,
    }
    fields = guard_fields_for_paper(paper_stub)
    if not fields:
        return {}

    scoped = subnode_field_scopes.compare_scoped_fields(
        maude_out,
        normalized_gt,
        scope_subnode or "node2a",
        classification_schema.compare_field_values,
        scope_fields=fields,
    )
    failures: Dict[str, Dict[str, Any]] = {}
    for field, payload in (scoped.get("fields") or {}).items():
        failures[field] = {
            "expected": payload.get("llm"),
            "got": payload.get("maude"),
        }
    return failures


def paper_alignment_rate(
    maude_out: Dict[str, Any],
    ground_truth: Dict[str, Any],
    *,
    scope_subnode: Optional[str] = None,
    scope_fields: Optional[Sequence[str]] = None,
) -> Optional[float]:
    """Returns scoped field alignment rate (0–1) for one golden paper."""
    normalized_gt = {
        key: golden_confirmed_store._parse_field_value(key, value)
        for key, value in ground_truth.items()
    }
    paper_stub = {
        "scope_subnode": scope_subnode,
        "scope_fields": list(scope_fields or []),
        "ground_truth": normalized_gt,
    }
    fields = guard_fields_for_paper(paper_stub)
    if not fields:
        return None

    scoped = subnode_field_scopes.compare_scoped_fields(
        maude_out,
        normalized_gt,
        scope_subnode or "node2a",
        classification_schema.compare_field_values,
        scope_fields=fields,
    )
    total = int(scoped.get("scoped_field_count") or len(fields))
    if not total:
        return None
    disagreements = len(scoped.get("fields") or {})
    return round((total - disagreements) / total, 4)


def classify_paper_maude(
    paper: Dict[str, Any],
    *,
    rules_version: str,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Runs local Maude classification for one golden confirmed paper record."""
    import paper_text_cache

    title = str(paper.get("title") or "")
    abstract = str(paper.get("abstract") or "")
    text_blob = paper.get("text")
    text_source = str(paper.get("text_source") or "")
    content_tier = str(paper.get("content_tier") or "")
    # Length/source wins over stale content_tier=abstract_only when PDF text is present.
    substantial = paper_text_cache.is_substantial_full_text(text_blob) or text_source in {
        "pdf_cache",
        "pdf",
        "fulltext",
    }
    if (
        content_tier in ("abstract_only", "abstract_reclassify")
        and not paper_text_cache.is_substantial_full_text(text_blob)
        and text_source not in {"pdf_cache", "pdf", "fulltext"}
    ):
        substantial = False

    if text_blob and not substantial:
        # Title+abstract candidate blob (tree_path_golden style).
        parts = str(text_blob).split("\n\n", 1)
        if len(parts) == 2:
            title = parts[0].strip() or title
            abstract = abstract or parts[1].strip()
        elif not abstract:
            abstract = str(text_blob)

    # Substantial PDF/fulltext: keep record title/abstract; text is full_text only.
    full_text = str(text_blob) if substantial and text_blob else None

    paper_id = paper.get("paper_id")
    maude_out, _ = calibration_pdf.classify_maude_for_calibration(
        title,
        abstract,
        full_text_link=paper.get("full_text_link"),
        full_text=full_text,
        pmid=paper.get("pmid"),
        doi=paper.get("doi"),
        paper_id=int(paper_id) if paper_id is not None else None,
        rules_version=rules_version,
        use_disk_cache=True,
        text_source_hint=text_source or None,
    )
    return maude_out


def run_golden_regression(
    scope_subnode: str,
    *,
    endpoint_id: Optional[str] = None,
    confirmed_path: Optional[Path] = None,
    rules_version: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    min_alignment_pct: float = GOLDEN_MIN_ALIGNMENT_PCT,
) -> Dict[str, Any]:
    """Runs golden regression against confirmed papers for a subnode (optionally one endpoint).

    When ``endpoint_id`` is set, only that endpoint's confirmed papers are checked. This
    prevents cross-endpoint regression loops during per-endpoint RL cycles.
    """
    store = golden_confirmed_store.load_confirmed(confirmed_path)
    papers = golden_confirmed_store.filter_by_scope_subnode(
        store.get("papers") or [],
        scope_subnode,
    )
    if endpoint_id:
        papers = golden_confirmed_store.filter_by_endpoint_id(papers, endpoint_id)

    if rules_version is None:
        rules_path = ROOT / "rules_config.json"
        rules_version = "1.0.0"
        if rules_path.exists():
            try:
                with open(rules_path, encoding="utf-8") as handle:
                    rules_version = str(json.load(handle).get("version") or rules_version)
            except Exception:
                pass

    paper_failures: List[Dict[str, Any]] = []
    paper_alignments: List[float] = []
    for paper in papers:
        ground_truth = paper.get("ground_truth") or {}
        if not ground_truth:
            continue
        try:
            maude_out = classify_paper_maude(
                paper,
                rules_version=rules_version,
                cache_dir=cache_dir,
            )
        except Exception as exc:
            paper_failures.append({
                "paper_id": paper.get("paper_id"),
                "endpoint_id": paper.get("endpoint_id"),
                "scope_subnode": paper.get("scope_subnode"),
                "error": str(exc),
                "fields": {},
            })
            continue

        scope_subnode_value = str(paper.get("scope_subnode") or scope_subnode)
        alignment_rate = paper_alignment_rate(
            maude_out,
            ground_truth,
            scope_subnode=scope_subnode_value,
            scope_fields=paper.get("scope_fields"),
        )
        if alignment_rate is not None:
            paper_alignments.append(alignment_rate)

        field_failures = compare_ground_truth(
            maude_out,
            ground_truth,
            scope_subnode=scope_subnode_value,
            scope_fields=paper.get("scope_fields"),
        )
        if field_failures or (
            alignment_rate is not None
            and alignment_rate < (min_alignment_pct / 100.0)
        ):
            paper_failures.append({
                "paper_id": paper.get("paper_id"),
                "endpoint_id": paper.get("endpoint_id"),
                "scope_subnode": paper.get("scope_subnode"),
                "alignment_rate": alignment_rate,
                "alignment_pct": round(alignment_rate * 100, 1) if alignment_rate is not None else None,
                "fields": field_failures,
            })

    batch_alignment_rate = (
        round(sum(paper_alignments) / len(paper_alignments), 4) if paper_alignments else None
    )
    batch_alignment_pct = (
        round(batch_alignment_rate * 100, 1) if batch_alignment_rate is not None else None
    )
    passed = (
        batch_alignment_pct is not None
        and batch_alignment_pct >= min_alignment_pct
    )

    return {
        "scope_subnode": scope_subnode,
        "endpoint_id": endpoint_id,
        "rules_version": rules_version,
        "papers_checked": len(papers),
        "papers_with_alignment": len(paper_alignments),
        "papers_failed": len(paper_failures),
        "batch_alignment_rate": batch_alignment_rate,
        "batch_alignment_pct": batch_alignment_pct,
        "min_alignment_pct": min_alignment_pct,
        "passed": passed,
        "failures": paper_failures,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Golden confirmed regression guard.")
    parser.add_argument(
        "--scope-subnode",
        required=True,
        help="Subnode to check (node2a, node2b, node2c).",
    )
    parser.add_argument(
        "--confirmed-path",
        default=str(golden_confirmed_store.DEFAULT_CONFIRMED_PATH),
        help="Path to golden_confirmed.json.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for golden_regression_failures.json.",
    )
    parser.add_argument(
        "--endpoint-id",
        default=None,
        help="Optional endpoint id to scope the guard (avoids cross-endpoint interference).",
    )
    parser.add_argument(
        "--min-alignment-pct",
        type=float,
        default=GOLDEN_MIN_ALIGNMENT_PCT,
        help="Minimum average batch alignment %% vs golden LLM (default 90).",
    )
    parser.add_argument(
        "--require-zero-regressions",
        action="store_true",
        help="Legacy alias: require 100%% batch alignment.",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()
    min_alignment_pct = float(args.min_alignment_pct)
    if args.require_zero_regressions:
        min_alignment_pct = 100.0

    report = run_golden_regression(
        args.scope_subnode,
        endpoint_id=args.endpoint_id,
        confirmed_path=Path(args.confirmed_path),
        min_alignment_pct=min_alignment_pct,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)

    print(json.dumps(report, indent=2))
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
