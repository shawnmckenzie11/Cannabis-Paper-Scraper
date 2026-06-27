#!/usr/bin/env python3
"""Apply exported classification deltas to production Postgres."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_health import is_transient_db_error, postgres_is_healthy
from db_manager import DatabaseManager
from local_sync import push_update_sql
from push_resilience import (
    DEFAULT_COMMIT_TIMEOUT_SECONDS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    configure_postgres_push_session,
    commit_with_timeout,
    reconnect_postgres,
)
from reingest_heuristic_papers import DB_WRITE_MAX_RETRIES, DB_WRITE_RETRY_BASE_SECONDS

DEFAULT_BATCH_SIZE = 25
DEFAULT_BATCH_PAUSE_SECONDS = 0.25


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Apply JSONL classification deltas to Postgres.",
    )
    parser.add_argument(
        "input",
        help="JSONL file produced by export_classification_deltas.py.",
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
        help=f"Pause between batches (default: {DEFAULT_BATCH_PAUSE_SECONDS}).",
    )
    parser.add_argument(
        "--commit-timeout-seconds",
        type=float,
        default=float(
            os.getenv("PUSH_COMMIT_TIMEOUT_SECONDS", DEFAULT_COMMIT_TIMEOUT_SECONDS)
        ),
        help="Hard timeout for Postgres commit calls.",
    )
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=int(os.getenv("PUSH_STATEMENT_TIMEOUT_MS", DEFAULT_STATEMENT_TIMEOUT_MS)),
        help="Postgres statement_timeout for the push session.",
    )
    parser.add_argument(
        "--skip-health-check",
        action="store_true",
        help="Skip Postgres health probe before apply.",
    )
    return parser


def iter_payloads(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield one delta payload per JSONL line."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def apply_one(conn, payload: Dict[str, Any]) -> bool:
    """Apply one merged UPDATE on Postgres. Returns True when a write occurred."""
    current = dict(payload)
    paper_id = int(current["id"])
    current["id"] = paper_id
    sql, params = push_update_sql(current)
    if not sql:
        return False
    conn.execute(sql, params)
    return True


def apply_with_retry(
    db: DatabaseManager,
    conn,
    *,
    payload: Dict[str, Any],
    statement_timeout_ms: int,
) -> Any:
    """Apply one delta with exponential backoff on transient Postgres errors."""
    last_exc: Optional[Exception] = None
    paper_id = int(payload["id"])
    for attempt in range(DB_WRITE_MAX_RETRIES):
        try:
            wrote = apply_one(conn, payload)
            return conn, wrote
        except Exception as exc:
            last_exc = exc
            if not is_transient_db_error(exc):
                raise
            delay = DB_WRITE_RETRY_BASE_SECONDS * (2**attempt)
            print(
                f"  transient error on paper {paper_id}; retry {attempt + 1}/{DB_WRITE_MAX_RETRIES} "
                f"after {delay:.1f}s ({exc})",
                flush=True,
            )
            time.sleep(delay)
            conn = reconnect_postgres(db, statement_timeout_ms=statement_timeout_ms)
    if last_exc is not None:
        raise last_exc
    return conn, False


def apply_deltas(args: argparse.Namespace) -> Dict[str, Any]:
    """Apply all JSONL deltas to Postgres."""
    url = os.getenv("DATABASE_URL") or ""
    if not url.startswith("postgres://") and not url.startswith("postgresql://"):
        print("ERROR: DATABASE_URL must point at Postgres.", file=sys.stderr)
        sys.exit(1)

    if not args.skip_health_check:
        healthy, detail = postgres_is_healthy()
        if not healthy:
            print(f"ERROR: Postgres unhealthy ({detail}).", file=sys.stderr)
            sys.exit(1)

    input_path = Path(args.input)
    payloads: List[Dict[str, Any]] = list(iter_payloads(input_path))
    print(f"Applying {len(payloads)} delta(s) from {input_path}.")

    db = DatabaseManager()
    conn = reconnect_postgres(db, statement_timeout_ms=args.statement_timeout_ms)
    applied = 0
    batch_count = 0

    for payload in payloads:
        conn, wrote = apply_with_retry(
            db,
            conn,
            payload=payload,
            statement_timeout_ms=args.statement_timeout_ms,
        )
        if wrote:
            applied += 1
            batch_count += 1
            if batch_count >= args.batch_size:
                commit_with_timeout(conn, timeout_seconds=args.commit_timeout_seconds)
                print(f"  committed {applied} update(s)...", flush=True)
                batch_count = 0
                if args.batch_pause_seconds > 0:
                    time.sleep(args.batch_pause_seconds)

    if batch_count:
        commit_with_timeout(conn, timeout_seconds=args.commit_timeout_seconds)

    conn.close()
    return {"delta_count": len(payloads), "applied": applied}


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    started = time.time()
    summary = apply_deltas(args)
    elapsed = time.time() - started
    print(
        f"Done: applied {summary['applied']} of {summary['delta_count']} delta(s) "
        f"in {elapsed:.1f}s."
    )


if __name__ == "__main__":
    main()
