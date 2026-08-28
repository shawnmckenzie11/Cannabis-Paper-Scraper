"""Idempotent daily PubMed harvest used by the in-process scheduler, CLI, and GitHub Actions.

CHEAP_OPS=1 skips bulk Maude re-ingest jobs and the nightly watchdog so the Fly
web machine can autostop between harvests.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("daily_harvest")

DAILY_HARVEST_QUERY = "cannabis OR cannabinoid OR marijuana"
DEFAULT_CATCHUP_DAYS = 3
LOCK_METADATA_KEY = "daily_harvest_lock"
LOCK_STALE_SECONDS = 45 * 60
CHEAP_OPS_TRUTHY = frozenset({"1", "true", "yes", "on"})

_cycle_lock = threading.Lock()


def cheap_ops_enabled(environ: Optional[dict] = None) -> bool:
    """Return True when cheap-ops mode should skip bulk Maude re-ingest work."""
    env = environ if environ is not None else os.environ
    return str(env.get("CHEAP_OPS", "0")).strip().lower() in CHEAP_OPS_TRUTHY


def resolve_harvest_mindate(
    last_run_date: Optional[str],
    *,
    today: Optional[date] = None,
    catchup_days: int = DEFAULT_CATCHUP_DAYS,
) -> str:
    """Pick the PubMed Entrez-date start for an incremental daily harvest.

    A recorded last-run date is always reused (catch-up, not a full historical
    refetch). Only a missing/never watermark falls back to ``catchup_days``.
    """
    if last_run_date and str(last_run_date).strip() not in {"Never", "never"}:
        return str(last_run_date).strip()[:10]
    day = today or date.today()
    return (day - timedelta(days=catchup_days)).isoformat()


def _lock_age_seconds(raw: Optional[str]) -> Optional[float]:
    """Return age in seconds of an ISO lock timestamp, or None if unparsable."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        locked_at = datetime.fromisoformat(text)
    except ValueError:
        return None
    if locked_at.tzinfo is not None:
        locked_at = locked_at.replace(tzinfo=None)
    return max(0.0, (datetime.now() - locked_at).total_seconds())


def _acquire_harvest_lock(db) -> bool:
    """Set a short metadata lock; return False if another harvest still holds it."""
    age = _lock_age_seconds(db.get_metadata(LOCK_METADATA_KEY))
    if age is not None and age < LOCK_STALE_SECONDS:
        return False
    db.set_metadata(LOCK_METADATA_KEY, datetime.now().isoformat())
    return True


def _release_harvest_lock(db) -> None:
    """Clear the harvest metadata lock."""
    try:
        db.set_metadata(LOCK_METADATA_KEY, "")
    except Exception:
        logger.warning("Failed to clear daily harvest lock", exc_info=True)


def run_daily_harvest_if_due(
    db=None,
    *,
    force: bool = False,
    classify: Optional[bool] = None,
    skip_purge: Optional[bool] = None,
) -> Dict[str, Any]:
    """Run the once-per-day PubMed harvest when it has not yet succeeded today.

    Steps match the former in-process scheduler body: optional pre-harvest
    manual-edit cycle, incremental ingest, tab-flag sync, scoped post-harvest
    Maude upgrade, and purge_unrelated.

    Args:
        db: Optional DatabaseManager; constructed if omitted.
        force: When True, harvest even if last_daily_harvest_date is today.
        classify: Override AUTO_HARVEST_CLASSIFY (True = Claude LLM).
        skip_purge: When True, skip the full-catalog purge. When None, CHEAP_OPS
            skips purge because GitHub Actions harvests into a Hub copy that
            must not be wiped if abstracts are still being backfilled.

    Returns:
        Status dict with ``status`` of skipped, ran, locked, or error.
    """
    import harvest
    from db_manager import DatabaseManager

    db = db or DatabaseManager()
    today_str = date.today().isoformat()
    last_run_date = db.get_metadata("last_daily_harvest_date")
    result: Dict[str, Any] = {
        "status": "skipped",
        "reason": "already_ran_today",
        "today": today_str,
        "last_run_date": last_run_date,
        "query": DAILY_HARVEST_QUERY,
    }
    if not force and last_run_date == today_str:
        return result

    if not _acquire_harvest_lock(db):
        result["status"] = "skipped"
        result["reason"] = "locked"
        return result

    try:
        last_run_date = db.get_metadata("last_daily_harvest_date")
        result["last_run_date"] = last_run_date
        if not force and last_run_date == today_str:
            result["reason"] = "already_ran_today"
            return result

        if classify is None:
            classify = os.getenv("AUTO_HARVEST_CLASSIFY", "false").lower() == "true"
        since_date = resolve_harvest_mindate(last_run_date)

        logger.info(
            "Daily harvest starting query=%r today=%s last_run=%s mindate=%s classify=%s",
            DAILY_HARVEST_QUERY,
            today_str,
            last_run_date,
            since_date,
            classify,
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
                    "Pre-harvest: %s unprocessed expert edit(s); running manual edit cycle",
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
                logger.info("Pre-harvest: no unprocessed expert edits; skipping manual edit cycle")
        except Exception as edit_err:
            logger.error("Pre-harvest manual edit cycle failed: %s", edit_err)
            result["pre_harvest_edit_error"] = str(edit_err)
            if os.getenv("MANUAL_EDIT_BLOCK_HARVEST", "0") == "1":
                raise

        success_count, skipped_count, filter_skipped, ingested_ids = harvest.run_harvest_pipeline(
            query=DAILY_HARVEST_QUERY,
            max_results=0,
            update=True,
            classify=bool(classify),
            mindate=since_date,
        )

        for paper_id in ingested_ids or []:
            try:
                db.sync_tab_flags_for_paper(int(paper_id))
            except Exception as flag_err:
                logger.error("Tab flag sync failed for paper %s: %s", paper_id, flag_err)

        if ingested_ids and not cheap_ops_enabled():
            try:
                import scheduled_jobs

                upgrade = scheduled_jobs.run_post_harvest_maude_upgrade(ingested_ids)
                logger.info("Post-harvest Maude upgrade: %s", upgrade)
                result["maude_upgrade"] = upgrade
            except Exception as upgrade_err:
                logger.error("Post-harvest Maude upgrade failed: %s", upgrade_err)
                result["maude_upgrade_error"] = str(upgrade_err)
        elif ingested_ids:
            logger.info("CHEAP_OPS: skipping post-harvest Maude PDF upgrade")
            result["maude_upgrade"] = {"status": "skipped", "reason": "cheap_ops"}

        if skip_purge is None:
            skip_purge = cheap_ops_enabled()
        if skip_purge:
            logger.info("Skipping full-catalog purge_unrelated (CHEAP_OPS or skip_purge)")
            result["purge_ok"] = True
            result["purge_skipped"] = True
        else:
            logger.info("Running purge_unrelated after daily harvest")
            try:
                import purge_unrelated

                purge_unrelated.run_purger(dry_run=False)
                result["purge_ok"] = True
            except Exception as purge_err:
                logger.error("Purge process failed: %s", purge_err)
                result["purge_ok"] = False
                result["purge_error"] = str(purge_err)

        date_str = datetime.now().isoformat()
        status_msg = (
            f"Success! Harvest complete. Ingested {success_count} papers "
            f"(skipped {skipped_count} pre-existing, filtered {filter_skipped} unrelated) "
            f"at {datetime.now().strftime('%H:%M:%S')}."
        )
        logger.info(status_msg)
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
    except Exception as exc:
        err_msg = f"Error at {datetime.now().strftime('%H:%M:%S')}: {exc}"
        logger.exception("Daily harvest failed")
        try:
            db.set_metadata("last_daily_harvest_status", err_msg)
        except Exception:
            pass
        result["status"] = "error"
        result["reason"] = "exception"
        result["error"] = str(exc)
        result["message"] = err_msg
        return result
    finally:
        _release_harvest_lock(db)


def run_scheduled_cycle(
    db=None,
    *,
    force_harvest: bool = False,
    harvest_only: bool = False,
    skip_purge: Optional[bool] = None,
    trigger: str = "external",
) -> Dict[str, Any]:
    """Run one harvest cycle plus (unless CHEAP_OPS) due jobs and the watchdog.

    Args:
        db: Optional DatabaseManager.
        force_harvest: Pass through to ``run_daily_harvest_if_due``.
        harvest_only: Skip due jobs, watchdog, and notification digests.
        skip_purge: Pass through to ``run_daily_harvest_if_due``.
        trigger: Value stored in ``scheduler_trigger`` metadata (external vs inprocess).

    Returns:
        Summary dict with ``ok`` and nested harvest / job results.
    """
    from db_manager import DatabaseManager

    if not _cycle_lock.acquire(blocking=False):
        logger.info("Scheduled cycle already running; skipping overlapping trigger.")
        return {"ok": False, "status": "already_running"}

    db = db or DatabaseManager()
    cheap = cheap_ops_enabled()
    summary: Dict[str, Any] = {
        "ok": True,
        "status": "completed",
        "cheap_ops": cheap,
        "harvest": None,
        "due_jobs": [],
        "watchdog": None,
        "notification_digests": None,
    }
    try:
        db.set_metadata("scheduler_active", "true")
        db.set_metadata("scheduler_trigger", trigger)
        if not db.get_metadata("last_daily_harvest_status"):
            db.set_metadata("last_daily_harvest_status", "Never run")
        db.set_metadata("scheduler_heartbeat_at", datetime.now().isoformat())

        summary["harvest"] = run_daily_harvest_if_due(
            db, force=force_harvest, skip_purge=skip_purge
        )
        harvest_status = (summary["harvest"] or {}).get("status")
        if harvest_status == "error":
            summary["ok"] = False
            summary["status"] = "error"

        if harvest_only:
            summary["watchdog"] = {"action": "skipped", "reason": "harvest_only"}
            return summary

        if cheap:
            logger.info("CHEAP_OPS: skipping due Maude jobs and reingest watchdog")
            summary["due_jobs"] = []
            summary["watchdog"] = {"action": "skipped", "reason": "cheap_ops"}
        else:
            import maude_reingest_watchdog
            import scheduled_jobs

            summary["due_jobs"] = scheduled_jobs.run_due_jobs(db)
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
        logger.exception("Scheduled cycle failed")
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


def main(argv: Optional[list] = None) -> int:
    """CLI entrypoint: print one harvest-cycle JSON summary to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description="Run the idempotent daily PubMed harvest (Maude classify by default).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Harvest even if last_daily_harvest_date is already today.",
    )
    parser.add_argument(
        "--harvest-only",
        action="store_true",
        help="Skip due jobs, watchdog, and notification digests.",
    )
    args = parser.parse_args(argv)
    summary = run_scheduled_cycle(force_harvest=args.force, harvest_only=args.harvest_only)
    print(json.dumps(summary, default=str))
    harvest = summary.get("harvest") or {}
    if summary.get("status") in {"error", "already_running"} or harvest.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
