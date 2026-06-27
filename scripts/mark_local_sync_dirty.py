#!/usr/bin/env python3
"""Mark local SQLite papers as dirty so they are included in the next delta push."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_sync import DEFAULT_SQLITE_PATH, ensure_sync_schema, mark_papers_dirty


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Mark paper ids dirty for local→Postgres delta export/push.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=DEFAULT_SQLITE_PATH,
        help=f"Local SQLite database path (default: {DEFAULT_SQLITE_PATH}).",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        action="append",
        dest="paper_ids",
        required=True,
        help="Paper id(s) to mark dirty.",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    conn = sqlite3.connect(args.sqlite_path)
    ensure_sync_schema(conn)
    marked = mark_papers_dirty(conn, args.paper_ids)
    conn.close()
    print(f"Marked {marked} paper(s) dirty for push.")


if __name__ == "__main__":
    main()
