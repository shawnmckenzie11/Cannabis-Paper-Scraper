#!/usr/bin/env python3
"""Filter Agent — audit and enforce dashboard UI filter tier policy.

Validates that the global filter bar exposes only §5.1 bibliographic controls
(plus ``has_pdf`` / ``has_full_text``) and that tab sidebar profiles reference
registered §5.2 / §5.3 sections. Run after changing ``dashboard_ui_config.py`` or
``templates/index.html`` filter markup.

Usage:
    python3 filter_agent.py
    python3 filter_agent.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from dashboard_ui_config import (
    FILTER_PROFILES,
    FILTER_SECTION_REGISTRY,
    GLOBAL_FILTER_CONTROLS,
    SCHEMA_TIER_51_FIELDS,
    build_dashboard_ui_config,
    sections_for_tab,
    validate_filter_config,
)


def run_audit() -> Dict[str, Any]:
    """Run the filter policy audit and return a structured report."""
    errors = validate_filter_config()
    config = build_dashboard_ui_config()

    tab_summary: List[Dict[str, Any]] = []
    for tab in config.get("tabs") or []:
        tab_key = tab["key"]
        section_ids = sections_for_tab(tab_key)
        tab_summary.append(
            {
                "tab": tab_key,
                "label": tab.get("label"),
                "node": FILTER_PROFILES.get(tab_key, {}).get("node"),
                "sections": section_ids,
                "tiers": {
                    section_id: FILTER_SECTION_REGISTRY.get(section_id, {}).get("tier")
                    for section_id in section_ids
                },
            }
        )

    return {
        "ok": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
        "global_filters": GLOBAL_FILTER_CONTROLS,
        "schema_tier_51_fields": list(SCHEMA_TIER_51_FIELDS),
        "tab_filter_profiles": tab_summary,
    }


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Audit dashboard filter tier policy.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full report as JSON instead of human-readable text.",
    )
    args = parser.parse_args()
    report = run_audit()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "PASS" if report["ok"] else "FAIL"
        print(f"Filter Agent audit: {status} ({report['error_count']} issue(s))")
        print()
        print("Global bar (§5.1 + has_pdf/has_full_text):")
        for control in report["global_filters"]:
            print(f"  - {control['label']} → param={control['param']} fields={control['fields']}")
        print()
        print("Tab sidebar profiles:")
        for tab in report["tab_filter_profiles"]:
            tiers = ", ".join(f"{k}({v})" for k, v in tab["tiers"].items())
            print(f"  - {tab['label']} [{tab['tab']}]: {tiers}")
        if report["errors"]:
            print()
            print("Violations:")
            for err in report["errors"]:
                print(f"  - {err}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
