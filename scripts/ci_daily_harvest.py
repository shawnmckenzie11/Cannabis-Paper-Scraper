#!/usr/bin/env python3
"""Run one incremental daily PubMed harvest against a local SQLite catalog.

GitHub Actions downloads cannabis_papers.db from Cloudflare R2, runs this
script (never Postgres), then uploads the DB back. Do not point this at
Fly DATABASE_URL.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily_harvest_config import DAILY_HARVEST_QUERY, resolve_harvest_mindate

logger = logging.getLogger("ci_daily_harvest")


def force_sqlite_env(sqlite_path: str) -> None:
    """Ensure DatabaseManager uses SQLite at sqlite_path, not Fly Postgres."""
    os.environ.pop("DATABASE_URL", None)
    os.environ["DATABASE_PATH"] = sqlite_path


def r2_uri(bucket: str, object_key: str) -> str:
    """Build an s3:// URI for the catalog object."""
    return f"s3://{bucket}/{object_key}"


def aws_s3_cp(src: str, dest: str, endpoint_url: str) -> None:
    """Copy one object with the AWS CLI against an S3-compatible endpoint."""
    cmd = [
        "aws",
        "s3",
        "cp",
        src,
        dest,
        "--endpoint-url",
        endpoint_url,
    ]
    logger.info("Running %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def download_catalog(sqlite_path: str, *, bucket: str, object_key: str, endpoint_url: str) -> None:
    """Fetch the catalog SQLite file from R2 onto disk."""
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    aws_s3_cp(r2_uri(bucket, object_key), sqlite_path, endpoint_url)


def upload_catalog(sqlite_path: str, *, bucket: str, object_key: str, endpoint_url: str) -> None:
    """Publish the harvested SQLite file to R2."""
    aws_s3_cp(sqlite_path, r2_uri(bucket, object_key), endpoint_url)


def run_daily_harvest(
    sqlite_path: str,
    *,
    query: str = DAILY_HARVEST_QUERY,
    classify: bool = False,
    run_purge: bool = True,
) -> dict:
    """Harvest since the last successful run date and sync dashboard tab flags.

    Returns:
        dict: ingested counts and the mindate used for PubMed.
    """
    force_sqlite_env(sqlite_path)
    import harvest
    from catalog_reload import checkpoint_sqlite
    from db_manager import DatabaseManager

    db = DatabaseManager(db_path=sqlite_path)
    last_run = db.get_metadata("last_daily_harvest_date")
    mindate = resolve_harvest_mindate(last_run)
    logger.info("Daily harvest mindate=%s last_run=%s db=%s", mindate, last_run, sqlite_path)

    success_count, skipped_count, filter_skipped, ingested_ids = harvest.run_harvest_pipeline(
        query=query,
        max_results=0,
        update=True,
        classify=classify,
        mindate=mindate,
    )

    for paper_id in ingested_ids or []:
        try:
            db.sync_tab_flags_for_paper(int(paper_id))
        except Exception as flag_err:
            logger.error("tab flag sync failed for paper %s: %s", paper_id, flag_err)

    try:
        repaired = db.sync_orphan_tab_flags_since(mindate)
        logger.info("orphan tab flags repaired=%s", repaired)
    except Exception as repair_err:
        logger.error("orphan tab flag repair failed: %s", repair_err)
        repaired = 0

    purge_ok = None
    if run_purge:
        try:
            import purge_unrelated

            purge_unrelated.run_purger(dry_run=False)
            purge_ok = True
        except Exception as purge_err:
            logger.error("purge_unrelated failed: %s", purge_err)
            purge_ok = False

    today_str = datetime.now().date().isoformat()
    status_msg = (
        f"Success! Harvest complete. Ingested {success_count} papers "
        f"(skipped {skipped_count} pre-existing, filtered {filter_skipped} unrelated) "
        f"at {datetime.now().strftime('%H:%M:%S')}."
    )
    db.set_metadata("last_daily_harvest_date", today_str)
    db.set_metadata("last_daily_harvest_timestamp", datetime.now().isoformat())
    db.set_metadata("last_daily_harvest_status", status_msg)
    try:
        db.set_metadata("dashboard_tab_counts_json", "")
        db.set_metadata("dashboard_tab_counts_cached_at", "0")
    except Exception:
        pass

    checkpoint_sqlite(sqlite_path)
    return {
        "success_count": success_count,
        "skipped_count": skipped_count,
        "filter_skipped": filter_skipped,
        "ingested_ids": list(ingested_ids or []),
        "mindate": mindate,
        "orphans_repaired": repaired,
        "purge_ok": purge_ok,
        "status": status_msg,
    }


def main() -> int:
    """CLI for GitHub Actions and operator catch-up runs."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=os.getenv("DATABASE_PATH", str(ROOT / "cannabis_papers.db")),
        help="Local SQLite catalog path.",
    )
    parser.add_argument("--skip-r2", action="store_true", help="Do not download/upload R2.")
    parser.add_argument("--download-only", action="store_true", help="Fetch R2 then exit.")
    parser.add_argument("--upload-only", action="store_true", help="Upload local DB to R2 then exit.")
    parser.add_argument("--no-purge", action="store_true", help="Skip purge_unrelated after ingest.")
    parser.add_argument(
        "--classify",
        action="store_true",
        help="Optional Claude classification pass (off by default).",
    )
    args = parser.parse_args()
    sqlite_path = os.path.abspath(args.sqlite_path)
    force_sqlite_env(sqlite_path)

    bucket = os.getenv("R2_BUCKET", "")
    object_key = os.getenv("R2_OBJECT", "cannabis_papers.db")
    endpoint = os.getenv("R2_ENDPOINT", "")
    use_r2 = not args.skip_r2
    if use_r2 and (not bucket or not endpoint):
        logger.error("R2_BUCKET and R2_ENDPOINT are required unless --skip-r2")
        return 2

    if use_r2 and not args.upload_only:
        download_catalog(sqlite_path, bucket=bucket, object_key=object_key, endpoint_url=endpoint)
        if args.download_only:
            return 0

    if args.upload_only:
        if use_r2:
            from catalog_reload import checkpoint_sqlite

            checkpoint_sqlite(sqlite_path)
            upload_catalog(sqlite_path, bucket=bucket, object_key=object_key, endpoint_url=endpoint)
        return 0

    classify = bool(args.classify) or os.getenv("AUTO_HARVEST_CLASSIFY", "false").lower() == "true"
    result = run_daily_harvest(
        sqlite_path,
        classify=classify,
        run_purge=not args.no_purge,
    )
    logger.info("harvest result=%s", result)

    if use_r2:
        upload_catalog(sqlite_path, bucket=bucket, object_key=object_key, endpoint_url=endpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
