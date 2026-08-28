#!/usr/bin/env python3
"""Copy the live Fly catalog into a local SQLite file via the public search API.

The empty repo cannabis_papers.db is not the corpus. This walks
https://cannabis-paper-scraper.fly.dev/api/search until every paper is stored,
then optionally uploads the file to the Hub dataset (or R2).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("bootstrap_catalog")

SKIP_KEYS = {"newly_harvested", "id", "rank"}
JSON_FIELDS = {
    "authors",
    "outcome_domain",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "expert_locked_fields",
}
UM_MAP = {"thc_um": "thc_uM", "cbd_um": "cbd_uM"}


def normalize_live_paper(paper: Dict[str, Any]) -> Dict[str, Any]:
    """Map a public /api/search row onto SQLite papers-table columns.

    Search JSON uses lowercase ``thc_um`` / ``cbd_um``; the schema uses ``thc_uM``
    / ``cbd_uM``. Fly row ids are dropped so SQLite assigns new ids. List fields
    are stored as JSON strings.
    """
    row: Dict[str, Any] = {}
    for key, value in paper.items():
        if key in SKIP_KEYS:
            continue
        row[UM_MAP.get(key, key)] = value
    for field in JSON_FIELDS:
        if field not in row:
            continue
        if not isinstance(row[field], str):
            row[field] = json.dumps(row[field] if row[field] is not None else [])
    if "open_access" in row:
        row["open_access"] = 1 if row["open_access"] else 0
    if not row.get("date_harvested"):
        row["date_harvested"] = datetime.now().isoformat()
    title = (row.get("title") or "").strip()
    if not title:
        raise ValueError("live paper is missing a title")
    row["title"] = title
    return row


def _paper_columns(conn: sqlite3.Connection) -> List[str]:
    """Return papers-table column names excluding the integer primary key."""
    names = [str(item[1]) for item in conn.execute("PRAGMA table_info(papers)").fetchall()]
    return [name for name in names if name != "id"]


def bulk_insert_papers(conn: sqlite3.Connection, rows: Sequence[Dict[str, Any]]) -> int:
    """Insert already-normalized rows with one executemany.

    Returns:
        Number of rows inserted.
    """
    if not rows:
        return 0
    columns = _paper_columns(conn)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO papers ({', '.join(columns)}) VALUES ({placeholders})"
    payload = [tuple(row.get(column) for column in columns) for row in rows]
    conn.executemany(sql, payload)
    return len(payload)


def fill_missing_abstracts(conn: sqlite3.Connection, batch_size: int = 100) -> int:
    """Fetch PubMed abstracts for rows that the public search API omitted.

    Returns:
        Number of rows updated with an abstract.
    """
    import harvest

    api_key = (os.getenv("NCBI_API_KEY") or "").strip()
    if api_key:
        from Bio import Entrez

        Entrez.api_key = api_key
    rows = conn.execute(
        """
        SELECT pmid FROM papers
        WHERE pmid IS NOT NULL AND TRIM(pmid) != ''
          AND (abstract IS NULL OR TRIM(abstract) = '')
        """
    ).fetchall()
    pmids = [str(row[0]) for row in rows]
    logger.info("Filling abstracts for %s papers from PubMed", len(pmids))
    updated = 0
    for start in range(0, len(pmids), batch_size):
        batch = pmids[start : start + batch_size]
        papers = harvest.fetch_pubmed_details(batch)
        for paper in papers:
            pmid = paper.get("pmid")
            abstract = (paper.get("abstract") or "").strip()
            if not pmid or not abstract:
                continue
            journal = paper.get("journal") or ""
            authors = paper.get("authors")
            if isinstance(authors, list):
                authors = json.dumps(authors)
            conn.execute(
                """
                UPDATE papers
                SET abstract = ?,
                    journal = CASE WHEN journal IS NULL OR TRIM(journal) = '' THEN ? ELSE journal END,
                    authors = CASE WHEN authors IS NULL OR TRIM(authors) = '' THEN ? ELSE authors END
                WHERE pmid = ?
                """,
                (abstract, journal, authors or "", pmid),
            )
            updated += 1
        conn.commit()
        logger.info(
            "Filled abstracts through %s / %s (%s updated)",
            min(start + batch_size, len(pmids)),
            len(pmids),
            updated,
        )
    return updated


def copy_scheduler_metadata(db, status: Dict[str, Any]) -> None:
    """Copy harvest watermarks so the next daily run is incremental, not historical."""
    mapping = {
        "last_daily_harvest_date": status.get("last_run_date"),
        "last_daily_harvest_timestamp": status.get("last_run_timestamp"),
        "last_daily_harvest_status": status.get("last_run_status"),
        "scheduler_trigger": status.get("trigger") or "external",
        "scheduler_heartbeat_at": status.get("heartbeat_at"),
    }
    for key, value in mapping.items():
        if value is None or value == "":
            continue
        db.set_metadata(key, str(value))


def iter_search_pages(
    session: requests.Session,
    base_url: str,
    page_size: int,
) -> Iterable[List[Dict[str, Any]]]:
    """Yield successive /api/search pages until the live catalog is exhausted."""
    page = 1
    total = None
    fetched = 0
    while True:
        resp = session.get(
            f"{base_url.rstrip('/')}/api/search",
            params={"page": page, "limit": page_size, "skip_count": 0},
            timeout=90,
        )
        resp.raise_for_status()
        payload = resp.json()
        if total is None:
            total = int(payload.get("total_count") or 0)
            logger.info("Live catalog reports %s papers", total)
        papers = payload.get("papers") or []
        if not papers:
            break
        fetched += len(papers)
        logger.info("Fetched page %s (%s / %s)", page, fetched, total)
        yield papers
        if total and fetched >= total:
            break
        if len(papers) < page_size:
            break
        page += 1


def main(argv: list[str] | None = None) -> int:
    """Paginate the live search API and insert rows into SQLite."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://cannabis-paper-scraper.fly.dev")
    parser.add_argument("--sqlite-path", default=str(ROOT / "cannabis_papers.db"))
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the built SQLite file to the configured catalog store.",
    )
    parser.add_argument(
        "--skip-abstracts",
        action="store_true",
        help="Do not backfill abstracts from PubMed (list metadata only).",
    )
    args = parser.parse_args(argv)

    os.environ.pop("DATABASE_URL", None)
    sqlite_path = os.path.abspath(args.sqlite_path)
    os.environ["DATABASE_PATH"] = sqlite_path

    from db_manager import DatabaseManager

    db = DatabaseManager(db_path=sqlite_path)
    db.init_db()

    session = requests.Session()
    inserted = 0
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM papers")
        conn.commit()
        for papers in iter_search_pages(session, args.base_url, args.page_size):
            rows = [normalize_live_paper(paper) for paper in papers]
            inserted += bulk_insert_papers(conn, rows)
            conn.commit()
            logger.info("Committed %s papers so far", inserted)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info("Inserted %s papers; backfilling tab flags", inserted)
    conn = db.get_connection()
    try:
        db._backfill_tab_flags(conn)
        conn.commit()
    finally:
        conn.close()

    if not args.skip_abstracts:
        conn = db.get_connection()
        try:
            filled = fill_missing_abstracts(conn)
            logger.info("Filled %s abstracts from PubMed", filled)
        finally:
            conn.close()

    try:
        status_resp = session.get(f"{args.base_url.rstrip('/')}/api/scheduler/status", timeout=30)
        status_resp.raise_for_status()
        copy_scheduler_metadata(db, status_resp.json())
    except Exception as exc:
        logger.warning("Could not copy scheduler metadata: %s", exc)

    logger.info("Bootstrap complete: %s papers in %s", inserted, sqlite_path)

    if args.upload:
        import catalog_reload
        import catalog_store

        catalog_reload.checkpoint_sqlite(sqlite_path)
        catalog_store.upload_catalog(sqlite_path)
        logger.info("Uploaded catalog to backend=%s", catalog_store.store_backend())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
