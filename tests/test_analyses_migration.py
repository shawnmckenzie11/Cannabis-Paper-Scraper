"""init_analyses_table must migrate in place and never drop saved analyses."""
import sqlite3

import pytest

from db_manager import DatabaseManager


@pytest.fixture(autouse=True)
def _sqlite_only(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(DatabaseManager, "is_postgres", property(lambda self: False))


def _mgr(path):
    m = DatabaseManager.__new__(DatabaseManager)
    m.db_path = str(path)
    return m


def _cols(path):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(analyses)")}
    finally:
        con.close()


def _count(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    finally:
        con.close()


def test_missing_column_added_rows_preserved(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, "
                "name TEXT, filter_settings TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    con.executemany("INSERT INTO analyses (user_id, name, filter_settings) VALUES (?, ?, ?)",
                    [(9001, "fake-a", "{}"), (9002, "fake-b", "{}")])
    con.commit(); con.close()

    m = _mgr(db)
    m.init_analyses_table()

    assert {"paper_count", "chart_data"} <= _cols(db)
    assert _count(db) == 2
    con = sqlite3.connect(db)
    rows = con.execute("SELECT name, paper_count FROM analyses ORDER BY id").fetchall()
    con.close()
    assert rows == [("fake-a", 0), ("fake-b", 0)]
    # Idempotent second boot, and the new columns are usable.
    m.init_analyses_table()
    assert m.create_analysis("fake-c", "{}", 3, "[]", None)
    assert _count(db) == 3


def test_unaddable_column_refuses_without_drop(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    # Missing created_at: SQLite cannot ADD COLUMN with DEFAULT CURRENT_TIMESTAMP.
    con.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, "
                "filter_settings TEXT, paper_count INTEGER, chart_data TEXT)")
    con.execute("INSERT INTO analyses (name) VALUES ('fake-keep')")
    con.commit(); con.close()

    with pytest.raises(RuntimeError, match=r"analyses.*created_at"):
        _mgr(db).init_analyses_table()
    assert _count(db) == 1
    assert "created_at" not in _cols(db)
