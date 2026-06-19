#!/usr/bin/env python3
"""Compile rules_config decision_nodes + maude section into maude_tree.json."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RULES_PATH = REPO / "rules_config.json"
TREE_DOC = REPO / "Cannabis_Classification_Decision_Tree"
OUT_PATH = REPO / "maude_tree.json"


def main() -> None:
    """Writes maude_tree.json from rules_config and expert tree doc metadata."""
    with open(RULES_PATH, encoding="utf-8") as handle:
        rules = json.load(handle)

    payload = {
        "version": rules.get("version"),
        "source": str(TREE_DOC.name),
        "compiled_from": "rules_config.decision_nodes + rules_config.maude",
        "decision_nodes": rules.get("decision_nodes") or {},
        "maude": rules.get("maude") or {},
        "high_level_fields": list(
            rules.get("maude", {}).get(
                "high_level_fields",
                [
                    "ingestion_status",
                    "publication_type",
                    "study_type",
                    "exposure_method",
                    "cannabis_type",
                    "outcome_domain",
                    "species",
                ],
            )
        ),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
