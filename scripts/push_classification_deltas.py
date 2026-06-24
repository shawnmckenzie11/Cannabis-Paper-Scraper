#!/usr/bin/env python3
"""Push local SQLite classification deltas back to production Postgres."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_health import is_transient_db_error, postgres_is_healthy
from db_manager import DatabaseManager
from local_sync import (
    BASELINE_META_KEY,
    DEFAULT_SQLITE_PATH,
    collect_delta_papers,
    ensure_baseline_schema,
    merged_update_sql,
    paper_row_to_extracted,
    refresh_baseline_after_push,
)
from reingest_heuristic_papers import DB_WRITE_MAX_RETRIES, DB_WRITE_RETRY_BASE_SECONDS

DEFAULT_BATCH_SIZE = 25
DEFAULT_BATCH_PAUSE_SECONDS = 0.15


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Push changed classification fields from local SQLite to Postgres.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=DEFAULT_SQLITE_PATH,
        help=f"Local SQLite database path (default: {DEFAULT_SQLITE_PATH}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("REINGEST_BATCH_SIZE", DEFAULT_BATCH_SIZE)),
        help=f"Commit every N updates (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--batch-pause-seconds",
        type=float,
        default=float(os.getenv("REINGEST_BATCH_PAUSE_SECONDS", DEFAULT_BATCH_PAUSE_SECONDS)),
        help=f"Pause between batches on Postgres (default: {DEFAULT_BATCH_PAUSE_SECONDS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report deltas without writing to Postgres.",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        action="append",
        dest="paper_ids",
        help="Push specific paper id(s) only.",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip Postgres health probe before push.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of delta rows to push.",
    )
    return parser


def require_postgres_configured() -> None:
    """Exit when DATABASE_URL is not configured for Postgres."""
    url = os.getenv("DATABASE_URL") or ""
    if not url.startswith("postgres://") and not url.startswith("postgresql://"):
        print("ERROR: DATABASE_URL must point at Postgres for delta push.", file=sys.stderr)
        sys.exit(1)


def load_pull_metadata(sqlite_conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Return metadata written by pull_papers_from_postgres.py when present."""
    cur = sqlite_conn.cursor()
    cur.execute("SELECT value FROM system_metadata WHERE key = ?", (BASELINE_META_KEY,))
    row = cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def push_one_paper(
    conn,
    *,
    current: Dict[str, Any],
) -> bool:
    """Apply one merged UPDATE on Postgres. Returns True when a write occurred."""
    extracted = paper_row_to_extracted(current)
    sql, params = merged_update_sql(current, extracted)
    if not sql:
        return False
    conn.execute(sql, params)
    return True


def push_with_retry(
    db: DatabaseManager,
    conn,
    *,
    current: Dict[str, Any],
) -> Any:
    """Push one paper with exponential backoff on transient Postgres errors."""
    last_exc: Optional[Exception] = None
    for attempt in range(DB_WRITE_MAX_RETRIES):
        try:
            wrote = push_one_paper(conn, current=current)
            return conn, wrote
        except Exception as exc:
            last_exc = exc
            if not is_transient_db_error(exc):
                raise
            delay = DB_WRITE_RETRY_BASE_SECONDS * (2**attempt)
            time.sleep(delay)
            conn = db.get_connection()
    if last_exc is not None:
        raise last_exc
    return conn, False


def push_deltas(args: argparse.Namespace) -> Dict[str, Any]:
    """Push local classification deltas to Postgres."""
    require_postgres_configured()
    if not args.skip_health_check:
        healthy, detail = postgres_is_healthy()
        if not healthy:
            print(f"ERROR: Postgres unhealthy ({detail}); aborting push.", file=sys.stderr)
            sys.exit(1)

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    ensure_baseline_schema(sqlite_conn)
    pull_meta = load_pull_metadata(sqlite_conn)
    if pull_meta:
        print(
            f"Using baseline from pull at {pull_meta.get('pulled_at')} "
            f"({pull_meta.get('row_count')} rows)."
        )

    paper_id_set: Optional[Set[int]] = set(args.paper_ids) if args.paper_ids else None
    deltas = collect_delta_papers(sqlite_conn, paper_ids=paper_id_set)
    if args.limit is not None:
        deltas = deltas[: args.limit]

    print(f"Found {len(deltas)} classification delta(s) to push.")
    if args.dry_run:
        sample_ids = [int(current["id"]) for _, current in deltas[:10]]
        sqlite_conn.close()
        return {
            "delta_count": len(deltas),
            "dry_run": True,
            "sample_ids": sample_ids,
            "pushed": 0,
        }

    if not deltas:
        sqlite_conn.close()
        return {"delta_count": 0, "dry_run": False, "pushed": 0, "skipped": 0}

    db = DatabaseManager()
    conn = db.get_connection()
    pushed = 0
    pushed_ids: List[int] = []
    batch_count = 0

    for _baseline, current in deltas:
        conn, wrote = push_with_retry(
            db,
            conn,
            current=current,
        )
        if wrote:
            pushed += 1
            pushed_ids.append(int(current["id"]))
            batch_count += 1
            if batch_count >= args.batch_size:
                conn.commit()
                print(f"  committed {pushed} update(s)...", flush=True)
                batch_count = 0
                if args.batch_pause_seconds > 0:
                    time.sleep(args.batch_pause_seconds)

    if batch_count:
        conn.commit()

    refresh_baseline_after_push(sqlite_conn, pushed_ids)
    conn.close()
    sqlite_conn.close()
    return {
        "delta_count": len(deltas),
        "dry_run": False,
        "pushed": pushed,
        "skipped": len(deltas) - pushed,
    }


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    started = time.time()
    summary = push_deltas(args)
    elapsed = time.time() - started
    if summary.get("dry_run"):
        print(
            f"Dry run: {summary['delta_count']} delta(s); "
            f"sample ids={summary.get('sample_ids', [])} ({elapsed:.1f}s)."
        )
        return
    print(
        f"Done: pushed {summary['pushed']} of {summary['delta_count']} delta(s) "
        f"in {elapsed:.1f}s."
    )


if __name__ == "__main__":
    main()
