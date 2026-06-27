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
    ensure_sync_schema,
    push_update_sql,
    refresh_baseline_after_push,
    utc_now_iso,
)
from push_resilience import (
    DEFAULT_CHECKPOINT_PATH,
    DEFAULT_COMMIT_TIMEOUT_SECONDS,
    DEFAULT_STALL_SECONDS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    EXIT_FATAL,
    EXIT_OK,
    EXIT_RESUMABLE,
    PushProgressTracker,
    PushStalledError,
    commit_with_timeout,
    configure_postgres_push_session,
    load_push_checkpoint,
    reconnect_postgres,
    save_push_checkpoint,
    wait_for_postgres_recovery,
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
        "--stall-seconds",
        type=float,
        default=float(os.getenv("PUSH_STALL_SECONDS", DEFAULT_STALL_SECONDS)),
        help=f"Abort when no progress for this many seconds (default: {DEFAULT_STALL_SECONDS}).",
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
        help="Postgres statement_timeout for each push session.",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=str(DEFAULT_CHECKPOINT_PATH),
        help="JSON checkpoint path for resumable push progress.",
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
        sys.exit(EXIT_FATAL)


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
    sql, params = push_update_sql(current)
    if not sql:
        return False
    conn.execute(sql, params)
    return True


def push_with_retry(
    db: DatabaseManager,
    conn,
    *,
    current: Dict[str, Any],
    statement_timeout_ms: int,
    tracker: PushProgressTracker,
) -> Any:
    """Push one paper with exponential backoff on transient Postgres errors."""
    last_exc: Optional[Exception] = None
    paper_id = int(current["id"])
    for attempt in range(DB_WRITE_MAX_RETRIES):
        tracker.check_stalled()
        tracker.touch(f"paper_id={paper_id} attempt={attempt + 1}")
        try:
            wrote = push_one_paper(conn, current=current)
            tracker.touch(f"paper_id={paper_id} wrote={wrote}")
            return conn, wrote
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, PushStalledError):
                raise
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


def persist_batch_checkpoint(
    *,
    checkpoint_file: Path,
    checkpoint: Dict[str, Any],
    pushed_total: int,
    delta_total: int,
    batch_ids: List[int],
    status: str,
    detail: str = "",
) -> None:
    """Write checkpoint state after a durable batch commit."""
    checkpoint["total_pushed_lifetime"] = int(checkpoint.get("total_pushed_lifetime", 0)) + len(
        batch_ids
    )
    checkpoint["session_pushed"] = pushed_total
    checkpoint["remaining_estimate"] = max(delta_total - pushed_total, 0)
    checkpoint["last_paper_id"] = batch_ids[-1] if batch_ids else checkpoint.get("last_paper_id")
    checkpoint["last_batch_size"] = len(batch_ids)
    checkpoint["status"] = status
    checkpoint["detail"] = detail
    save_push_checkpoint(checkpoint, checkpoint_file)


def finalize_batch(
    *,
    conn,
    sqlite_conn: sqlite3.Connection,
    batch_ids: List[int],
    pushed_total: int,
    delta_total: int,
    checkpoint_file: Path,
    checkpoint: Dict[str, Any],
    commit_timeout_seconds: float,
    tracker: PushProgressTracker,
) -> None:
    """Commit one Postgres batch, persist baseline, and update checkpoint."""
    if not batch_ids:
        return
    commit_with_timeout(conn, timeout_seconds=commit_timeout_seconds)
    refresh_baseline_after_push(sqlite_conn, batch_ids)
    persist_batch_checkpoint(
        checkpoint_file=checkpoint_file,
        checkpoint=checkpoint,
        pushed_total=pushed_total,
        delta_total=delta_total,
        batch_ids=batch_ids,
        status="in_progress",
        detail=f"committed batch ending paper_id={batch_ids[-1]}",
    )
    print(f"  committed {pushed_total} update(s)...", flush=True)
    tracker.touch(f"committed {pushed_total}")


def push_deltas(args: argparse.Namespace) -> Dict[str, Any]:
    """Push local classification deltas to Postgres."""
    require_postgres_configured()
    checkpoint_file = Path(args.checkpoint_path)
    prior_checkpoint = load_push_checkpoint(checkpoint_file) or {}
    if prior_checkpoint.get("status") == "complete":
        print("Prior checkpoint reports push complete; verifying remaining deltas.")

    if not args.skip_health_check:
        healthy, detail = postgres_is_healthy()
        if not healthy:
            print(f"ERROR: Postgres unhealthy ({detail}); aborting push.", file=sys.stderr)
            sys.exit(EXIT_FATAL)

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    ensure_sync_schema(sqlite_conn)
    pull_meta = load_pull_metadata(sqlite_conn)
    if pull_meta:
        print(
            f"Using baseline from pull at {pull_meta.get('pulled_at')} "
            f"({pull_meta.get('row_count')} rows)."
        )
    if prior_checkpoint:
        print(
            "Resuming from checkpoint: "
            f"lifetime_pushed={prior_checkpoint.get('total_pushed_lifetime', 0)} "
            f"last_paper_id={prior_checkpoint.get('last_paper_id')} "
            f"status={prior_checkpoint.get('status')}"
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
        save_push_checkpoint(
            {
                **prior_checkpoint,
                "status": "complete",
                "remaining_estimate": 0,
                "completed_at": utc_now_iso(),
            },
            checkpoint_file,
        )
        sqlite_conn.close()
        return {"delta_count": 0, "dry_run": False, "pushed": 0, "skipped": 0}

    checkpoint: Dict[str, Any] = {
        **prior_checkpoint,
        "started_at": prior_checkpoint.get("started_at") or utc_now_iso(),
        "status": "in_progress",
        "remaining_at_start": len(deltas),
        "session_pushed": 0,
    }
    save_push_checkpoint(checkpoint, checkpoint_file)

    db = DatabaseManager()
    conn = reconnect_postgres(db, statement_timeout_ms=args.statement_timeout_ms)
    tracker = PushProgressTracker(stall_seconds=args.stall_seconds)
    tracker.start()

    pushed = 0
    batch_ids: List[int] = []
    batch_count = 0
    partial = False
    partial_reason = ""

    try:
        for _baseline, current in deltas:
            tracker.check_stalled()
            tracker.maybe_log_heartbeat(pushed=pushed, delta_total=len(deltas))
            conn, wrote = push_with_retry(
                db,
                conn,
                current=current,
                statement_timeout_ms=args.statement_timeout_ms,
                tracker=tracker,
            )
            if wrote:
                paper_id = int(current["id"])
                pushed += 1
                batch_ids.append(paper_id)
                batch_count += 1
                if batch_count >= args.batch_size:
                    finalize_batch(
                        conn=conn,
                        sqlite_conn=sqlite_conn,
                        batch_ids=batch_ids,
                        pushed_total=pushed,
                        delta_total=len(deltas),
                        checkpoint_file=checkpoint_file,
                        checkpoint=checkpoint,
                        commit_timeout_seconds=args.commit_timeout_seconds,
                        tracker=tracker,
                    )
                    batch_ids = []
                    batch_count = 0
                    if args.batch_pause_seconds > 0:
                        time.sleep(args.batch_pause_seconds)

        if batch_count:
            finalize_batch(
                conn=conn,
                sqlite_conn=sqlite_conn,
                batch_ids=batch_ids,
                pushed_total=pushed,
                delta_total=len(deltas),
                checkpoint_file=checkpoint_file,
                checkpoint=checkpoint,
                commit_timeout_seconds=args.commit_timeout_seconds,
                tracker=tracker,
            )
    except PushStalledError as exc:
        partial = True
        partial_reason = str(exc)
        print(f"STALL: {partial_reason}", flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        save_push_checkpoint(
            {
                **checkpoint,
                "status": "stalled",
                "session_pushed": pushed,
                "remaining_estimate": max(len(deltas) - pushed, 0),
                "detail": partial_reason,
            },
            checkpoint_file,
        )
    except Exception as exc:
        partial = True
        partial_reason = str(exc)
        print(f"ERROR during push: {partial_reason}", flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        save_push_checkpoint(
            {
                **checkpoint,
                "status": "error",
                "session_pushed": pushed,
                "remaining_estimate": max(len(deltas) - pushed, 0),
                "detail": partial_reason,
            },
            checkpoint_file,
        )
    finally:
        tracker.stop()
        try:
            conn.close()
        except Exception:
            pass

    remaining = collect_delta_papers(sqlite_conn, paper_ids=paper_id_set)
    if args.limit is not None:
        remaining = remaining[: max(args.limit - pushed, 0)]

    if remaining:
        partial = True
        partial_reason = f"{len(remaining)} delta(s) still remain after session"
        save_push_checkpoint(
            {
                **checkpoint,
                "status": "in_progress",
                "session_pushed": pushed,
                "remaining_estimate": len(remaining),
                "detail": partial_reason,
            },
            checkpoint_file,
        )
    else:
        save_push_checkpoint(
            {
                **checkpoint,
                "status": "complete",
                "session_pushed": pushed,
                "remaining_estimate": 0,
                "completed_at": utc_now_iso(),
                "detail": "all deltas pushed",
            },
            checkpoint_file,
        )

    sqlite_conn.close()
    return {
        "delta_count": len(deltas),
        "dry_run": False,
        "pushed": pushed,
        "skipped": len(deltas) - pushed,
        "partial": partial,
        "partial_reason": partial_reason,
        "remaining": len(remaining),
        "resumable": partial,
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
        raise SystemExit(EXIT_OK)

    print(
        f"Done: pushed {summary['pushed']} of {summary['delta_count']} delta(s) "
        f"in {elapsed:.1f}s."
    )
    if summary.get("partial"):
        print(f"Partial: {summary.get('partial_reason', 'remaining work')}", flush=True)
        raise SystemExit(EXIT_RESUMABLE)
    raise SystemExit(EXIT_OK)


if __name__ == "__main__":
    main()
