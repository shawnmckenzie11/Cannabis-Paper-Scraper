#!/usr/bin/env python3
"""Unit tests for Postgres backup/restore safety helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import postgres_backup as pgb


class PostgresBackupSafetyTests(unittest.TestCase):
    """Dump URL parsing and restore-to-scratch guards."""

    def test_parse_database_url_redacts_password(self) -> None:
        """Parsed connection fields keep the password off the public label."""
        conn = pgb.parse_database_url(
            "postgresql://user:s3cret@cannabis-papers-db.flycast:5432/papers"
        )
        self.assertEqual(conn["user"], "user")
        self.assertEqual(conn["password"], "s3cret")
        self.assertEqual(conn["database"], "papers")
        self.assertNotIn("s3cret", conn["label"])

    def test_parse_rejects_sqlite_url(self) -> None:
        """SQLite is not a pg_dump target."""
        with self.assertRaises(pgb.BackupError) as ctx:
            pgb.parse_database_url("sqlite:///cannabis_papers.db")
        self.assertIn("Postgres URL", str(ctx.exception))

    def test_live_prod_hosts_are_detected(self) -> None:
        """Fly cluster hostnames are treated as production."""
        self.assertTrue(
            pgb.looks_like_live_prod(
                "postgres://u:p@cannabis-papers-db.flycast:5432/papers"
            )
        )
        self.assertTrue(
            pgb.looks_like_live_prod(
                "postgres://u:p@cannabis-papers-db.internal:5432/papers"
            )
        )
        self.assertFalse(
            pgb.looks_like_live_prod("postgres://postgres:scratch@127.0.0.1:5433/postgres")
        )
        self.assertTrue(pgb.is_localhost("postgres://postgres@localhost:5432/scratch"))

    def test_restore_refuses_prod_by_default(self) -> None:
        """Restore to the live cluster fails without the dual confirmation."""
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "backup.dump"
            dump.write_bytes(b"PGDMP")
            with self.assertRaises(pgb.BackupError) as ctx:
                pgb.restore_database(
                    dump,
                    target_url="postgres://u:p@cannabis-papers-db.flycast:5432/papers",
                )
            message = str(ctx.exception)
            self.assertIn("Refusing restore to live Fly Postgres", message)
            self.assertIn("never wipes production by default", message)
            self.assertIn("127.0.0.1:5433", message)

    @patch.dict(os.environ, {"RESTORE_CONFIRM_PROD": "nope"}, clear=False)
    def test_allow_prod_without_confirm_env_still_refuses(self) -> None:
        """--allow-prod alone is not enough to wipe production."""
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "backup.dump"
            dump.write_bytes(b"PGDMP")
            with self.assertRaises(pgb.BackupError) as ctx:
                pgb.restore_database(
                    dump,
                    target_url="postgres://u:p@cannabis-papers-db.flycast:5432/papers",
                    allow_prod=True,
                )
            self.assertIn("RESTORE_CONFIRM_PROD", str(ctx.exception))

    def test_plain_list_rejects_random_text(self) -> None:
        """A non-dump file fails the dry-run list step."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.sql"
            path.write_text("not a dump\n", encoding="utf-8")
            with self.assertRaises(pgb.BackupError):
                pgb.list_dump(path, fmt="plain")

    def test_plain_list_accepts_pg_dump_header(self) -> None:
        """Dry-run list prints a header preview for a plain SQL dump."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.sql"
            path.write_text(
                "--\n-- PostgreSQL database dump\n--\nSET statement_timeout = 0;\n",
                encoding="utf-8",
            )
            preview = pgb.list_dump(path, fmt="plain")
            self.assertIn("PostgreSQL database dump", preview)

    def test_fly_snapshot_command_uses_papers_db_app(self) -> None:
        """Managed snapshot listing targets cannabis-papers-db."""
        cmd = pgb.fly_snapshot_list_command()
        self.assertEqual(cmd[:3], ["fly", "postgres", "backup"])
        self.assertIn("cannabis-papers-db", cmd)


if __name__ == "__main__":
    unittest.main()
