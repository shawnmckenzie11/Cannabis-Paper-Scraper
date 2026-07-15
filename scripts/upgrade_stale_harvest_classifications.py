#!/usr/bin/env python3
"""Re-stamp daily-harvest papers when rules_config advances (e.g. 2.6.x → 2.7.0)."""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import classifier
import heuristics_engine
from db_manager import DatabaseManager
from rules_version import compare_semver

logger = logging.getLogger(__name__)

_MAUDE_VERSION_RE = re.compile(
    r"^maude(?:-(?:pdf|ft|fulltext))?-(\d+\.\d+\.\d+)",
    re.IGNORECASE,
)


def normalize_rules_version_from_label(classifier_version: str) -> Optional[str]:
    """Extract semver from a Maude classifier_version label, ignoring golden row tags."""
    match = _MAUDE_VERSION_RE.match(str(classifier_version or "").strip())
    if not match:
        return None
    return match.group(1)


def is_stale_maude_harvest_label(classifier_version: str, current_rules: str) -> bool:
    """Return True when a Maude harvest label is behind the active rules version."""
    label_version = normalize_rules_version_from_label(classifier_version)
    if not label_version:
        return False
    return compare_semver(label_version, current_rules) < 0


def find_stale_harvest_paper_ids(
    db: DatabaseManager,
    *,
    current_rules: str,
    since_date: str,
    limit: Optional[int] = None,
) -> List[int]:
    """Return paper ids harvested since ``since_date`` with Maude labels older than ``current_rules``."""
    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        is_postgres = getattr(db, "is_postgres", False)
        if is_postgres:
            cursor.execute(
                """
                SELECT id, classifier_version
                FROM papers
                WHERE date_harvested IS NOT NULL
                  AND date_harvested::text >= %s
                  AND classifier_version LIKE 'maude%%'
                ORDER BY date_harvested DESC, id DESC
                """,
                (since_date,),
            )
        else:
            cursor.execute(
                """
                SELECT id, classifier_version
                FROM papers
                WHERE date_harvested IS NOT NULL
                  AND date_harvested >= ?
                  AND classifier_version LIKE 'maude%'
                ORDER BY date_harvested DESC, id DESC
                """,
                (since_date,),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()

    stale_ids: List[int] = []
    for row in rows:
        paper_id = int(row[0] if isinstance(row, tuple) else row["id"])
        version = row[1] if isinstance(row, tuple) else row["classifier_version"]
        if is_stale_maude_harvest_label(str(version or ""), current_rules):
            stale_ids.append(paper_id)
            if limit is not None and len(stale_ids) >= limit:
                break
    return stale_ids


def upgrade_stale_harvest_classifications(
    *,
    since_date: str = "2026-06-01",
    limit: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """
    Re-classify harvested papers whose Maude label semver is behind rules_config.

    Returns a summary dict with stale_count and reingest exit code.
    """
    os.environ.pop("GOLDEN_ROW_INDEX", None)
    heuristics_engine.reload_rules_config()
    current_rules = classifier.get_rules_version()
    db = DatabaseManager()
    stale_ids = find_stale_harvest_paper_ids(
        db,
        current_rules=current_rules,
        since_date=since_date,
        limit=limit,
    )
    summary = {
        "current_rules": current_rules,
        "since_date": since_date,
        "stale_count": len(stale_ids),
        "sample_ids": stale_ids[:10],
        "dry_run": dry_run,
    }
    if not stale_ids or dry_run:
        logger.info("Stale harvest upgrade: %s", summary)
        return summary

    chunk_size = 100
    exit_code = 0
    upgraded = 0
    for offset in range(0, len(stale_ids), chunk_size):
        chunk = stale_ids[offset : offset + chunk_size]
        cmd = [
            sys.executable,
            str(ROOT / "reingest_heuristic_papers.py"),
            "--pass",
            "fast",
            "--no-skip-current",
            "--paper-ids",
            ",".join(str(pid) for pid in chunk),
            "--workers",
            "2",
            "--batch-size",
            str(len(chunk)),
        ]
        logger.info("Re-stamping %s stale harvest papers (rules v%s)...", len(chunk), current_rules)
        proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
        if proc.returncode != 0:
            exit_code = proc.returncode
            summary["error"] = f"reingest failed on chunk offset {offset}"
            break
        upgraded += len(chunk)

    summary["upgraded_count"] = upgraded
    summary["exit_code"] = exit_code
    logger.info("Stale harvest upgrade complete: %s", summary)
    return summary


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Re-stamp harvested papers on outdated Maude semver labels.",
    )
    parser.add_argument(
        "--since-date",
        default="2026-06-01",
        help="Only papers with date_harvested on/after this ISO date (default: 2026-06-01).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max papers to upgrade.")
    parser.add_argument("--dry-run", action="store_true", help="Report stale ids only.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = upgrade_stale_harvest_classifications(
        since_date=args.since_date,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    if summary.get("exit_code"):
        raise SystemExit(int(summary["exit_code"]))


if __name__ == "__main__":
    main()
