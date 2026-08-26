"""One-shot background jobs persisted in DB metadata and executed by the app scheduler."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from db_manager import DatabaseManager

logger = logging.getLogger("scheduler.jobs")
JOB_METADATA_KEY = "scheduled_jobs"
DEFAULT_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "America/Toronto")
DAILY_HARVEST_QUERY = "cannabis OR cannabinoid OR marijuana"
SCHEDULER_TOKEN_ENV = "SCHEDULER_RUN_TOKEN"
SCHEDULER_TOKEN_HEADER = "X-Scheduler-Token"

# One in-process cycle at a time (HTTP handler and overlapping cron pings).
_cycle_lock = threading.Lock()


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


def scheduler_token_is_authorized(
    provided: Optional[str],
    expected: Optional[str] = None,
) -> bool:
    """Return True when the caller token matches SCHEDULER_RUN_TOKEN.

    Fails closed when the expected secret is missing or empty so the
    HTTP trigger cannot be invoked publicly by accident.
    """
    expected_token = (expected if expected is not None else os.getenv(SCHEDULER_TOKEN_ENV) or "").strip()
    provided_token = (provided or "").strip()
    if provided_token.lower().startswith("bearer "):
        provided_token = provided_token[7:].strip()
    if not expected_token:
        return False
    try:
        return hmac.compare_digest(provided_token, expected_token)
    except Exception:
        return False


def _maybe_run_daily_harvest(db: DatabaseManager) -> Dict[str, Any]:
    """Run the once-per-day PubMed harvest branch when it has not yet succeeded today.

    Harvest logic is unchanged from the former in-process scheduler loop:
    pre-harvest manual-edit cycle, incremental ingest, tab-flag sync,
    post-harvest Maude upgrade, and purge_unrelated.
    """
    import harvest

    classify = os.getenv("AUTO_HARVEST_CLASSIFY", "false").lower() == "true"
    today_str = date.today().isoformat()
    last_run_date = db.get_metadata("last_daily_harvest_date")
    result: Dict[str, Any] = {
        "status": "skipped",
        "reason": "already_ran_today",
        "today": today_str,
        "last_run_date": last_run_date,
    }
    if last_run_date == today_str:
        return result

    logger.info(
        "Daily scheduler: starting automated harvest for query %r (today: %s, last run: %s)",
        DAILY_HARVEST_QUERY,
        today_str,
        last_run_date,
    )
    db.set_metadata(
        "last_daily_harvest_status",
        f"Running automated harvest since {datetime.now().strftime('%H:%M:%S')}...",
    )

    try:
        import manual_edit_cycle

        if manual_edit_cycle.should_run_pre_harvest_cycle(db):
            since_ts = manual_edit_cycle.pre_harvest_processing_since(db)
            pending = db.count_expert_edits_since(since_ts, expert_drawer_only=True)
            logger.info(
                "Pre-harvest: %s unprocessed expert edit(s) since last harvest/cycle; running manual edit cycle",
                pending,
            )
            edit_result = manual_edit_cycle.run_manual_edit_cycle(
                db,
                since=since_ts,
                dry_run=False,
                sqlite_path=os.getenv("SQLITE_PATH", "/data/cannabis_papers.db"),
            )
            logger.info("Pre-harvest manual edit cycle: %s", edit_result)
            result["pre_harvest_edit_cycle"] = edit_result
        else:
            logger.info(
                "Pre-harvest: no unprocessed expert edits since last daily harvest; skipping manual edit cycle"
            )
    except Exception as edit_err:
        logger.error("Pre-harvest manual edit cycle failed: %s", edit_err)
        result["pre_harvest_edit_error"] = str(edit_err)
        if os.getenv("MANUAL_EDIT_BLOCK_HARVEST", "0") == "1":
            raise

    since_date = last_run_date
    if not since_date or since_date == "Never":
        since_date = (date.today() - timedelta(days=3)).isoformat()
    success_count, skipped_count, filter_skipped, ingested_ids = harvest.run_harvest_pipeline(
        query=DAILY_HARVEST_QUERY,
        max_results=0,
        update=True,
        classify=classify,
        mindate=since_date,
    )

    for paper_id in ingested_ids or []:
        try:
            db.sync_tab_flags_for_paper(int(paper_id))
        except Exception as flag_err:
            logger.error(
                "Daily scheduler: tab flag sync failed for paper %s: %s",
                paper_id,
                flag_err,
            )

    if ingested_ids:
        try:
            upgrade = run_post_harvest_maude_upgrade(ingested_ids)
            logger.info("Daily scheduler: post-harvest Maude upgrade: %s", upgrade)
            result["maude_upgrade"] = upgrade
        except Exception as upgrade_err:
            logger.error("Daily scheduler: post-harvest Maude upgrade failed: %s", upgrade_err)
            result["maude_upgrade_error"] = str(upgrade_err)

    logger.info("Daily scheduler: Running purge_unrelated to clean up acronym-collision outliers...")
    try:
        import purge_unrelated

        purge_unrelated.run_purger(dry_run=False)
        logger.info("Daily scheduler: Cleanse completed successfully.")
        result["purge_ok"] = True
    except Exception as purge_err:
        logger.error("Daily scheduler: Purge process failed: %s", purge_err)
        result["purge_ok"] = False
        result["purge_error"] = str(purge_err)

    date_str = datetime.now().isoformat()
    status_msg = (
        f"Success! Harvest complete. Ingested {success_count} papers "
        f"(skipped {skipped_count} pre-existing, filtered {filter_skipped} unrelated) "
        f"at {datetime.now().strftime('%H:%M:%S')}."
    )
    logger.info("Daily scheduler status: %s", status_msg)
    db.set_metadata("last_daily_harvest_date", today_str)
    db.set_metadata("last_daily_harvest_timestamp", date_str)
    db.set_metadata("last_daily_harvest_status", status_msg)
    try:
        db.set_metadata("dashboard_tab_counts_json", "")
        db.set_metadata("dashboard_tab_counts_cached_at", "0")
    except Exception:
        pass

    result.update(
        {
            "status": "ran",
            "reason": None,
            "success_count": success_count,
            "skipped_count": skipped_count,
            "filter_skipped": filter_skipped,
            "ingested_ids": list(ingested_ids or []),
            "mindate": since_date,
            "message": status_msg,
        }
    )
    return result


def run_scheduled_cycle(db: Optional[DatabaseManager] = None) -> Dict[str, Any]:
    """Run one harvest + due-jobs + watchdog + notification-digest cycle and return.

    This is the body of the former in-process ``while True`` poll loop, callable
    from CLI (``python scheduled_jobs.py --run-cycle``) or
    ``POST /api/scheduler/run-cycle`` so the Fly web machine can autostop
    between triggers.
    """
    import maude_reingest_watchdog

    if not _cycle_lock.acquire(blocking=False):
        logger.info("Scheduled cycle already running; skipping overlapping trigger.")
        return {"ok": False, "status": "already_running"}

    db = db or DatabaseManager()
    summary: Dict[str, Any] = {
        "ok": True,
        "status": "completed",
        "harvest": None,
        "due_jobs": [],
        "watchdog": None,
        "notification_digests": None,
    }
    try:
        try:
            repaired = db.sync_orphan_tab_flags_since("2026-07-17")
            logger.info("Tab-flag repair updated %s recently harvested papers", repaired)
            summary["tab_flags_repaired"] = repaired
        except Exception as repair_err:
            logger.error("Tab-flag repair failed: %s", repair_err)
            summary["tab_flags_repair_error"] = str(repair_err)

        db.set_metadata("scheduler_active", "true")
        db.set_metadata("scheduler_trigger", "external")
        if not db.get_metadata("last_daily_harvest_status"):
            db.set_metadata("last_daily_harvest_status", "Never run")
        db.set_metadata("scheduler_heartbeat_at", datetime.now().isoformat())

        summary["harvest"] = _maybe_run_daily_harvest(db)
        summary["due_jobs"] = run_due_jobs(db)
        summary["watchdog"] = maude_reingest_watchdog.run_watchdog(db)
        try:
            import user_notifications

            digest_result = user_notifications.run_due_notification_digests(db)
            summary["notification_digests"] = digest_result
            if digest_result.get("sent") or digest_result.get("errors"):
                logger.info("Notification digests: %s", digest_result)
        except Exception as notif_err:
            logger.error("Notification digest runner failed: %s", notif_err)
            summary["notification_digests"] = {"error": str(notif_err)}
        return summary
    except Exception as exc:
        err_msg = f"Background scheduler failed: {exc}"
        logger.error(err_msg)
        try:
            db.set_metadata(
                "last_daily_harvest_status",
                f"Error at {datetime.now().strftime('%H:%M:%S')}: {exc}",
            )
        except Exception:
            pass
        summary["ok"] = False
        summary["status"] = "error"
        summary["error"] = str(exc)
        return summary
    finally:
        _cycle_lock.release()


def main() -> None:
    """CLI for scheduling jobs and running one external scheduler cycle."""
    parser = argparse.ArgumentParser(description="Schedule one-shot background jobs.")
    parser.add_argument(
        "--run-cycle",
        action="store_true",
        help="Run one harvest + due-jobs + watchdog + digest cycle and exit.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "run-cycle",
        help="Run one harvest + due-jobs + watchdog + digest cycle and exit.",
    )

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
    if args.run_cycle or args.command == "run-cycle":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        result = run_scheduled_cycle(DatabaseManager())
        print(json.dumps(result, indent=2, default=str))
        raise SystemExit(0 if result.get("ok") or result.get("status") == "already_running" else 1)

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
