#!/usr/bin/env python3
"""Run Claude golden patch feedback on an existing golden disagreement batch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibration_feedback_agent as cfa
from env_secrets import load_anthropic_api_key


def build_parser() -> argparse.ArgumentParser:
    """CLI argument parser."""
    parser = argparse.ArgumentParser(description="Golden Claude patch feedback on disagreement batch.")
    parser.add_argument(
        "batch_path",
        type=Path,
        help="Path to golden_disagreement_*.json batch file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Artifact output directory (defaults to batch parent dir).",
    )
    parser.add_argument(
        "--llm-results",
        type=Path,
        default=None,
        help="Path to llm_results.json with candidate paper text (defaults to sibling).",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    args = build_parser().parse_args()
    batch_path = args.batch_path.resolve()
    if not batch_path.exists():
        raise SystemExit(f"Batch not found: {batch_path}")

    output_dir = args.output_dir or batch_path.parent
    llm_path = args.llm_results or batch_path.parent / "llm_results.json"

    if not load_anthropic_api_key():
        raise SystemExit(
            "ANTHROPIC_API_KEY not found (set env, migrations/env.py, or .env).",
        )

    result = cfa.run_golden_feedback_cycle(
        batch_path,
        output_dir=output_dir,
        llm_results_path=llm_path if llm_path.exists() else None,
        skip_lock=True,
        skip_refresh=True,
    )
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
