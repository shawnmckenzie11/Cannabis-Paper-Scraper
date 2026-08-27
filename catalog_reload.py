"""Replace the live SQLite catalog with a harvested copy without rebooting gunicorn."""

from __future__ import annotations

import os
import sqlite3
import subprocess
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
    """Download the catalog object from R2 using the AWS CLI and process env."""
    bucket = os.getenv("R2_BUCKET", "").strip()
    endpoint = os.getenv("R2_ENDPOINT", "").strip()
    object_key = os.getenv("R2_OBJECT", "cannabis_papers.db").strip() or "cannabis_papers.db"
    if not bucket or not endpoint:
        raise CatalogReloadError("R2_BUCKET and R2_ENDPOINT are required to pull the catalog")
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aws",
        "s3",
        "cp",
        f"s3://{bucket}/{object_key}",
        str(dest_path),
        "--endpoint-url",
        endpoint,
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise CatalogReloadError(f"R2 download failed: {err}")


MIN_CATALOG_BYTES = 50_000_000


def catalog_needs_seed(path: str | Path, min_bytes: int = MIN_CATALOG_BYTES) -> bool:
    """Return True when the SQLite catalog is missing or too small to be the corpus."""
    db_path = Path(path)
    return (not db_path.is_file()) or db_path.stat().st_size < min_bytes


def ensure_local_catalog(db_path: str | Path | None = None) -> str:
    """Return a usable SQLite catalog path, downloading from R2 when needed.

    Production hosting (Render, a VPS, Docker) must not use Fly ``DATABASE_URL``.
    The canonical corpus lives in Cloudflare R2; this pulls it onto disk at boot
    when ``DATABASE_PATH`` is empty or is the tiny repo placeholder.
    """
    path = Path(db_path or os.getenv("DATABASE_PATH") or "cannabis_papers.db")
    min_bytes = int(os.getenv("MIN_CATALOG_BYTES") or MIN_CATALOG_BYTES)
    if not catalog_needs_seed(path, min_bytes=min_bytes):
        validate_catalog_sqlite(path)
        return str(path.resolve())

    seed = Path("cannabis_papers.db")
    if seed.is_file() and seed.resolve() != path.resolve() and seed.stat().st_size >= min_bytes:
        import shutil

        path.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(str(path) + ".seed")
        shutil.copy2(seed, staging)
        replace_sqlite_catalog(path, staging)
        return str(path.resolve())

    staging = Path(str(path) + ".download")
    download_from_r2_env(staging)
    replace_sqlite_catalog(path, staging)
    return str(path.resolve())
