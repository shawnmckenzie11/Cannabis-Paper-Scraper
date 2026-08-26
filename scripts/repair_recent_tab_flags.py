#!/usr/bin/env python3
"""Repair dashboard tab_* flags for recently harvested papers that have none."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_manager import DatabaseManager


def main() -> int:
    """Recompute tab flags for orphaned rows harvested on/after a date."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-harvested",
        default="2026-07-17",
        help="Inclusive date_harvested start (YYYY-MM-DD).",
    )
    args = parser.parse_args()
    db = DatabaseManager()
    if not db._tab_flag_columns_exist():
        print("Tab membership columns are missing; nothing to repair.")
        return 0
    updated = db.sync_orphan_tab_flags_since(args.since_harvested)
    print(f"repaired_orphans={updated} since={args.since_harvested}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
