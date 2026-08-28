#!/usr/bin/env python3
"""Run one incremental daily PubMed harvest against a local SQLite catalog.

GitHub Actions downloads cannabis_papers.db from the Hub dataset (or R2),
runs harvest with DATABASE_URL unset, uploads the DB, then the Space reloads.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("ci_daily_harvest")


def force_sqlite_env(sqlite_path: str) -> None:
    """Ensure DatabaseManager uses SQLite at sqlite_path, not Fly Postgres."""
    os.environ.pop("DATABASE_URL", None)
    os.environ["DATABASE_PATH"] = sqlite_path


def main(argv: list[str] | None = None) -> int:
    """CLI for GitHub Actions and operator catch-up runs."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("DATABASE_PATH", str(ROOT / "cannabis_papers.db")),
        help="Local SQLite catalog path.",
    )
    parser.add_argument("--skip-store", action="store_true", help="Do not download/upload the remote catalog.")
    parser.add_argument("--download-only", action="store_true", help="Fetch the catalog then exit.")
    parser.add_argument("--upload-only", action="store_true", help="Upload local DB then exit.")
    parser.add_argument("--force", action="store_true", help="Harvest even if already ran today.")
    parser.add_argument("--no-purge", action="store_true", help="Skip purge_unrelated after ingest.")
    args = parser.parse_args(argv)
    sqlite_path = os.path.abspath(args.sqlite_path)
    force_sqlite_env(sqlite_path)
    os.environ["CHEAP_OPS"] = os.getenv("CHEAP_OPS") or "1"

    import catalog_reload
    import catalog_store
    import daily_harvest

    use_store = not args.skip_store
    if use_store and catalog_store.store_backend() == "none":
        logger.error("Configure CATALOG_DATASET_ID/HF_TOKEN or R2_BUCKET/R2_ENDPOINT (or pass --skip-store)")
        return 2

    if use_store and not args.upload_only:
        catalog_store.download_catalog(sqlite_path)
        if args.download_only:
            return 0

    if args.upload_only:
        catalog_reload.checkpoint_sqlite(sqlite_path)
        if use_store:
            catalog_store.upload_catalog(sqlite_path)
        return 0

    summary = daily_harvest.run_scheduled_cycle(
        force_harvest=args.force,
        harvest_only=args.no_purge,
        trigger="external",
    )
    harvest = summary.get("harvest") or {}
    logger.info("harvest summary=%s", summary)
    catalog_reload.checkpoint_sqlite(sqlite_path)
    if use_store:
        catalog_store.upload_catalog(sqlite_path)

    if summary.get("status") == "error" or harvest.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
