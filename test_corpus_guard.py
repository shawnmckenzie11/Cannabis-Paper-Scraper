#!/usr/bin/env python3
"""Unit tests for the golden/RL corpus guard."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import corpus_guard


def _write_sqlite(path: str, *, papers: int = 0, create_papers: bool = True) -> None:
    """Create a tiny SQLite file for guard tests."""
    conn = sqlite3.connect(path)
    try:
        if create_papers:
            conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
            for index in range(papers):
                conn.execute(
                    "INSERT INTO papers (id, title) VALUES (?, ?)",
                    (index + 1, f"Paper {index + 1}"),
                )
        else:
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


class CorpusGuardTests(unittest.TestCase):
    """Fail-closed checks for empty and wrong SQLite / Postgres targets."""

    def test_missing_sqlite_file_fails(self) -> None:
        """A missing SQLite path is refused with an actionable message."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.db")
            with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
                corpus_guard.assert_corpus_ready(profile="golden", sqlite_path=path)
            self.assertIn("not found", str(ctx.exception))
            self.assertIn("pull_papers_from_postgres.py", str(ctx.exception))

    def test_empty_sqlite_fails_golden(self) -> None:
        """Zero papers is not a golden corpus."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.db")
            _write_sqlite(path, papers=0)
            with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
                corpus_guard.assert_corpus_ready(profile="golden", sqlite_path=path)
            message = str(ctx.exception)
            self.assertIn("0 papers", message)
            self.assertIn("minimum 1", message)
            self.assertIn("sole production source of truth", message)

    def test_missing_papers_table_fails(self) -> None:
        """A SQLite file without papers is treated as the wrong file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wrong.db")
            _write_sqlite(path, create_papers=False)
            with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
                corpus_guard.assert_corpus_ready(profile="golden", sqlite_path=path)
            self.assertIn("missing required table", str(ctx.exception))

    def test_populated_sqlite_passes_golden(self) -> None:
        """A pulled cache with at least one paper is accepted for golden."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pulled.db")
            _write_sqlite(path, papers=3)
            snapshot = corpus_guard.assert_corpus_ready(
                profile="golden",
                sqlite_path=path,
            )
            self.assertEqual(snapshot.backend, "sqlite")
            self.assertEqual(snapshot.paper_count, 3)

    def test_app_default_sqlite_is_rejected_when_empty(self) -> None:
        """/app/cannabis_papers.db is the image default, not the corpus."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_app = os.path.join(tmp, "app")
            os.makedirs(fake_app)
            path = os.path.join(fake_app, "cannabis_papers.db")
            _write_sqlite(path, papers=0)
            with patch.object(
                corpus_guard,
                "KNOWN_WRONG_SQLITE_PATHS",
                frozenset({os.path.abspath(path)}),
            ):
                with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
                    corpus_guard.assert_corpus_ready(profile="rl", sqlite_path=path)
            self.assertIn("image default", str(ctx.exception))

    def test_allow_empty_sqlite_skips_local_count_before_pull(self) -> None:
        """Golden pull may start from an empty cache when Postgres will fill it."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.db")
            snapshot = corpus_guard.assert_corpus_ready(
                profile="golden",
                sqlite_path=path,
                allow_empty_sqlite=True,
            )
            self.assertEqual(snapshot.detail, "sqlite_check_skipped_pre_pull")

    @patch.dict(os.environ, {}, clear=True)
    def test_require_postgres_without_url_fails(self) -> None:
        """Pull/push modes must not fall back to a random SQLite file."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "local.db")
            _write_sqlite(path, papers=5)
            with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
                corpus_guard.assert_corpus_ready(
                    profile="golden",
                    sqlite_path=path,
                    require_postgres=True,
                    allow_empty_sqlite=True,
                )
            message = str(ctx.exception)
            self.assertIn("DATABASE_URL", message)
            self.assertIn("run_with_fly_proxy_venv.sh", message)

    @patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/papers"},
        clear=True,
    )
    @patch("corpus_guard.inspect_postgres")
    def test_rl_uses_postgres_when_url_set(self, mock_inspect) -> None:
        """RL on Fly inspects Postgres and does not require local SQLite."""
        mock_inspect.return_value = corpus_guard.CorpusSnapshot(
            backend="postgres",
            path_or_url="postgresql://127.0.0.1:5432/papers",
            exists=True,
            papers_table=True,
            paper_count=21000,
            missing_tables=(),
            detail="ok",
        )
        snapshot = corpus_guard.assert_corpus_ready(profile="rl", sqlite_path="/missing.db")
        self.assertEqual(snapshot.backend, "postgres")
        self.assertEqual(snapshot.paper_count, 21000)
        mock_inspect.assert_called_once()

    @patch.dict(
        os.environ,
        {"DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/papers"},
        clear=True,
    )
    @patch("corpus_guard.inspect_postgres")
    def test_rl_refuses_empty_postgres(self, mock_inspect) -> None:
        """An empty Postgres is not a usable RL corpus."""
        mock_inspect.return_value = corpus_guard.CorpusSnapshot(
            backend="postgres",
            path_or_url="postgresql://127.0.0.1:5432/papers",
            exists=True,
            papers_table=True,
            paper_count=0,
            missing_tables=(),
            detail="ok",
        )
        with self.assertRaises(corpus_guard.CorpusGuardError) as ctx:
            corpus_guard.assert_corpus_ready(profile="rl")
        self.assertIn("0 papers", str(ctx.exception))

    @patch.dict(os.environ, {"CORPUS_GUARD": "0"}, clear=True)
    def test_emergency_override_skips_check(self) -> None:
        """CORPUS_GUARD=0 is the explicit emergency bypass."""
        snapshot = corpus_guard.assert_corpus_ready(profile="golden", sqlite_path="/nope.db")
        self.assertEqual(snapshot.detail, "corpus_guard_disabled")

    @patch.dict(os.environ, {"GOLDEN_RUN": "1"}, clear=True)
    def test_golden_run_env_selects_golden_profile(self) -> None:
        """GOLDEN_RUN=1 resolves the CLI profile to golden."""
        self.assertEqual(corpus_guard.resolve_profile(None), "golden")

    def test_cli_fails_closed_on_empty_sqlite(self) -> None:
        """The CLI exits 1 when the configured SQLite is empty."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.db")
            _write_sqlite(path, papers=0)
            with self.assertRaises(SystemExit) as ctx:
                corpus_guard.main(["--profile", "golden", "--sqlite-path", path])
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
