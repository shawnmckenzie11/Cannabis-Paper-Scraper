"""SQLAlchemy engine factory for Postgres (prod) and SQLite (dev/cache)."""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")

_ENGINE_CACHE: Dict[Tuple[str, str], Engine] = {}


def using_postgres(database_url: Optional[str] = None) -> bool:
    """Return True when a Postgres URL is the configured production backend."""
    url = database_url if database_url is not None else os.getenv("DATABASE_URL")
    return bool(url) and (url.startswith("postgres://") or url.startswith("postgresql://"))


def sqlalchemy_url(db_path: Optional[str] = None, database_url: Optional[str] = None) -> str:
    """Return the SQLAlchemy URL for the live backend.

    Production: ``DATABASE_URL`` (``postgres://`` is rewritten to ``postgresql+psycopg2://``).
    Local/dev: ``sqlite:///<DATABASE_PATH or db_path>``.
    """
    url = database_url if database_url is not None else os.getenv("DATABASE_URL")
    if using_postgres(url):
        assert url is not None
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://") :]
        return url
    path = db_path or os.getenv("DATABASE_PATH", DATABASE_FILE)
    return f"sqlite:///{path}"


def _cache_key(db_path: Optional[str] = None, database_url: Optional[str] = None) -> Tuple[str, str]:
    """Stable cache key for one backend target."""
    url = database_url if database_url is not None else os.getenv("DATABASE_URL")
    if using_postgres(url):
        return ("postgresql", url or "")
    return ("sqlite", db_path or os.getenv("DATABASE_PATH", DATABASE_FILE))


def get_engine(db_path: Optional[str] = None, database_url: Optional[str] = None) -> Engine:
    """Return a cached SQLAlchemy engine for the live backend.

    Postgres uses a small checked-out pool. SQLite uses ``NullPool`` so each
    call opens and closes a connection (same lifetime as the old sqlite3 path,
    and safe for tests that delete the file between cases).
    """
    key = _cache_key(db_path=db_path, database_url=database_url)
    engine = _ENGINE_CACHE.get(key)
    if engine is not None:
        return engine

    url = sqlalchemy_url(db_path=db_path, database_url=database_url)
    if key[0] == "postgresql":
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=int(os.getenv("POSTGRES_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("POSTGRES_MAX_OVERFLOW", "2")),
            pool_timeout=int(os.getenv("POSTGRES_POOL_TIMEOUT", "10")),
            future=True,
        )
    else:
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30.0},
            poolclass=NullPool,
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.close()

    _ENGINE_CACHE[key] = engine
    return engine


def reset_engine_cache() -> None:
    """Dispose cached engines. Used by tests that swap ``DATABASE_URL``."""
    for engine in _ENGINE_CACHE.values():
        engine.dispose()
    _ENGINE_CACHE.clear()
