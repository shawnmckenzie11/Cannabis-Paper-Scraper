"""Sync production Postgres feedback_audit + affected papers into local SQLite."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from db_manager import DatabaseManager
from manual_edit_cycle import dedupe_expert_edits, pull_affected_papers
from reingest_heuristic_papers import parse_json_field, serialize

logger = logging.getLogger(__name__)

METADATA_LAST_SYNC = "last_postgres_feedback_sync_at"
DEFAULT_SQLITE_PATH = "cannabis_papers.db"


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _postgres_db() -> DatabaseManager:
    """Return a DatabaseManager connected to production Postgres."""
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set for Postgres feedback_audit sync.")
    db = DatabaseManager()
    if not db.is_postgres:
        raise RuntimeError("DATABASE_URL must point at Postgres for feedback_audit sync.")
    return db


def _local_db(sqlite_path: str) -> DatabaseManager:
    """Return a DatabaseManager for local SQLite."""
    return DatabaseManager(db_path=sqlite_path)


def _local_max_audit_timestamp(conn: sqlite3.Connection) -> str:
    """Return the newest feedback_audit timestamp in local SQLite."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM feedback_audit")
    row = cur.fetchone()
    if not row or row[0] is None:
        return "1970-01-01T00:00:00"
    return str(row[0])


def _audit_row_exists(conn: sqlite3.Connection, row: Dict[str, Any]) -> bool:
    """Return True when an equivalent audit row already exists locally."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM feedback_audit
        WHERE paper_id = ? AND field_name = ? AND timestamp = ?
        LIMIT 1
        """,
        (int(row["paper_id"]), str(row["field_name"]), str(row.get("timestamp") or "")),
    )
    return cur.fetchone() is not None


def _apply_audit_correction_to_paper(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    field_name: str,
    new_value: Any,
) -> bool:
    """Apply one feedback_audit correction to a local papers row."""
    cur = conn.cursor()
    cur.execute("SELECT expert_locked_fields FROM papers WHERE id = ?", (paper_id,))
    row = cur.fetchone()
    if row is None:
        return False

    locked = parse_json_field(row[0] if not hasattr(row, "keys") else row["expert_locked_fields"]) or []
    if not isinstance(locked, list):
        locked = []
    locked_set = set(str(item) for item in locked)
    locked_set.add(str(field_name))
    locked_json = json.dumps(sorted(locked_set))

    if field_name in {
        "study_type",
        "exposure_method",
        "cannabis_type",
        "outcome_domain",
    }:
        stored = serialize(field_name, parse_json_field(new_value))
    else:
        stored = new_value if new_value is None or isinstance(new_value, str) else json.dumps(new_value)

    cur.execute(
        f"UPDATE papers SET {field_name} = ?, expert_locked_fields = ? WHERE id = ?",
        (stored, locked_json, paper_id),
    )
    return cur.rowcount > 0


def sync_feedback_audit_from_postgres(
    sqlite_path: str = DEFAULT_SQLITE_PATH,
    *,
    since: Optional[str] = None,
    pull_papers: bool = True,
) -> Dict[str, Any]:
    """
    Preflight sync: copy new Postgres feedback_audit rows into local SQLite and refresh papers.

    1. Read expert/auto corrections from production Postgres since the last sync watermark.
    2. Insert missing rows into local feedback_audit (FTS triggers fire on SQLite).
    3. Apply corrected field values to local papers and lock those fields.
    4. Pull full paper rows from Postgres for all affected paper ids (refreshes baselines).
    """
    sqlite_path = str(sqlite_path)
    if not Path(sqlite_path).is_file():
        return {"skipped": True, "reason": f"sqlite missing: {sqlite_path}"}

    try:
        pg_db = _postgres_db()
    except RuntimeError as exc:
        return {"skipped": True, "reason": str(exc)}

    local_db = _local_db(sqlite_path)
    local_conn = local_db.get_connection()
    since_ts = since or local_db.get_metadata(METADATA_LAST_SYNC)
    if not since_ts:
        since_ts = _local_max_audit_timestamp(local_conn)

    rows = pg_db.fetch_feedback_audit_since(since_ts, expert_drawer_only=False)
    rows = dedupe_expert_edits(rows)

    inserted = 0
    papers_updated = 0
    affected_ids: Set[int] = set()
    latest_ts = since_ts

    try:
        for row in rows:
            paper_id = int(row["paper_id"])
            affected_ids.add(paper_id)
            ts = str(row.get("timestamp") or "")
            if ts and ts > latest_ts:
                latest_ts = ts
            if _audit_row_exists(local_conn, row):
                continue
            local_db.insert_feedback_audit(
                paper_id=paper_id,
                field_name=str(row["field_name"]),
                old_value=row.get("old_value"),
                new_value=row.get("new_value"),
                title=row.get("title"),
                abstract=row.get("abstract"),
                timestamp=ts or utc_now_iso(),
                confidence_before_review=row.get("confidence_before_review"),
                classifier_version=row.get("classifier_version"),
            )
            inserted += 1
            field_name = str(row["field_name"])
            if field_name.startswith("maude:"):
                continue
            if _apply_audit_correction_to_paper(
                local_conn,
                paper_id=paper_id,
                field_name=field_name,
                new_value=row.get("new_value"),
            ):
                papers_updated += 1
        local_conn.commit()
    finally:
        local_conn.close()

    pull_summary: Dict[str, Any] = {"skipped": True}
    if pull_papers and affected_ids:
        pull_summary = pull_affected_papers(sqlite_path, sorted(affected_ids))

    if latest_ts and latest_ts != since_ts:
        local_db.set_metadata(METADATA_LAST_SYNC, latest_ts)

    summary = {
        "skipped": False,
        "since": since_ts,
        "audit_rows_fetched": len(rows),
        "audit_rows_inserted": inserted,
        "papers_updated": papers_updated,
        "paper_ids": sorted(affected_ids),
        "pull_summary": pull_summary,
        "synced_at": utc_now_iso(),
    }
    logger.info("feedback_audit sync: %s", summary)
    return summary


def run_preflight_or_exit(
    sqlite_path: str = DEFAULT_SQLITE_PATH,
    *,
    required: bool = False,
) -> Dict[str, Any]:
    """
    Run feedback_audit preflight; exit the process when required sync fails.

    Call before Loop A/B/manual patch runs when DATABASE_URL is available.
    """
    summary = sync_feedback_audit_from_postgres(sqlite_path)
    if required and summary.get("skipped") and summary.get("reason"):
        raise SystemExit(f"feedback_audit preflight required but skipped: {summary['reason']}")
    if summary.get("pull_summary", {}).get("error"):
        message = summary["pull_summary"]["error"]
        if required:
            raise SystemExit(f"feedback_audit paper pull failed: {message}")
        logger.warning("feedback_audit paper pull failed: %s", message)
    return summary
