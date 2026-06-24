"""Shared helpers for Postgres ↔ SQLite local reingest sync."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from paper_tab_flags import TAB_FLAG_FIELDS
from reingest_heuristic_papers import (
    UPDATE_COLUMNS,
    build_merged_update,
    paper_update_is_noop,
    parse_json_field,
)

DEFAULT_SQLITE_PATH = "cannabis_papers.db"
BASELINE_TABLE = "local_sync_baseline"
BASELINE_META_KEY = "local_sync_meta"

JSON_TEXT_FIELDS = frozenset(
    {
        "authors",
        "outcome_domain",
        "expert_locked_fields",
        "study_type",
        "exposure_method",
        "cannabis_type",
    }
)

TAB_COLUMNS: Tuple[str, ...] = tuple(TAB_FLAG_FIELDS.values())


def push_tracked_columns() -> List[str]:
    """Return paper columns compared for delta push and stored in the pull baseline."""
    return list(UPDATE_COLUMNS) + list(TAB_COLUMNS) + ["expert_locked_fields"]


def ensure_baseline_schema(conn: sqlite3.Connection) -> None:
    """Create baseline storage tables when missing."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {BASELINE_TABLE} (
            paper_id INTEGER PRIMARY KEY,
            baseline_json TEXT NOT NULL,
            pulled_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def pg_row_to_sqlite_value(column: str, value: Any) -> Any:
    """Normalize a Postgres row value for SQLite TEXT/INTEGER storage."""
    if value is None:
        return None
    if column in JSON_TEXT_FIELDS and isinstance(value, (list, dict)):
        return json.dumps(value)
    if column.startswith("tab_"):
        return int(value or 0)
    if column == "open_access":
        return 1 if value else 0
    return value


def sqlite_row_to_dict(row: sqlite3.Row, columns: Sequence[str]) -> Dict[str, Any]:
    """Convert a sqlite3.Row into a plain dict keyed by column name."""
    return {col: row[col] for col in columns}


def baseline_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Extract push-tracked fields from a paper row for baseline storage."""
    tracked = push_tracked_columns()
    return {col: row.get(col) for col in tracked}


def paper_row_to_extracted(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a SQLite paper row into classifier-shaped values for merged UPDATE."""
    extracted: Dict[str, Any] = {}
    for col in UPDATE_COLUMNS:
        extracted[col] = parse_json_field(row.get(col))
    return extracted


def save_baseline_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Dict[str, Any]],
    *,
    pulled_at: Optional[str] = None,
    replace_all: bool = False,
) -> int:
    """Persist baseline snapshots for the supplied paper rows."""
    ensure_baseline_schema(conn)
    timestamp = pulled_at or utc_now_iso()
    if replace_all:
        conn.execute(f"DELETE FROM {BASELINE_TABLE}")
    saved = 0
    for row in rows:
        paper_id = row.get("id")
        if paper_id is None:
            continue
        snapshot = baseline_from_row(row)
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {BASELINE_TABLE} (paper_id, baseline_json, pulled_at)
            VALUES (?, ?, ?)
            """,
            (int(paper_id), json.dumps(snapshot, sort_keys=True), timestamp),
        )
        saved += 1
    conn.commit()
    return saved


def load_baseline_row(conn: sqlite3.Connection, paper_id: int) -> Optional[Dict[str, Any]]:
    """Load a stored baseline snapshot for one paper id."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT baseline_json FROM {BASELINE_TABLE} WHERE paper_id = ?",
        (int(paper_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def intersect_table_columns(
    pg_columns: Set[str],
    sqlite_columns: Sequence[str],
) -> List[str]:
    """Return shared column names in SQLite table order."""
    pg_lower = {col.lower() for col in pg_columns}
    return [col for col in sqlite_columns if col.lower() in pg_lower]


def fetch_sqlite_paper_columns(conn: sqlite3.Connection, table: str = "papers") -> List[str]:
    """Return column names for a SQLite table via PRAGMA."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def fetch_postgres_paper_columns(pg_conn) -> Set[str]:
    """Return lowercase column names for the Postgres papers table."""
    import psycopg2.extras

    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'papers'
            """
        )
        return {str(row["column_name"]).lower() for row in cur.fetchall()}


def collect_delta_papers(
    conn: sqlite3.Connection,
    *,
    paper_ids: Optional[Set[int]] = None,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (baseline, current) pairs that differ on push-tracked fields."""
    ensure_baseline_schema(conn)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if paper_ids:
        placeholders = ",".join("?" for _ in paper_ids)
        cur.execute(
            f"""
            SELECT p.*
            FROM papers p
            INNER JOIN {BASELINE_TABLE} b ON b.paper_id = p.id
            WHERE p.id IN ({placeholders})
            ORDER BY p.id
            """,
            tuple(sorted(paper_ids)),
        )
    else:
        cur.execute(
            f"""
            SELECT p.*
            FROM papers p
            INNER JOIN {BASELINE_TABLE} b ON b.paper_id = p.id
            ORDER BY p.id
            """
        )

    deltas: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for row in cur.fetchall():
        current = dict(row)
        baseline = load_baseline_row(conn, int(current["id"]))
        if baseline is None:
            continue
        extracted = paper_row_to_extracted(current)
        if paper_update_is_noop(baseline, extracted):
            continue
        deltas.append((baseline, current))
    return deltas


def merged_update_sql(
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
) -> Tuple[str, List[Any]]:
    """Build a merged UPDATE statement using SQLite-style placeholders."""
    set_parts, params = build_merged_update(paper, extracted)
    if not set_parts:
        return "", []
    sql = f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?"
    return sql, params


def refresh_baseline_after_push(conn: sqlite3.Connection, paper_ids: Iterable[int]) -> None:
    """Update baseline snapshots to the current local row after a successful push."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    timestamp = utc_now_iso()
    for paper_id in paper_ids:
        cur.execute("SELECT * FROM papers WHERE id = ?", (int(paper_id),))
        row = cur.fetchone()
        if row is None:
            continue
        snapshot = baseline_from_row(dict(row))
        conn.execute(
            f"""
            INSERT OR REPLACE INTO {BASELINE_TABLE} (paper_id, baseline_json, pulled_at)
            VALUES (?, ?, ?)
            """,
            (int(paper_id), json.dumps(snapshot, sort_keys=True), timestamp),
        )
    conn.commit()
