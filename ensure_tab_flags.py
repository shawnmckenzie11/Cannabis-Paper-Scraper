"""Ensure indexed tab membership columns are populated before serving traffic."""
from db_manager import DatabaseManager


def main() -> None:
    """Backfill tab flags when columns exist but membership is incomplete."""
    db = DatabaseManager()
    if not db._tab_flag_columns_exist():
        raise SystemExit("Tab membership columns are missing.")

    db._refresh_tab_flags_ready_cache()
    if db._tab_flags_are_ready():
        print("Indexed tab membership columns already backfilled.")
        return

    conn = db.get_connection()
    try:
        db._backfill_tab_flags(conn)
    finally:
        conn.close()

    db._refresh_tab_flags_ready_cache()
    if db._tab_flags_are_ready():
        print("Indexed tab membership columns backfilled successfully.")
        return
    raise SystemExit("Tab membership backfill did not complete.")


if __name__ == "__main__":
    main()
