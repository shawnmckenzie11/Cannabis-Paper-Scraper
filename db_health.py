"""Lightweight Postgres health probes for bulk jobs and the web app."""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_TRANSIENT_MARKERS = (
    "server closed the connection",
    "connection unexpectedly",
    "could not connect",
    "connection refused",
    "timeout",
    "too many clients",
    "remaining connection slots",
)


def is_transient_db_error(exc: BaseException) -> bool:
    """Return True when an exception looks like a recoverable connection failure."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def postgres_configured() -> bool:
    """Return True when DATABASE_URL points at Postgres."""
    url = os.getenv("DATABASE_URL") or ""
    return url.startswith("postgres://") or url.startswith("postgresql://")


def postgres_is_healthy(
    *,
    connect_timeout: int = 5,
    statement_timeout_ms: int = 5000,
) -> Tuple[bool, str]:
    """Run a minimal SELECT 1 against Postgres.

    Returns:
        Tuple of (healthy, detail message).
    """
    if not postgres_configured():
        return True, "sqlite_or_unconfigured"

    try:
        import psycopg2
    except ImportError:
        return False, "psycopg2_not_installed"

    url = os.getenv("DATABASE_URL")
    conn = None
    try:
        conn = psycopg2.connect(url, connect_timeout=connect_timeout)
        conn.autocommit = True
        with conn.cursor() as cur:
            if statement_timeout_ms > 0:
                cur.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, "ok"
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return False, str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def production_reingest_limits() -> dict:
    """Return conservative bulk-write defaults when backed by Postgres."""
    if postgres_configured():
        return {
            "workers": int(os.getenv("REINGEST_WORKERS", "1")),
            "workers_fast": int(os.getenv("REINGEST_WORKERS_FAST", "2")),
            "batch_size": int(os.getenv("REINGEST_BATCH_SIZE", "25")),
            "batch_pause_seconds": float(os.getenv("REINGEST_BATCH_PAUSE_SECONDS", "0.15")),
        }
    return {
        "workers": int(os.getenv("REINGEST_WORKERS", "4")),
        "workers_fast": int(os.getenv("REINGEST_WORKERS_FAST", "4")),
        "batch_size": int(os.getenv("REINGEST_BATCH_SIZE", "50")),
        "batch_pause_seconds": float(os.getenv("REINGEST_BATCH_PAUSE_SECONDS", "0")),
    }
