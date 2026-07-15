"""One-shot background jobs persisted in DB metadata and executed by the app scheduler."""

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from db_manager import DatabaseManager

logger = logging.getLogger("scheduler.jobs")
JOB_METADATA_KEY = "scheduled_jobs"
DEFAULT_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "America/Toronto")


def _now_iso() -> str:
    """Returns the current UTC timestamp as an ISO string."""
    return datetime.now(tz=ZoneInfo("UTC")).isoformat()


def load_jobs(db: Optional[DatabaseManager] = None) -> List[Dict[str, Any]]:
    """Loads scheduled job records from metadata."""
    db = db or DatabaseManager()
    raw = db.get_metadata(JOB_METADATA_KEY, "[]")
    try:
        jobs = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return jobs if isinstance(jobs, list) else []


def save_jobs(jobs: List[Dict[str, Any]], db: Optional[DatabaseManager] = None) -> None:
    """Persists scheduled job records to metadata."""
    db = db or DatabaseManager()
    db.set_metadata(JOB_METADATA_KEY, json.dumps(jobs))


def parse_local_run_at(
    at_time: str,
    run_date: Optional[str] = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    """Parses a local date/time string into a timezone-aware datetime."""
    tz = ZoneInfo(timezone_name)
    day = date.fromisoformat(run_date) if run_date else datetime.now(tz).date()
    if " " in at_time.strip():
        local_dt = datetime.strptime(at_time.strip(), "%Y-%m-%d %H:%M")
    elif ":" in at_time:
        hour, minute = at_time.strip().split(":", 1)
        local_dt = datetime(day.year, day.month, day.day, int(hour), int(minute))
    else:
        raise ValueError(f"Unsupported time format: {at_time!r}")
    return local_dt.replace(tzinfo=tz)


def schedule_maude_reingest(
    at_time: str,
    run_date: Optional[str] = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    batch_size: int = 25,
    refresh_maude_confidence: bool = True,
    maude_and_heuristic: bool = False,
    job_id: Optional[str] = None,
    db: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """Registers a one-shot Maude re-ingest job for heuristic/maude papers."""
    db = db or DatabaseManager()
    run_at = parse_local_run_at(at_time, run_date=run_date, timezone_name=timezone_name)
    if run_at <= datetime.now(tz=run_at.tzinfo):
        raise ValueError(
            f"Scheduled time {run_at.isoformat()} is in the past; choose a future run time."
        )

    jobs = load_jobs(db)
    for job in jobs:
        if job.get("type") == "maude_reingest" and job.get("status") == "pending":
            raise ValueError(
                f"A pending Maude re-ingest is already scheduled for {job.get('run_at')}."
            )

    record = {
        "id": job_id or f"maude_reingest_{run_at.strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:8]}",
        "type": "maude_reingest",
        "run_at": run_at.isoformat(),
        "timezone": timezone_name,
        "status": "pending",
        "created_at": _now_iso(),
        "payload": {
            "batch_size": batch_size,
            "refresh_maude_confidence": refresh_maude_confidence,
            "maude_and_heuristic": maude_and_heuristic,
            "two_pass": maude_and_heuristic,
            "workers": 1,
            "detached": maude_and_heuristic,
        },
    }
    jobs.append(record)
    save_jobs(jobs, db)
    return record


def cancel_job(job_id: str, db: Optional[DatabaseManager] = None) -> bool:
    """Marks a pending job as cancelled."""
    db = db or DatabaseManager()
    jobs = load_jobs(db)
    updated = False
    for job in jobs:
        if job.get("id") == job_id and job.get("status") == "pending":
            job["status"] = "cancelled"
            job["cancelled_at"] = _now_iso()
            updated = True
    if updated:
        save_jobs(jobs, db)
    return updated


def _execute_maude_reingest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Runs the Maude two-pass or legacy re-ingest pipeline."""
    import reingest_heuristic_papers
    from db_health import postgres_configured, postgres_is_healthy, production_reingest_limits

    limits = production_reingest_limits()
    batch_size = int(payload.get("batch_size") or limits["batch_size"])
    maude_and_heuristic = bool(payload.get("maude_and_heuristic"))
    two_pass = bool(payload.get("two_pass", maude_and_heuristic))
    workers = int(payload.get("workers") or limits["workers"])
    detached = bool(payload.get("detached", two_pass and maude_and_heuristic))

    if postgres_configured():
        healthy, detail = postgres_is_healthy()
        if not healthy:
            return {
                "status": "skipped",
                "reason": "postgres_unavailable",
                "detail": detail,
            }

    if detached:
        from maude_reingest_watchdog import (
            REINGEST_LOG,
            activate_bulk_watchdog,
            start_detached_two_pass,
        )

        activate_bulk_watchdog(batch_size=batch_size, workers=workers)
        pid = start_detached_two_pass(batch_size=batch_size, workers=workers)
        return {
            "detached": True,
            "pid": pid,
            "pass_mode": "two-pass" if two_pass else "full",
            "batch_size": batch_size,
            "workers": workers,
            "log_path": REINGEST_LOG,
        }

    if two_pass and maude_and_heuristic:
        summary = reingest_heuristic_papers.run_two_pass_reingest(
            batch_size=batch_size,
            workers=workers,
            refresh_maude_confidence=bool(payload.get("refresh_maude_confidence", True)),
        )
    else:
        summary = reingest_heuristic_papers.reingest_heuristic_papers(
            dry_run=False,
            batch_size=batch_size,
            only_heuristic=not maude_and_heuristic,
            maude_and_heuristic=maude_and_heuristic,
            pass_mode=str(payload.get("pass_mode") or "full"),
            workers=workers,
        )
        if payload.get("refresh_maude_confidence") or summary.get("papers_processed", 0) > 0:
            summary["confidence_refresh"] = reingest_heuristic_papers.refresh_maude_confidence_scores(
                batch_size=batch_size
            )
    return summary


def run_post_harvest_maude_upgrade(
    paper_ids: List[int],
    *,
    batch_size: int = 25,
    workers: int = 4,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Retry PDF/full-text Maude classification for papers ingested on the latest harvest.

    Open-access ids are ordered first so the slow pass spends early capacity on papers
    most likely to yield PDF/full-text upgrades.
    """
    from db_health import postgres_configured, production_reingest_limits
    from maude_reingest_watchdog import REINGEST_LOG, start_detached_two_pass

    ids = sorted({int(pid) for pid in paper_ids if pid is not None})
    if not ids:
        return {"status": "skipped", "reason": "no_new_papers", "paper_count": 0}

    prioritized_ids = _prioritize_open_access_paper_ids(ids)
    oa_count = _count_open_access_among(ids)

    limits = production_reingest_limits() if postgres_configured() else {}
    if batch_size >= 50 and limits:
        batch_size = int(limits.get("batch_size") or batch_size)
    if workers >= 4 and limits:
        workers = int(limits.get("workers") or workers)

    pid = start_detached_two_pass(
        batch_size=batch_size,
        workers=workers,
        log_path=log_path or REINGEST_LOG,
        paper_ids=prioritized_ids,
        slow_only=True,
    )
    logger.info(
        "Started post-harvest Maude PDF/full-text upgrade for %s papers "
        "(%s open_access prioritized, pid=%s)",
        len(prioritized_ids),
        oa_count,
        pid,
    )
    return {
        "status": "started",
        "pid": pid,
        "paper_count": len(prioritized_ids),
        "open_access_count": oa_count,
        "pass_mode": "two-pass-slow-only",
        "log_path": log_path or REINGEST_LOG,
    }


def _count_open_access_among(paper_ids: List[int]) -> int:
    """Counts open_access papers among the given ids."""
    if not paper_ids:
        return 0
    db = DatabaseManager()
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        ph = "%s" if db.is_postgres else "?"
        placeholders = ", ".join([ph] * len(paper_ids))
        cur.execute(
            f"SELECT COUNT(*) FROM papers WHERE id IN ({placeholders}) AND open_access = 1",
            tuple(paper_ids),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        if hasattr(row, "keys"):
            return int(list(row.values())[0])
        return int(row[0])
    except Exception as exc:
        logger.warning("Could not count open_access papers for upgrade: %s", exc)
        return 0
    finally:
        conn.close()


def _prioritize_open_access_paper_ids(paper_ids: List[int]) -> List[int]:
    """Returns paper ids with open_access first, preserving relative order within groups."""
    if not paper_ids:
        return []
    db = DatabaseManager()
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        ph = "%s" if db.is_postgres else "?"
        placeholders = ", ".join([ph] * len(paper_ids))
        cur.execute(
            f"SELECT id, open_access FROM papers WHERE id IN ({placeholders})",
            tuple(paper_ids),
        )
        rows = cur.fetchall()
        oa_ids: List[int] = []
        other_ids: List[int] = []
        seen = set()
        for row in rows:
            if hasattr(row, "keys"):
                pid = int(row["id"])
                oa = row["open_access"]
            else:
                pid = int(row[0])
                oa = row[1]
            seen.add(pid)
            if oa in (1, True, "1"):
                oa_ids.append(pid)
            else:
                other_ids.append(pid)
        missing = [pid for pid in paper_ids if pid not in seen]
        return oa_ids + other_ids + missing
    except Exception as exc:
        logger.warning("Could not prioritize open_access papers; using input order: %s", exc)
        return list(paper_ids)
    finally:
        conn.close()


def run_maude_reingest_now(
    batch_size: int = 50,
    refresh_maude_confidence: bool = True,
    maude_and_heuristic: bool = True,
    two_pass: bool = True,
    workers: int = 4,
    db: Optional[DatabaseManager] = None,
) -> Dict[str, Any]:
    """Immediately runs the Maude re-ingest job (does not require a scheduled record)."""
    payload = {
        "batch_size": batch_size,
        "refresh_maude_confidence": refresh_maude_confidence,
        "maude_and_heuristic": maude_and_heuristic,
        "two_pass": two_pass,
        "workers": workers,
    }
    logger.info("Starting immediate Maude re-ingest: %s", payload)
    return _execute_maude_reingest(payload)


def run_due_jobs(db: Optional[DatabaseManager] = None) -> List[Dict[str, Any]]:
    """Executes pending jobs whose scheduled time has passed."""
    db = db or DatabaseManager()
    jobs = load_jobs(db)
    completed: List[Dict[str, Any]] = []
    now_utc = datetime.now(tz=ZoneInfo("UTC"))

    for job in jobs:
        if job.get("status") != "pending":
            continue
        run_at_raw = job.get("run_at")
        if not run_at_raw:
            continue
        run_at = datetime.fromisoformat(run_at_raw)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=ZoneInfo(job.get("timezone") or DEFAULT_TIMEZONE))
        if run_at.astimezone(ZoneInfo("UTC")) > now_utc:
            continue

        job["status"] = "running"
        job["started_at"] = _now_iso()
        save_jobs(jobs, db)
        logger.info("Starting scheduled job %s (%s)", job.get("id"), job.get("type"))

        try:
            if job.get("type") == "maude_reingest":
                result = _execute_maude_reingest(job.get("payload") or {})
            else:
                raise ValueError(f"Unknown scheduled job type: {job.get('type')}")
            job["status"] = "completed"
            job["completed_at"] = _now_iso()
            job["result"] = result
            logger.info("Scheduled job %s completed: %s", job.get("id"), result)
        except Exception as exc:
            job["status"] = "failed"
            job["failed_at"] = _now_iso()
            job["error"] = str(exc)
            logger.exception("Scheduled job %s failed", job.get("id"))

        completed.append(job)
        save_jobs(jobs, db)

    return completed


def main() -> None:
    """CLI for scheduling and inspecting one-shot background jobs."""
    parser = argparse.ArgumentParser(description="Schedule one-shot background jobs.")
    sub = parser.add_subparsers(dest="command")

    schedule_parser = sub.add_parser(
        "schedule-maude-reingest",
        help="Schedule heuristic-1.0.0 → Maude re-ingest.",
    )
    schedule_parser.add_argument(
        "--at",
        default="23:00",
        help="Local run time (HH:MM or YYYY-MM-DD HH:MM). Default: 23:00.",
    )
    schedule_parser.add_argument(
        "--date",
        default=None,
        help="Local run date (YYYY-MM-DD). Default: today in scheduler timezone.",
    )
    schedule_parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"IANA timezone for --at/--date (default: {DEFAULT_TIMEZONE}).",
    )
    schedule_parser.add_argument("--batch-size", type=int, default=25)
    schedule_parser.add_argument(
        "--maude-and-heuristic",
        action="store_true",
        help="Target all maude-* and heuristic-* original research (not LLM-classified).",
    )
    schedule_parser.add_argument(
        "--no-confidence-refresh",
        action="store_true",
        help="Skip post-run maude-* confidence refresh.",
    )

    run_now_parser = sub.add_parser(
        "run-maude-reingest-now",
        help="Run Maude re-ingest immediately (maude-* + heuristic-* original research).",
    )
    run_now_parser.add_argument("--batch-size", type=int, default=50)
    run_now_parser.add_argument(
        "--heuristic-only",
        action="store_true",
        help="Only re-classify heuristic-1.0.0 papers (legacy scheduled scope).",
    )
    run_now_parser.add_argument(
        "--legacy-full",
        action="store_true",
        help="Use legacy single-pass PDF→fulltext→abstract (not two-pass).",
    )
    run_now_parser.add_argument("--workers", type=int, default=4, help="Slow-pass parallel workers.")
    run_now_parser.add_argument(
        "--no-confidence-refresh",
        action="store_true",
        help="Skip post-run maude-* confidence refresh.",
    )

    sub.add_parser("list", help="List scheduled jobs.")
    cancel_parser = sub.add_parser("cancel", help="Cancel a pending job by id.")
    cancel_parser.add_argument("job_id")

    args = parser.parse_args()
    if args.command == "schedule-maude-reingest":
        record = schedule_maude_reingest(
            at_time=args.at,
            run_date=args.date,
            timezone_name=args.timezone,
            batch_size=args.batch_size,
            refresh_maude_confidence=not args.no_confidence_refresh,
            maude_and_heuristic=args.maude_and_heuristic,
        )
        print(json.dumps(record, indent=2))
        return

    if args.command == "run-maude-reingest-now":
        result = run_maude_reingest_now(
            batch_size=args.batch_size,
            refresh_maude_confidence=not args.no_confidence_refresh,
            maude_and_heuristic=not args.heuristic_only,
            two_pass=not args.legacy_full,
            workers=args.workers,
        )
        print(json.dumps(result, indent=2))
        return

    if args.command == "list":
        print(json.dumps(load_jobs(), indent=2))
        return

    if args.command == "cancel":
        ok = cancel_job(args.job_id)
        print("cancelled" if ok else "not_found_or_not_pending")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
