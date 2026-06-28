#!/usr/bin/env python3
"""Build HTML/Markdown tables of Maude field changes after golden subnode reingest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import patch_blast_radius
from calibration_build import MAUDE_CLASSIFIER_BUILD_ID

REPORT_DIR = ROOT / "scratch/golden_dataset/node_reingest_reports"


def _load_reingest_summary(
    *,
    artifact_dir: Optional[Path],
    summary_json: Optional[Path],
) -> Dict[str, Any]:
    """Load reingest summary from cycle artifact or explicit JSON path."""
    if summary_json and summary_json.is_file():
        with open(summary_json, encoding="utf-8") as handle:
            return json.load(handle)
    if artifact_dir and artifact_dir.is_file():
        with open(artifact_dir, encoding="utf-8") as handle:
            return json.load(handle)
    if artifact_dir:
        return patch_blast_radius.load_reingest_from_artifact(artifact_dir)
    return {}


def write_reports(
    endpoint_id: str,
    summary: Dict[str, Any],
    *,
    cycle_id: Optional[str] = None,
    patch_id: Optional[str] = None,
    push_summary: Optional[Dict[str, Any]] = None,
    cohort_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Write HTML and Markdown reports via patch_blast_radius; return output paths."""
    slug = patch_id or cycle_id or patch_blast_radius.utc_now_iso().replace(":", "")
    ctx = patch_blast_radius.PatchFinishContext(
        loop_type="golden_b",
        patch_id=slug,
        scope_subnode=summary.get("scope_subnode"),
        endpoint_id=endpoint_id,
        reingest_summary=summary,
        push_summary=push_summary,
        cohort_validation=cohort_validation,
        maude_build_id=MAUDE_CLASSIFIER_BUILD_ID,
        sqlite_path=str(ROOT / "cannabis_papers.db"),
    )
    paths = patch_blast_radius.write_blast_radius_reports(ctx)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe = endpoint_id.replace(".", "_")
    latest_html = REPORT_DIR / f"{safe}_latest.html"
    latest_md = REPORT_DIR / f"{safe}_latest.md"
    latest_html.write_text(Path(paths["html"]).read_text(encoding="utf-8"), encoding="utf-8")
    latest_md.write_text(Path(paths["markdown"]).read_text(encoding="utf-8"), encoding="utf-8")
    return paths


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Golden subnode reingest field-change report.")
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--artifact-dir", default=None, help="Cycle artifact directory")
    parser.add_argument("--summary-json", default=None, help="Explicit reingest summary JSON")
    parser.add_argument("--cycle-id", default=None)
    parser.add_argument("--patch-id", default=None)
    parser.add_argument("--push-json", default=None)
    parser.add_argument("--cohort-json", default=None)
    args = parser.parse_args()

    artifact = Path(args.artifact_dir) if args.artifact_dir else None
    summary_path = Path(args.summary_json) if args.summary_json else None
    summary = _load_reingest_summary(artifact_dir=artifact or summary_path, summary_json=summary_path)
    if not summary:
        raise SystemExit("No reingest summary found.")

    push_summary = None
    if args.push_json:
        with open(args.push_json, encoding="utf-8") as handle:
            push_summary = json.load(handle)
    cohort = None
    if args.cohort_json:
        with open(args.cohort_json, encoding="utf-8") as handle:
            cohort = json.load(handle)

    paths = write_reports(
        args.endpoint_id,
        summary,
        cycle_id=args.cycle_id,
        patch_id=args.patch_id,
        push_summary=push_summary,
        cohort_validation=cohort,
    )
    print(json.dumps({"endpoint_id": args.endpoint_id, "reports": paths}, indent=2))


if __name__ == "__main__":
    main()
