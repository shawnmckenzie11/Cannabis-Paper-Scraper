"""Thin SQLAlchemy Core data-access layer.

Postgres (``DATABASE_URL``) is the production source of truth. Local SQLite
(``DATABASE_PATH`` / ``cannabis_papers.db``) is for development and cache only.
Callers import repository helpers and never branch on the live backend.

Schema changes belong in Alembic, not runtime ``_ensure_*`` patches.
"""

from repository.classify import ClassifyRepository, get_classify_repository
from repository.engine import get_engine, reset_engine_cache, sqlalchemy_url, using_postgres
from repository.papers import PapersRepository, get_papers_repository

__all__ = [
    "ClassifyRepository",
    "PapersRepository",
    "get_classify_repository",
    "get_engine",
    "get_papers_repository",
    "reset_engine_cache",
    "sqlalchemy_url",
    "using_postgres",
]
