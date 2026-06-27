#!/usr/bin/env python3
"""Pull papers from production Postgres into local SQLite for offline Maude reingest."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import reingest_heuristic_papers as rip
from db_manager import DatabaseManager
from local_sync import (
    DEFAULT_SQLITE_PATH,
    BASELINE_META_KEY,
    ensure_baseline_schema,
    fetch_postgres_paper_columns,
    fetch_sqlite_paper_columns,
    intersect_table_columns,
    pg_row_to_sqlite_value,
    save_baseline_rows,
    utc_now_iso,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Stream papers from Postgres into local SQLite, preserving production ids.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=DEFAULT_SQLITE_PATH,
        help=f"Local SQLite database path (default: {DEFAULT_SQLITE_PATH}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Postgres fetch batch size (default: 500).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of papers to pull.",
    )
    parser.add_argument(
        "--reingest-only",
        action="store_true",
        help="Pull only papers eligible for Maude two-pass reingest.",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        action="append",
        dest="paper_ids",
        help="Pull specific paper id(s) only.",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Skip local DatabaseManager.init_db() before pull.",
    )
    parser.add_argument(
        "--replace-all-baseline",
        action="store_true",
        help="Replace the entire push baseline with this pull (default: upsert pulled rows only).",
    )
    return parser


def require_database_url() -> None:
    """Exit when DATABASE_URL is missing."""
    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL must be set for pull.", file=sys.stderr)
        sys.exit(1)


def ensure_sqlite_paper_columns(conn: sqlite3.Connection, columns: Sequence[str]) -> None:
    """Add missing papers columns on local SQLite before upsert from Postgres."""
    existing = set(fetch_sqlite_paper_columns(conn))
    for col in columns:
        if col in existing:
            continue
        if col.startswith("tab_"):
            col_type = "INTEGER DEFAULT 0"
        elif col in {"open_access", "puff_count", "sample_size", "repeat_exposure_count", "citation_count"}:
            col_type = "INTEGER"
        elif col in {"thc_pct", "cbd_pct", "dose_mg", "duration_days", "classification_confidence"}:
            col_type = "REAL"
        else:
            col_type = "TEXT"
        conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {col_type}")
    conn.commit()


def ensure_local_sqlite(sqlite_path: str, *, skip_init: bool) -> sqlite3.Connection:
    """Ensure local SQLite schema exists and return a connection."""
    saved_url = os.environ.pop("DATABASE_URL", None)
    try:
        if not skip_init:
            db = DatabaseManager(db_path=sqlite_path)
            db.init_db()
    finally:
        if saved_url is not None:
            os.environ["DATABASE_URL"] = saved_url

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    ensure_baseline_schema(conn)
    return conn


def upsert_sqlite_batch(
    sqlite_conn: sqlite3.Connection,
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> int:
    """Insert or replace a batch of paper rows into local SQLite."""
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO papers ({col_sql}) VALUES ({placeholders})"
    values = []
    for row in rows:
        values.append(
            tuple(pg_row_to_sqlite_value(col, row.get(col)) for col in columns)
        )
    cur = sqlite_conn.cursor()
    cur.executemany(sql, values)
    sqlite_conn.commit()
    return len(rows)


def store_pull_metadata(sqlite_conn: sqlite3.Connection, *, row_count: int, reingest_only: bool) -> None:
    """Persist pull metadata in system_metadata for downstream scripts."""
    meta = {
        "pulled_at": utc_now_iso(),
        "row_count": row_count,
        "reingest_only": reingest_only,
    }
    sqlite_conn.execute(
        """
        INSERT OR REPLACE INTO system_metadata (key, value)
        VALUES (?, ?)
        """,
        (BASELINE_META_KEY, json.dumps(meta)),
    )
    sqlite_conn.commit()


def pull_papers(args: argparse.Namespace) -> Dict[str, Any]:
    """Pull papers from Postgres into local SQLite and refresh the push baseline."""
    require_database_url()
    sqlite_path = args.sqlite_path
    sqlite_conn = ensure_local_sqlite(sqlite_path, skip_init=args.skip_init)

    pg_db = DatabaseManager()
    pg_conn = pg_db.get_connection()
    sqlite_columns = fetch_sqlite_paper_columns(sqlite_conn)
    pg_columns = fetch_postgres_paper_columns(pg_conn.conn if hasattr(pg_conn, "conn") else pg_conn)
    columns = intersect_table_columns(pg_columns, sqlite_columns)
    if "id" not in columns:
        raise RuntimeError("Shared papers columns must include id.")
    ensure_sqlite_paper_columns(sqlite_conn, columns)

    col_sql = ", ".join(columns)
    last_id = 0
    pulled = 0
    baseline_rows: List[Dict[str, Any]] = []
    pulled_at = utc_now_iso()

    print(f"Pulling papers into {sqlite_path} ({len(columns)} columns)...")
    cur = pg_conn.cursor()
    while True:
        batch_limit = args.batch_size
        if args.limit is not None:
            remaining = args.limit - pulled
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        filters: List[str] = []
        params: List[Any] = []
        if args.paper_ids:
            placeholders = ", ".join("?" for _ in args.paper_ids)
            filters.append(f"id IN ({placeholders})")
            params.extend(int(pid) for pid in sorted(set(args.paper_ids)))
        else:
            filters.append("id > ?")
            params.append(last_id)
        if args.reingest_only:
            filters.append(f"({rip._reingest_where_clause(maude_and_heuristic=True)})")

        query = (
            f"SELECT {col_sql} FROM papers "
            f"WHERE {' AND '.join(filters)} "
            f"ORDER BY id ASC LIMIT ?"
        )
        params.append(batch_limit)
        cur.execute(query, tuple(params))
        batch = [dict(row) for row in cur.fetchall()]
        if not batch:
            break

        upserted = upsert_sqlite_batch(sqlite_conn, columns, batch)
        pulled += upserted
        baseline_rows.extend(batch)
        last_id = max(int(row["id"]) for row in batch)
        print(f"  pulled {pulled} papers (last id={last_id})", flush=True)

        if args.paper_ids:
            break

    save_baseline_rows(
        sqlite_conn,
        baseline_rows,
        pulled_at=pulled_at,
        replace_all=bool(args.replace_all_baseline and not args.paper_ids),
    )
    store_pull_metadata(sqlite_conn, row_count=pulled, reingest_only=args.reingest_only)

    sqlite_conn.close()
    pg_conn.close()
    return {"pulled": pulled, "sqlite_path": sqlite_path, "pulled_at": pulled_at}


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    started = time.time()
    summary = pull_papers(args)
    elapsed = time.time() - started
    print(
        f"Done: pulled {summary['pulled']} papers into {summary['sqlite_path']} "
        f"in {elapsed:.1f}s (baseline at {summary['pulled_at']})."
    )


if __name__ == "__main__":
    main()
