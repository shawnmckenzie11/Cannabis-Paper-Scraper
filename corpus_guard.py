#!/usr/bin/env python3
"""Fail-closed corpus check for golden tests and RL calibration.

Postgres is the sole production source of truth. Local SQLite is a
dev/cache copy only. Golden and RL jobs must not treat an empty image
default (``/app/cannabis_papers.db``) or an uninitialized local file as
the corpus.

This is not an always-on web/prod blocker. Call it from golden/RL
entrypoints, or run the CLI when ``GOLDEN_RUN=1``. Set ``CORPUS_GUARD=0``
only as an emergency override.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple
from urllib.parse import urlparse

REQUIRED_TABLES: Tuple[str, ...] = ("papers",)

# Image default on the Fly app VM — never the golden/RL corpus.
KNOWN_WRONG_SQLITE_PATHS = frozenset({
    "/app/cannabis_papers.db",
})

DEFAULT_MIN_PAPERS = {
    "golden": 1,
    "rl": 100,
}

_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


class CorpusGuardError(RuntimeError):
    """Raised when the configured database is empty, missing, or the wrong file."""


@dataclass(frozen=True)
class CorpusSnapshot:
    """Inspected database state used by the corpus guard."""

    backend: str
    path_or_url: str
    exists: bool
    papers_table: bool
    paper_count: int
    missing_tables: Tuple[str, ...]
    detail: str


def guard_enabled() -> bool:
    """Return False when CORPUS_GUARD is an explicit opt-out."""
    return os.getenv("CORPUS_GUARD", "1").strip().lower() not in _DISABLED_VALUES


def postgres_url(raw: Optional[str] = None) -> Optional[str]:
    """Return a Postgres URL when DATABASE_URL (or raw) points at Postgres."""
    url = raw if raw is not None else (os.getenv("DATABASE_URL") or "")
    url = url.strip()
    if url.startswith("postgres://") or url.startswith("postgresql://"):
        return url
    return None


def default_sqlite_path() -> str:
    """Return DATABASE_PATH or the repo-root SQLite filename."""
    return os.getenv("DATABASE_PATH") or "cannabis_papers.db"


def min_papers_for(profile: str) -> int:
    """Return the minimum paper count for a guard profile.

    ``CORPUS_MIN_PAPERS`` overrides every profile. Otherwise
    ``GOLDEN_MIN_PAPERS`` / ``RL_MIN_PAPERS`` apply, then the profile default.
    """
    shared = os.getenv("CORPUS_MIN_PAPERS")
    if shared:
        return max(0, int(shared))
    env_key = "GOLDEN_MIN_PAPERS" if profile == "golden" else "RL_MIN_PAPERS"
    override = os.getenv(env_key)
    if override:
        return max(0, int(override))
    if profile not in DEFAULT_MIN_PAPERS:
        raise ValueError(f"Unknown corpus guard profile: {profile}")
    return DEFAULT_MIN_PAPERS[profile]


def _redact_url(url: str) -> str:
    """Strip credentials from a database URL for error messages."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    dbname = (parsed.path or "/").lstrip("/") or "?"
    return f"{parsed.scheme}://{host}{port}/{dbname}"


def _is_known_wrong_sqlite(path: str) -> bool:
    """Return True when path is the Fly image default SQLite file."""
    resolved = os.path.abspath(os.path.expanduser(path))
    if resolved in KNOWN_WRONG_SQLITE_PATHS:
        return True
    return os.path.normpath(resolved) in KNOWN_WRONG_SQLITE_PATHS


def inspect_sqlite(path: str) -> CorpusSnapshot:
    """Inspect a SQLite file for the papers table and row count."""
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(resolved):
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=False,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail="file_missing",
        )
    if os.path.isdir(resolved):
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=True,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail="path_is_directory",
        )
    if os.path.getsize(resolved) == 0:
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=True,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail="empty_file",
        )
    try:
        conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=True,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail=f"not_sqlite:{exc}",
        )
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = tuple(name for name in REQUIRED_TABLES if name not in tables)
        count = 0
        if "papers" in tables:
            count = int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=True,
            papers_table="papers" in tables,
            paper_count=count,
            missing_tables=missing,
            detail="ok" if not missing else "missing_tables",
        )
    except sqlite3.Error as exc:
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=resolved,
            exists=True,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail=f"sqlite_error:{exc}",
        )
    finally:
        conn.close()


def inspect_postgres(url: Optional[str] = None) -> CorpusSnapshot:
    """Inspect Postgres for the papers table and row count."""
    resolved = postgres_url(url)
    if not resolved:
        return CorpusSnapshot(
            backend="postgres",
            path_or_url="",
            exists=False,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail="database_url_missing",
        )
    label = _redact_url(resolved)
    try:
        import psycopg2
    except ImportError:
        return CorpusSnapshot(
            backend="postgres",
            path_or_url=label,
            exists=False,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail="psycopg2_not_installed",
        )
    conn = None
    try:
        conn = psycopg2.connect(resolved, connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'papers'
                )
                """
            )
            has_papers = bool(cur.fetchone()[0])
            if not has_papers:
                return CorpusSnapshot(
                    backend="postgres",
                    path_or_url=label,
                    exists=True,
                    papers_table=False,
                    paper_count=0,
                    missing_tables=REQUIRED_TABLES,
                    detail="missing_tables",
                )
            cur.execute("SELECT COUNT(*) FROM papers")
            count = int(cur.fetchone()[0])
        return CorpusSnapshot(
            backend="postgres",
            path_or_url=label,
            exists=True,
            papers_table=True,
            paper_count=count,
            missing_tables=(),
            detail="ok",
        )
    except Exception as exc:
        return CorpusSnapshot(
            backend="postgres",
            path_or_url=label,
            exists=False,
            papers_table=False,
            paper_count=0,
            missing_tables=REQUIRED_TABLES,
            detail=f"connect_error:{exc}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _sqlite_fix_hint(path: str) -> str:
    """Return the operator hint for a rejected SQLite path."""
    return (
        f"Empty `/app/cannabis_papers.db` or an uninitialized local file is not the corpus.\n"
        f"Fix: pull from Postgres into a populated SQLite cache, then pass that path:\n"
        f"  DATABASE_URL=... python3 scripts/pull_papers_from_postgres.py --sqlite-path {path}\n"
        f"On Fly, use --sqlite-path /data/cannabis_papers.db (volume), never /app/cannabis_papers.db.\n"
        f"Postgres is the sole production source of truth."
    )


def _postgres_fix_hint() -> str:
    """Return the operator hint when Postgres is required or empty."""
    return (
        "DATABASE_URL must point at Fly Postgres (app cannabis-papers-db) for pull/push "
        "and Fly RL batches.\n"
        "Start the existing proxy helper, then retry:\n"
        "  ./scripts/run_with_fly_proxy_venv.sh\n"
        "Or: fly ssh console -a cannabis-paper-scraper -C 'printenv DATABASE_URL'"
    )


def _raise_for_snapshot(
    snapshot: CorpusSnapshot,
    *,
    profile: str,
    min_papers: int,
    sqlite_path: Optional[str] = None,
) -> None:
    """Raise CorpusGuardError when a snapshot is empty, incomplete, or the wrong file."""
    prefix = f"Corpus guard failed ({profile})"
    if snapshot.backend == "sqlite" and sqlite_path and _is_known_wrong_sqlite(sqlite_path):
        if snapshot.paper_count < min_papers or not snapshot.papers_table:
            raise CorpusGuardError(
                f"{prefix}: {snapshot.path_or_url} is the Fly image default SQLite, "
                f"not the corpus (papers={snapshot.paper_count}, min={min_papers}).\n"
                f"{_sqlite_fix_hint('/data/cannabis_papers.db')}"
            )
    if not snapshot.exists and snapshot.detail == "database_url_missing":
        raise CorpusGuardError(
            f"{prefix}: DATABASE_URL is unset or is not a Postgres URL.\n"
            f"{_postgres_fix_hint()}"
        )
    if snapshot.detail == "psycopg2_not_installed":
        raise CorpusGuardError(
            f"{prefix}: psycopg2 is not installed; cannot inspect Postgres.\n"
            "Install project requirements or run inside ./venv."
        )
    if snapshot.detail.startswith("connect_error:"):
        raise CorpusGuardError(
            f"{prefix}: cannot connect to {snapshot.path_or_url}: "
            f"{snapshot.detail.split(':', 1)[1].strip()}\n"
            f"{_postgres_fix_hint()}"
        )
    if snapshot.backend == "sqlite" and not snapshot.exists:
        raise CorpusGuardError(
            f"{prefix}: SQLite file not found: {snapshot.path_or_url}.\n"
            f"{_sqlite_fix_hint(snapshot.path_or_url)}"
        )
    if snapshot.missing_tables or not snapshot.papers_table:
        raise CorpusGuardError(
            f"{prefix}: {snapshot.backend} at {snapshot.path_or_url} is missing "
            f"required table(s): {', '.join(snapshot.missing_tables) or 'papers'} "
            f"({snapshot.detail}).\n"
            f"{_sqlite_fix_hint(snapshot.path_or_url) if snapshot.backend == 'sqlite' else _postgres_fix_hint()}"
        )
    if snapshot.paper_count < min_papers:
        raise CorpusGuardError(
            f"{prefix}: {snapshot.backend} at {snapshot.path_or_url} has "
            f"{snapshot.paper_count} papers (minimum {min_papers}).\n"
            + (
                _sqlite_fix_hint(snapshot.path_or_url)
                if snapshot.backend == "sqlite"
                else _postgres_fix_hint()
            )
        )


def assert_corpus_ready(
    *,
    profile: str = "golden",
    sqlite_path: Optional[str] = None,
    require_postgres: bool = False,
    allow_empty_sqlite: bool = False,
    min_papers: Optional[int] = None,
) -> CorpusSnapshot:
    """Refuse to proceed when the configured DB is empty or the wrong file.

    Args:
        profile: ``golden`` (local cache after pull) or ``rl`` (Fly/Postgres
            preferred; SQLite only when DATABASE_URL is unset).
        sqlite_path: Local SQLite path. Defaults to DATABASE_PATH or
            ``cannabis_papers.db``.
        require_postgres: Fail when DATABASE_URL is not a Postgres URL
            (pull/push and Fly RL).
        allow_empty_sqlite: Skip the SQLite row-count check (golden is about
            to pull into an empty cache). Postgres is still checked when
            required or when profile is ``rl`` and a URL is set.
        min_papers: Override the profile minimum.

    Returns:
        The snapshot that passed (Postgres when that backend was checked,
        otherwise SQLite).

    Raises:
        CorpusGuardError: The configured database is unusable.
        ValueError: Unknown profile.
    """
    if profile not in DEFAULT_MIN_PAPERS:
        raise ValueError(f"Unknown corpus guard profile: {profile}")
    if not guard_enabled():
        return CorpusSnapshot(
            backend="skipped",
            path_or_url="",
            exists=True,
            papers_table=True,
            paper_count=-1,
            missing_tables=(),
            detail="corpus_guard_disabled",
        )

    floor = min_papers if min_papers is not None else min_papers_for(profile)
    lite_path = sqlite_path or default_sqlite_path()
    pg_url = postgres_url()

    check_postgres = bool(require_postgres or (profile == "rl" and pg_url))
    if check_postgres:
        if not pg_url:
            raise CorpusGuardError(
                f"Corpus guard failed ({profile}): DATABASE_URL must point at "
                f"Postgres for this mode (SQLite is a local cache only).\n"
                f"{_postgres_fix_hint()}"
            )
        snapshot = inspect_postgres(pg_url)
        _raise_for_snapshot(snapshot, profile=profile, min_papers=floor)
        if profile == "rl" or allow_empty_sqlite:
            return snapshot

    if allow_empty_sqlite:
        return CorpusSnapshot(
            backend="sqlite",
            path_or_url=os.path.abspath(os.path.expanduser(lite_path)),
            exists=os.path.exists(lite_path),
            papers_table=True,
            paper_count=-1,
            missing_tables=(),
            detail="sqlite_check_skipped_pre_pull",
        )

    snapshot = inspect_sqlite(lite_path)
    _raise_for_snapshot(
        snapshot,
        profile=profile,
        min_papers=floor,
        sqlite_path=lite_path,
    )
    return snapshot


def require_corpus_or_exit(
    *,
    profile: str = "golden",
    sqlite_path: Optional[str] = None,
    require_postgres: bool = False,
    allow_empty_sqlite: bool = False,
    min_papers: Optional[int] = None,
) -> CorpusSnapshot:
    """Run ``assert_corpus_ready`` and exit 1 with the error on stderr."""
    try:
        snapshot = assert_corpus_ready(
            profile=profile,
            sqlite_path=sqlite_path,
            require_postgres=require_postgres,
            allow_empty_sqlite=allow_empty_sqlite,
            min_papers=min_papers,
        )
    except CorpusGuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        f"corpus_guard ok: backend={snapshot.backend} "
        f"target={snapshot.path_or_url or '(disabled)'} "
        f"papers={snapshot.paper_count} detail={snapshot.detail}"
    )
    return snapshot


def resolve_profile(explicit: Optional[str] = None) -> str:
    """Resolve the guard profile from CLI, CORPUS_GUARD_PROFILE, or GOLDEN_RUN."""
    if explicit:
        return explicit
    env_profile = (os.getenv("CORPUS_GUARD_PROFILE") or "").strip().lower()
    if env_profile in DEFAULT_MIN_PAPERS:
        return env_profile
    if os.getenv("GOLDEN_RUN", "").strip() in {"1", "true", "yes"}:
        return "golden"
    return "rl"


def build_parser() -> argparse.ArgumentParser:
    """Build the corpus-guard CLI parser."""
    parser = argparse.ArgumentParser(
        description="Refuse golden/RL runs against an empty or wrong database.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(DEFAULT_MIN_PAPERS),
        default=None,
        help="golden (local cache) or rl (Postgres when DATABASE_URL is set). "
        "Default: CORPUS_GUARD_PROFILE, else golden when GOLDEN_RUN=1, else rl.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="SQLite path (default: DATABASE_PATH or cannabis_papers.db).",
    )
    parser.add_argument(
        "--require-postgres",
        action="store_true",
        help="Fail when DATABASE_URL is not a Postgres URL.",
    )
    parser.add_argument(
        "--allow-empty-sqlite",
        action="store_true",
        help="Skip SQLite row counts (golden is about to pull).",
    )
    parser.add_argument(
        "--min-papers",
        type=int,
        default=None,
        help="Override CORPUS_MIN_PAPERS / profile default.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI entrypoint used by golden/RL shell wrappers."""
    args = build_parser().parse_args(argv)
    require_corpus_or_exit(
        profile=resolve_profile(args.profile),
        sqlite_path=args.sqlite_path,
        require_postgres=args.require_postgres,
        allow_empty_sqlite=args.allow_empty_sqlite,
        min_papers=args.min_papers,
    )


if __name__ == "__main__":
    main()
