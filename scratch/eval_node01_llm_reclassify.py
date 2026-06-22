#!/usr/bin/env python3
"""Evaluate Maude node 0/1 accuracy against stored llm-reclassify classifications."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import classification_schema
import maude_classifier
from calibration_agent import paper_row_to_llm_block, select_llm_pdf_reclassify_candidates


def main() -> int:
    """Print node 0/1 agreement metrics and failure samples for llm-reclassify papers."""
    candidates = select_llm_pdf_reclassify_candidates(
        fetch_limit=10000,
        include_abstract_reclassify=True,
    )
    abstract_only = [
        row
        for row in candidates
        if str(row.get("classifier_version", "")).startswith("llm-reclassify-")
    ]
    print("llm-reclassify count:", len(abstract_only))

    fields = ("ingestion_status", "publication_type")
    stats = {field: {"agree": 0, "disagree": 0} for field in fields}
    pub_failures = []
    ing_failures = []
    both_ok = 0

    for candidate in abstract_only:
        title = candidate.get("title") or ""
        abstract = candidate.get("abstract") or ""
        llm = paper_row_to_llm_block(candidate, title, abstract)
        maude = maude_classifier.classify_paper(title, abstract)
        comparison = classification_schema.compare_classifiers(maude, llm, title, abstract)
        disagree = set((comparison.get("fields") or {}).keys())

        for field in fields:
            if field in disagree:
                stats[field]["disagree"] += 1
            else:
                stats[field]["agree"] += 1

        if not (disagree & set(fields)):
            both_ok += 1
        else:
            if "publication_type" in disagree:
                field_data = comparison["fields"]["publication_type"]
                pub_failures.append({
                    "id": candidate["id"],
                    "maude": field_data["maude"],
                    "llm": field_data["llm"],
                    "title": title,
                    "abstract": abstract,
                    "nodes": (maude.get("_maude_meta") or {}).get("nodes_visited"),
                })
            if "ingestion_status" in disagree:
                field_data = comparison["fields"]["ingestion_status"]
                ing_failures.append({
                    "id": candidate["id"],
                    "maude": field_data["maude"],
                    "llm": field_data["llm"],
                    "title": title,
                    "abstract": abstract,
                })

    total = len(abstract_only) or 1
    for field in fields:
        agree = stats[field]["agree"]
        print(f"{field}: {agree / total * 100:.1f}% ({agree}/{total})")
    print(f"both: {both_ok / total * 100:.1f}% ({both_ok}/{total})")
    print(f"pub failures: {len(pub_failures)} ing failures: {len(ing_failures)}")

    print("pub patterns:", Counter((str(x["maude"]), str(x["llm"])) for x in pub_failures).most_common(12))
    print("ing patterns:", Counter((str(x["maude"]), str(x["llm"])) for x in ing_failures).most_common(12))

    out_path = Path(__file__).resolve().parent / "node01_llm_reclassify_failures.json"
    out_path.write_text(
        json.dumps({"pub_failures": pub_failures, "ing_failures": ing_failures}, indent=2),
        encoding="utf-8",
    )
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
