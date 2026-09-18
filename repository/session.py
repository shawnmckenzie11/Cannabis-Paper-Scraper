"""Execute SQLAlchemy ``text()`` statements with qmark-style binds."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, CursorResult, Row

_QMARK = re.compile(r"\?")


def qmark_to_named(sql: str, params: Optional[Sequence[Any]] = None) -> Tuple[str, dict]:
    """Rewrite ``?`` placeholders to ``:p0``, ``:p1``, … for SQLAlchemy ``text()``.

    This is placeholder adaptation only — it does not rewrite JSON, FTS, or
    dialect SQL. Callers must emit backend-correct SQL before execute.
    """
    if not params:
        if "?" in sql:
            raise ValueError("SQL contains '?' but no bind parameters were provided")
        return sql, {}

    binds: dict = {}
    index = 0

    def _replace(_match: re.Match) -> str:
        nonlocal index
        if index >= len(params):
            raise ValueError(
                f"Not enough bind parameters for SQL placeholders ({len(params)} provided)"
            )
        key = f"p{index}"
        binds[key] = params[index]
        index += 1
        return f":{key}"

    rewritten = _QMARK.sub(_replace, sql)
    if index != len(params):
        raise ValueError(
            f"Placeholder count {index} does not match parameter count {len(params)}"
        )
    return rewritten, binds


def execute(
    conn: Connection,
    sql: str,
    params: Optional[Sequence[Any]] = None,
) -> CursorResult:
    """Execute ``sql`` with optional positional ``?`` binds on ``conn``."""
    rewritten, binds = qmark_to_named(sql, params)
    return conn.execute(text(rewritten), binds)


def row_mapping(row: Optional[Row]) -> Optional[dict]:
    """Convert a SQLAlchemy row to a plain dict, or None."""
    if row is None:
        return None
    return dict(row._mapping)


def fetchone(conn: Connection, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[dict]:
    """Return one row as a dict, or None."""
    result = execute(conn, sql, params)
    return row_mapping(result.fetchone())


def fetchall(conn: Connection, sql: str, params: Optional[Sequence[Any]] = None) -> List[dict]:
    """Return all rows as dicts."""
    result = execute(conn, sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


def scalar(conn: Connection, sql: str, params: Optional[Sequence[Any]] = None) -> Any:
    """Return the first column of the first row."""
    result = execute(conn, sql, params)
    return result.scalar()


def inserted_id(result: CursorResult, *, returning: bool) -> Optional[int]:
    """Read the new row id from RETURNING (Postgres) or lastrowid (SQLite)."""
    if returning:
        row = result.fetchone()
        if row is None:
            return None
        mapping = row._mapping
        if "id" in mapping:
            return int(mapping["id"])
        return int(list(mapping.values())[0])
    lastrowid = result.lastrowid
    return int(lastrowid) if lastrowid is not None else None
