#!/usr/bin/env python3
"""Audit and optionally repair indexed tab_* flags on papers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_manager import DatabaseManager
from paper_tab_flags import compute_tab_flags, TAB_FLAG_FIELDS


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Audit or repair tab_* membership columns.")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Recompute and write tab flags for every paper (or mismatched only with --mismatched-only).",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="With --repair, update every paper regardless of current tab_* values.",
    )
    parser.add_argument(
        "--mismatched-only",
        action="store_true",
        help="With --repair, update only papers whose tab_* columns differ from computed flags.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="Number of orphan/mismatch rows to print in audit mode (default: 20).",
    )
    parser.add_argument(
        "--since-harvested",
        default=None,
        help="Only audit/repair papers with date_harvested on or after this ISO date (YYYY-MM-DD).",
    )
    return parser


def row_flags(row: Dict[str, Any]) -> Dict[str, int]:
    """Return computed tab flags for a paper row."""
    return compute_tab_flags(
        publication_type=row.get("publication_type"),
        study_type=row.get("study_type"),
        ingestion_status=row.get("ingestion_status"),
    )


def stored_flags(row: Dict[str, Any]) -> Dict[str, int]:
    """Return stored tab_* values from a paper row."""
    return {
        column: int(row.get(column) or 0)
        for column in TAB_FLAG_FIELDS.values()
    }


def flags_match(row: Dict[str, Any]) -> bool:
    """Return True when stored tab flags match computed flags."""
    return stored_flags(row) == row_flags(row)


def is_orphaned(row: Dict[str, Any]) -> bool:
    """Return True when a paper has no tab membership at all."""
    return all(int(row.get(column) or 0) == 0 for column in TAB_FLAG_FIELDS.values())


def _harvest_since_clause(since_harvested: Optional[str]) -> tuple:
    """Return SQL fragment and params limiting to papers harvested on/after a date."""
    if not since_harvested:
        return "", []
    start = str(since_harvested).strip()
    if "T" not in start:
        start = start + "T00:00:00"
    return " AND date_harvested >= %s", [start]


def audit(db: DatabaseManager, sample: int, since_harvested: Optional[str] = None) -> Dict[str, Any]:
    """Summarize orphan and mismatch counts."""
    conn = db.get_connection()
    cur = conn.cursor()
    extra_sql, extra_params = _harvest_since_clause(since_harvested)
    cur.execute(f"SELECT COUNT(*) as total FROM papers WHERE 1=1{extra_sql}", extra_params)
    total = int(cur.fetchone()["total"])

    tab_cols = ", ".join(TAB_FLAG_FIELDS.values())
    cur.execute(
        f"""
        SELECT id, publication_type, study_type, ingestion_status, {tab_cols}
        FROM papers
        WHERE 1=1{extra_sql}
        """,
        extra_params,
    )
    orphans: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    for raw in cur.fetchall():
        row = dict(raw)
        if is_orphaned(row):
            orphans.append(row)
        elif not flags_match(row):
            mismatches.append(row)

    conn.close()
    print(f"papers_total={total}")
    print(f"orphaned_all_tabs_zero={len(orphans)}")
    print(f"tab_flag_mismatch={len(mismatches)}")

    for label, rows in (("orphan", orphans), ("mismatch", mismatches)):
        if not rows:
            continue
        print(f"\n{label} sample:")
        for row in rows[:sample]:
            computed = row_flags(row)
            print(
                f"  id={row['id']} pub={row.get('publication_type')!r} "
                f"study={str(row.get('study_type'))[:60]!r} "
                f"ingest={row.get('ingestion_status')!r} stored={stored_flags(row)} computed={computed}"
            )

    return {
        "total": total,
        "orphaned": len(orphans),
        "mismatched": len(mismatches),
    }


def repair(
    db: DatabaseManager,
    *,
    force_all: bool,
    mismatched_only: bool,
    since_harvested: Optional[str] = None,
) -> Dict[str, int]:
    """Recompute tab flags for papers and persist updates."""
    conn = db.get_connection()
    cur = conn.cursor()
    tab_columns = list(TAB_FLAG_FIELDS.values())
    set_clause = ", ".join(f"{column} = %s" for column in tab_columns)
    extra_sql, extra_params = _harvest_since_clause(since_harvested)

    cur.execute(
        f"""
        SELECT id, publication_type, study_type, ingestion_status, {", ".join(tab_columns)}
        FROM papers
        WHERE 1=1{extra_sql}
        ORDER BY id
        """,
        extra_params,
    )
    rows = [dict(row) for row in cur.fetchall()]
    updated = 0
    batch: List[List[Any]] = []

    for row in rows:
        if mismatched_only and not force_all:
            if flags_match(row):
                continue
        elif not force_all:
            if not is_orphaned(row) and flags_match(row):
                continue

        computed = row_flags(row)
        if force_all or mismatched_only or is_orphaned(row) or not flags_match(row):
            if stored_flags(row) == computed and not force_all:
                continue
            params = [int(computed.get(column, 0)) for column in tab_columns]
            params.append(int(row["id"]))
            batch.append(params)
            if len(batch) >= 500:
                cur.executemany(f"UPDATE papers SET {set_clause} WHERE id = %s", batch)
                conn.commit()
                updated += len(batch)
                batch = []

    if batch:
        cur.executemany(f"UPDATE papers SET {set_clause} WHERE id = %s", batch)
        conn.commit()
        updated += len(batch)

    db._mark_tab_flags_ready(True)
    db._refresh_tab_flags_ready_cache()
    try:
        db.set_metadata("dashboard_tab_counts_json", "")
        db.set_metadata("dashboard_tab_counts_cached_at", "0")
    except Exception:
        pass

    conn.close()
    return {"scanned": len(rows), "updated": updated}


def main() -> None:
    """CLI entry point."""
    args = build_parser().parse_args()
    db = DatabaseManager()
    if not db._tab_flag_columns_exist():
        raise SystemExit("Tab membership columns are missing on papers table.")

    if args.repair:
        summary = repair(
            db,
            force_all=args.force_all,
            mismatched_only=args.mismatched_only and not args.force_all,
            since_harvested=args.since_harvested,
        )
        print(f"repair_scanned={summary['scanned']} repair_updated={summary['updated']}")
        audit(db, sample=args.sample, since_harvested=args.since_harvested)
        return

    audit(db, sample=args.sample, since_harvested=args.since_harvested)


if __name__ == "__main__":
    main()
