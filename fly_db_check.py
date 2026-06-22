#!/usr/bin/env python3
"""Quick Fly.io production database status check for calibration runs."""

import os

from calibration_build import maude_build_info
from db_manager import DatabaseManager


def _scalar(row) -> int:
    """Extracts a single COUNT(*) value from sqlite or postgres row objects."""
    if row is None:
        return 0
    if hasattr(row, "keys"):
        return int(list(row.values())[0])
    return int(row[0])


def main() -> None:
    """Prints production DB path and calibration counts."""
    print("DATABASE_PATH:", os.getenv("DATABASE_PATH"))
    print("DATABASE_URL set:", bool(os.getenv("DATABASE_URL")))
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM papers")
    total = _scalar(cursor.fetchone())
    cursor.execute(
        "SELECT COUNT(*) FROM papers WHERE classifier_version LIKE ?",
        ("llm-node1-calibration-%",),
    )
    node1 = _scalar(cursor.fetchone())
    cursor.execute(
        "SELECT COUNT(*) FROM papers WHERE classifier_version LIKE ?",
        ("llm-pdf-reclassify-%",),
    )
    llm_pdf = _scalar(cursor.fetchone())
    cursor.execute(
        "SELECT COUNT(*) FROM papers WHERE classifier_version LIKE ?",
        ("llm-reclassify-%",),
    )
    llm_abstract = _scalar(cursor.fetchone())
    conn.close()
    print("total_papers:", total)
    print("node1_calibrated:", node1)
    print("llm_pdf_reclassify:", llm_pdf)
    print("llm_reclassify:", llm_abstract)
    for key, value in maude_build_info().items():
        print(f"{key}:", value)


if __name__ == "__main__":
    main()
