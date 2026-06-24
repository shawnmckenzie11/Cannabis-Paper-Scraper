"""Regression tests for Postgres DatabaseManager __init__ safety."""

import os
import unittest
from unittest.mock import MagicMock, patch

import db_manager


class PostgresInitTests(unittest.TestCase):
    """Ensure Postgres init never calls fetchone without a query or init_db on outage."""

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}, clear=False)
    @patch.object(db_manager.DatabaseManager, "get_connection")
    @patch.object(db_manager.DatabaseManager, "init_db")
    def test_fetchone_requires_exists_query(self, mock_init_db, mock_get_connection):
        """Regression: missing SELECT before fetchone must not run init_db on Postgres."""
        conn = MagicMock()
        cursor = MagicMock()
        conn.conn.cursor.return_value = cursor
        cursor.fetchone.return_value = {"exists": True}
        mock_get_connection.return_value = conn
        db_manager.DatabaseManager._postgres_compat_ready = True
        db_manager.DatabaseManager._initialized = False

        db_manager.DatabaseManager()

        executed = [str(call.args[0]) for call in cursor.execute.call_args_list]
        self.assertTrue(
            any("information_schema.tables" in sql for sql in executed),
            f"Expected papers EXISTS probe, got: {executed}",
        )
        mock_init_db.assert_not_called()

    @patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}, clear=False)
    @patch.object(db_manager.DatabaseManager, "get_connection", side_effect=Exception("no results to fetch"))
    @patch.object(db_manager.DatabaseManager, "init_db")
    def test_postgres_outage_skips_init_db(self, mock_init_db, _mock_get_connection):
        """When Postgres probe fails, never auto-run init_db on production URL."""
        db_manager.DatabaseManager._postgres_compat_ready = True
        db_manager.DatabaseManager._initialized = False

        db_manager.DatabaseManager()

        mock_init_db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
