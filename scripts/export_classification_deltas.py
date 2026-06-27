#!/usr/bin/env python3
"""Export local SQLite classification deltas to a compact JSONL file."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_sync import (
    DEFAULT_SQLITE_PATH,
    collect_delta_papers,
    ensure_sync_schema,
    push_tracked_columns,
)

DEFAULT_OUTPUT = Path("scratch/classification_deltas.jsonl")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Export classification deltas from local SQLite to JSONL.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=DEFAULT_SQLITE_PATH,
        help=f"Local SQLite database path (default: {DEFAULT_SQLITE_PATH}).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT}).",
    )
    return parser


def row_payload(current: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact payload with fields needed for Postgres merged UPDATE."""
    tracked = push_tracked_columns()
    payload = {"id": int(current["id"])}
    for col in tracked:
        payload[col] = current.get(col)
    return payload


def export_deltas(sqlite_path: str, output_path: Path) -> Dict[str, Any]:
    """Write delta rows to JSONL and return a summary dict."""
    conn = sqlite3.connect(sqlite_path)
    ensure_sync_schema(conn)
    deltas = collect_delta_papers(conn)
    conn.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for _baseline, current in deltas:
            handle.write(json.dumps(row_payload(current), sort_keys=True))
            handle.write("\n")

    return {"delta_count": len(deltas), "output": str(output_path)}


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    summary = export_deltas(args.sqlite_path, Path(args.output))
    size_mb = Path(args.output).stat().st_size / (1024 * 1024)
    print(
        f"Exported {summary['delta_count']} delta(s) to {summary['output']} ({size_mb:.1f} MB)."
    )


if __name__ == "__main__":
    main()
