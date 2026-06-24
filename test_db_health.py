#!/usr/bin/env python3
"""Unit tests for Postgres health helpers."""

import unittest
from unittest.mock import patch

import db_health


class DbHealthTests(unittest.TestCase):
    """Health probe and production limit helpers."""

    @patch.dict("os.environ", {}, clear=True)
    def test_postgres_not_configured_is_healthy(self):
        """When DATABASE_URL is unset, health check passes (SQLite dev mode)."""
        healthy, detail = db_health.postgres_is_healthy()
        self.assertTrue(healthy)
        self.assertEqual(detail, "sqlite_or_unconfigured")

    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://localhost/test"}, clear=True)
    @patch("psycopg2.connect")
    def test_postgres_health_probe_success(self, mock_connect):
        """Healthy Postgres returns ok."""
        conn = mock_connect.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        healthy, detail = db_health.postgres_is_healthy()
        self.assertTrue(healthy)
        self.assertEqual(detail, "ok")
        cursor.execute.assert_any_call("SELECT 1")

    @patch.dict("os.environ", {"DATABASE_URL": "postgresql://localhost/test"}, clear=True)
    @patch("psycopg2.connect", side_effect=Exception("server closed the connection unexpectedly"))
    def test_postgres_health_probe_failure(self, _mock_connect):
        """Failed probe returns unhealthy with error detail."""
        healthy, detail = db_health.postgres_is_healthy()
        self.assertFalse(healthy)
        self.assertIn("server closed", detail)

    @patch.dict(
        "os.environ",
        {"DATABASE_URL": "postgresql://localhost/test", "REINGEST_WORKERS": "2"},
        clear=True,
    )
    def test_production_limits_on_postgres(self):
        """Postgres defaults use conservative worker and batch settings."""
        limits = db_health.production_reingest_limits()
        self.assertEqual(limits["workers"], 2)
        self.assertEqual(limits["batch_size"], 25)
        self.assertGreater(limits["batch_pause_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
