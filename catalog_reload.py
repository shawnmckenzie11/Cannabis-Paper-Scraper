"""Replace the live SQLite catalog without rebooting gunicorn."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class CatalogReloadError(ValueError):
    """Raised when a staging SQLite file cannot replace the live catalog."""


def checkpoint_sqlite(path: str | Path) -> None:
    """Flush WAL into the main file so the catalog is a single uploadable DB."""
    db_path = str(path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
    finally:
        conn.close()


def validate_catalog_sqlite(path: str | Path) -> None:
    """Require a non-empty SQLite file that contains the papers table."""
    db_path = Path(path)
    if not db_path.is_file() or db_path.stat().st_size <= 0:
        raise CatalogReloadError(f"catalog is missing or empty: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='papers'"
        ).fetchone()
        if not row:
            raise CatalogReloadError(f"catalog has no papers table: {db_path}")
    except sqlite3.Error as exc:
        raise CatalogReloadError(f"catalog is not readable SQLite: {db_path}: {exc}") from exc
    finally:
        conn.close()


def replace_sqlite_catalog(live_path: str | Path, staging_path: str | Path) -> str:
    """Atomically replace ``live_path`` with ``staging_path`` after a WAL checkpoint.

    Returns the resolved live path. Leftover ``-wal`` / ``-shm`` sidecars for the
    old live file are removed so gunicorn opens the new catalog cleanly.
    """
    live = Path(live_path).resolve()
    staging = Path(staging_path).resolve()
    if live == staging:
        raise CatalogReloadError("staging path must differ from the live catalog path")
    checkpoint_sqlite(staging)
    validate_catalog_sqlite(staging)
    live.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, live)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(live) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    return str(live)


def download_from_r2_env(dest: str | Path) -> None:
    """Download the catalog from R2 or the Hub dataset into dest."""
    import catalog_store

    catalog_store.download_catalog(str(dest))
