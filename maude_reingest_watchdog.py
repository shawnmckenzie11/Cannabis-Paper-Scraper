"""Watchdog: restart detached Maude two-pass re-ingest if the bulk job stops."""

from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db_manager import DatabaseManager
from db_health import postgres_configured, postgres_is_healthy, production_reingest_limits

logger = logging.getLogger("maude.watchdog")

WATCHDOG_LOG = os.getenv("MAUDE_WATCHDOG_LOG", "/data/maude_watchdog.log")
REINGEST_LOG = os.getenv("MAUDE_REINGEST_LOG", "/data/maude_nightly_reclassify.log")
WATCHDOG_INTERVAL_MINUTES = int(os.getenv("MAUDE_WATCHDOG_INTERVAL_MINUTES", "30"))
DB_RETRY_ATTEMPTS = int(os.getenv("MAUDE_WATCHDOG_DB_RETRIES", "3"))
DB_RETRY_SECONDS = float(os.getenv("MAUDE_WATCHDOG_DB_RETRY_SECONDS", "5"))
METADATA_LAST_RUN = "maude_watchdog_last_run"
METADATA_LAST_ACTION = "maude_watchdog_last_action"
METADATA_BULK_COMPLETE = "maude_bulk_reingest_completed"
METADATA_BULK_ACTIVE = "maude_bulk_reingest_active"
METADATA_BULK_ACTIVE_CONFIG = "maude_bulk_reingest_active_config"


def _now_iso() -> str:
    """Returns current UTC time as ISO string."""
    return datetime.now(tz=timezone.utc).isoformat()


def is_reingest_running() -> bool:
    """Returns True when a reingest_heuristic_papers.py process is active."""
    for proc_dir in glob.glob("/proc/[0-9]*"):
        try:
            cmdline = open(f"{proc_dir}/cmdline", "rb").read().decode("utf-8", errors="replace")
            if "reingest_heuristic_papers.py" in cmdline:
                return True
        except OSError:
            continue
    return False


def _with_db_retries(fn, *args, **kwargs):
    """Runs a DB callable with short retries on transient connection errors."""
    last_exc = None
    for attempt in range(1, DB_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            transient = any(
                token in msg
                for token in (
                    "server closed the connection",
                    "connection unexpectedly",
                    "could not connect",
                    "connection refused",
                    "timeout",
                )
            )
            if not transient or attempt >= DB_RETRY_ATTEMPTS:
                raise
            logger.warning("DB transient error (attempt %s/%s): %s", attempt, DB_RETRY_ATTEMPTS, exc)
            time.sleep(DB_RETRY_SECONDS)
    raise last_exc  # pragma: no cover


def count_papers_needing_reingest(db: Optional[DatabaseManager] = None) -> int:
    """Counts non-LLM maude/heuristic original-research papers not on Maude 2.6.0 tiers."""
    import classifier
    from reingest_heuristic_papers import _reingest_where_clause

    rules = classifier.get_rules_version()
    current_tiers = (
        f"maude-{rules}",
        f"maude-pdf-{rules}",
        f"maude-fulltext-{rules}",
    )
    db = db or DatabaseManager()
    conn = db.get_connection()
    cur = conn.cursor()
    base = _reingest_where_clause(maude_and_heuristic=True)
    tier_list = ", ".join(f"'{v}'" for v in current_tiers)
    cur.execute(
        f"""
        SELECT COUNT(*) AS c FROM papers
        WHERE {base}
          AND classifier_version NOT IN ({tier_list})
        """
    )
    row = cur.fetchone()
    conn.close()
    if hasattr(row, "keys"):
        return int(list(row.values())[0])
    return int(row[0])


def start_detached_two_pass(
    *,
    batch_size: int = 50,
    workers: int = 4,
    workers_fast: Optional[int] = None,
    log_path: Optional[str] = None,
) -> int:
    """Starts two-pass Maude re-ingest detached; returns subprocess pid."""
    limits = production_reingest_limits()
    if postgres_configured():
        if batch_size >= 50:
            batch_size = limits["batch_size"]
        if workers >= 4:
            workers = limits["workers"]
        if workers_fast is None:
            workers_fast = limits["workers_fast"]
    if workers_fast is None:
        workers_fast = int(os.getenv("WORKERS_FAST", "4"))

    log_path = log_path or REINGEST_LOG
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    log_file.write(f"\n=== watchdog restart {_now_iso()} ===\n")
    log_file.flush()
    cmd = [
        "python3",
        "-u",
        "/app/reingest_heuristic_papers.py",
        "--pass",
        "two-pass",
        "--maude-and-heuristic",
        "--batch-size",
        str(batch_size),
        "--workers",
        str(workers),
        "--workers-fast",
        str(workers_fast),
        "--refresh-maude-confidence",
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd="/app",
        env=env,
    )
    logger.info("Started detached Maude re-ingest pid=%s cmd=%s", proc.pid, cmd)
    return proc.pid


def is_bulk_watchdog_active(db: Optional[DatabaseManager] = None) -> bool:
    """Returns True when the 30-minute watchdog is armed for an active bulk run."""
    db = db or DatabaseManager()
    try:
        return db.get_metadata(METADATA_BULK_ACTIVE) == "true"
    except Exception:
        return False


def activate_bulk_watchdog(
    db: Optional[DatabaseManager] = None,
    *,
    batch_size: int = 50,
    workers: int = 4,
) -> None:
    """Arms the watchdog for the current bulk re-ingest session."""
    db = db or DatabaseManager()
    db.set_metadata(METADATA_BULK_ACTIVE, "true")
    db.set_metadata(METADATA_BULK_COMPLETE, "")
    db.set_metadata(
        METADATA_BULK_ACTIVE_CONFIG,
        json.dumps({"batch_size": batch_size, "workers": workers}),
    )
    db.set_metadata(METADATA_LAST_RUN, "")
    logger.info(
        "Armed Maude bulk watchdog (batch_size=%s, workers=%s)",
        batch_size,
        workers,
    )


def deactivate_bulk_watchdog(db: Optional[DatabaseManager] = None) -> None:
    """Disarms the watchdog after bulk re-ingest completes."""
    db = db or DatabaseManager()
    db.set_metadata(METADATA_BULK_ACTIVE, "")
    db.set_metadata(METADATA_BULK_ACTIVE_CONFIG, "")
    logger.info("Disarmed Maude bulk watchdog")


def _active_watchdog_config(db: DatabaseManager) -> Dict[str, int]:
    """Returns batch_size/workers saved when the bulk session was armed."""
    raw = db.get_metadata(METADATA_BULK_ACTIVE_CONFIG) or "{}"
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        config = {}
    return {
        "batch_size": int(config.get("batch_size") or 50),
        "workers": int(config.get("workers") or 4),
    }


def run_watchdog(
    db: Optional[DatabaseManager] = None,
    *,
    force: bool = False,
    batch_size: int = 50,
    workers: int = 4,
) -> Dict[str, Any]:
    """Checks bulk re-ingest health and restarts if stopped with work remaining.

    Args:
        db: Optional database manager.
        force: When True, run even if interval has not elapsed.
        batch_size: Re-ingest commit batch size when restarting.
        workers: Slow-pass worker count when restarting.

    Returns:
        Action summary dict persisted to system metadata.
    """
    db = db or DatabaseManager()
    if not force and not is_bulk_watchdog_active(db):
        return {
            "action": "idle",
            "reason": "watchdog_not_active",
            "at": _now_iso(),
        }

    active_config = _active_watchdog_config(db)
    batch_size = active_config["batch_size"]
    workers = active_config["workers"]

    try:
        if db.get_metadata(METADATA_BULK_COMPLETE) == "true":
            deactivate_bulk_watchdog(db)
            summary = {"action": "idle", "reason": "bulk_marked_complete", "at": _now_iso()}
            db.set_metadata(METADATA_LAST_ACTION, json.dumps(summary))
            return summary
    except Exception as exc:
        logger.warning("Could not read bulk-complete metadata: %s", exc)

    last_run_raw = None
    try:
        last_run_raw = db.get_metadata(METADATA_LAST_RUN)
    except Exception as exc:
        logger.warning("Could not read watchdog last-run metadata: %s", exc)
    if last_run_raw and not force:
        try:
            last_run = datetime.fromisoformat(last_run_raw)
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            elapsed_min = (datetime.now(tz=timezone.utc) - last_run).total_seconds() / 60
            if elapsed_min < WATCHDOG_INTERVAL_MINUTES:
                return {
                    "action": "skipped_interval",
                    "minutes_until_next": round(WATCHDOG_INTERVAL_MINUTES - elapsed_min, 1),
                    "at": _now_iso(),
                }
        except ValueError:
            pass

    try:
        db.set_metadata(METADATA_LAST_RUN, _now_iso())
    except Exception as exc:
        logger.warning("Could not persist watchdog last-run metadata: %s", exc)
    running = is_reingest_running()
    remaining: Optional[int] = None
    db_error: Optional[str] = None

    try:
        remaining = _with_db_retries(count_papers_needing_reingest, db)
    except Exception as exc:
        db_error = str(exc)
        logger.error("Could not count remaining papers: %s", exc)

    if running:
        summary = {
            "action": "running",
            "remaining": remaining,
            "at": _now_iso(),
        }
    elif remaining == 0:
        try:
            db.set_metadata(METADATA_BULK_COMPLETE, "true")
            deactivate_bulk_watchdog(db)
        except Exception as exc:
            logger.warning("Could not persist bulk-complete metadata: %s", exc)
        summary = {
            "action": "complete",
            "remaining": 0,
            "at": _now_iso(),
        }
    elif remaining is None:
        summary = {
            "action": "db_unavailable",
            "reason": "skip_restart_while_postgres_down",
            "db_error": db_error,
            "at": _now_iso(),
        }
    else:
        healthy, health_detail = postgres_is_healthy()
        if postgres_configured() and not healthy:
            summary = {
                "action": "db_unavailable",
                "reason": "skip_restart_while_postgres_down",
                "db_error": health_detail,
                "remaining": remaining,
                "at": _now_iso(),
            }
        else:
            pid = start_detached_two_pass(batch_size=batch_size, workers=workers)
            summary = {
                "action": "restarted",
                "pid": pid,
                "remaining": remaining,
                "at": _now_iso(),
            }

    try:
        db.set_metadata(METADATA_LAST_ACTION, json.dumps(summary))
    except Exception as exc:
        logger.warning("Could not persist watchdog action metadata: %s", exc)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps(summary) + "\n")
    except OSError as exc:
        logger.debug("Could not write watchdog log: %s", exc)

    logger.info("Maude re-ingest watchdog: %s", summary)
    return summary


def ensure_reingest_running(
    *,
    batch_size: int = 50,
    workers: int = 4,
) -> Dict[str, Any]:
    """Starts detached re-ingest when no process is running and Postgres is healthy."""
    if is_reingest_running():
        return {"action": "running", "at": _now_iso()}
    healthy, detail = postgres_is_healthy()
    if postgres_configured() and not healthy:
        return {
            "action": "db_unavailable",
            "reason": "skip_start_while_postgres_down",
            "db_error": detail,
            "at": _now_iso(),
        }
    pid = start_detached_two_pass(batch_size=batch_size, workers=workers)
    return {"action": "restarted", "pid": pid, "at": _now_iso()}


def main() -> None:
    """CLI entry point for manual watchdog runs."""
    import argparse

    parser = argparse.ArgumentParser(description="Maude bulk re-ingest watchdog.")
    parser.add_argument("--force", action="store_true", help="Run immediately (ignore 30m interval).")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--status", action="store_true", help="Print watchdog and bulk status.")
    parser.add_argument("--disarm", action="store_true", help="Stop auto-restart until next scheduled bulk job.")
    parser.add_argument("--start-only", action="store_true", help="Start re-ingest if stopped and Postgres is healthy.")
    args = parser.parse_args()

    if args.disarm:
        try:
            db = DatabaseManager()
            deactivate_bulk_watchdog(db)
            print(json.dumps({"action": "disarmed", "at": _now_iso()}, indent=2))
        except Exception as exc:
            print(json.dumps({"action": "disarm_failed", "error": str(exc)}, indent=2))
        return

    if args.start_only:
        result = ensure_reingest_running(batch_size=args.batch_size, workers=args.workers)
        print(json.dumps(result, indent=2))
        return

    if args.status:
        healthy, health_detail = postgres_is_healthy()
        status = {
            "postgres_healthy": healthy,
            "postgres_detail": health_detail,
            "reingest_running": is_reingest_running(),
        }
        try:
            db = DatabaseManager()
            status["watchdog_active"] = is_bulk_watchdog_active(db)
            raw = db.get_metadata(METADATA_LAST_ACTION) or "{}"
            status["last_action"] = json.loads(raw) if raw.startswith("{") else raw
            try:
                status["remaining"] = _with_db_retries(count_papers_needing_reingest, db)
            except Exception as exc:
                status["remaining"] = None
                status["remaining_error"] = str(exc)
        except Exception as exc:
            status["db_error"] = str(exc)
        print(json.dumps(status, indent=2))
        return

    try:
        db = DatabaseManager()
    except Exception as exc:
        if args.force:
            print(json.dumps({"db_error": str(exc), "action": "db_unavailable"}, indent=2))
            return
        raise

    result = run_watchdog(db, force=args.force, batch_size=args.batch_size, workers=args.workers)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
