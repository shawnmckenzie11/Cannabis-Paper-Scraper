"""Postgres websearch_to_tsquery must keep SQLite FTS5 OR precedence ((a|b) & c)."""
import unittest

from repository.dialect import DialectSQL, websearch_or_groups
from repository.filters import build_filter_clauses


class PgOrPrecedenceTest(unittest.TestCase):
    def test_q24_expands_to_dnf(self):
        self.assertEqual(
            websearch_or_groups("cannabidiol OR CBD nausea"),
            "cannabidiol nausea or CBD nausea",
        )

    def test_no_or_unchanged(self):
        self.assertEqual(websearch_or_groups("cbd nausea"), "cbd nausea")
        self.assertEqual(websearch_or_groups("cbd or nausea"), "cbd or nausea")

    def test_multiple_groups(self):
        self.assertEqual(
            websearch_or_groups("a OR b c OR d"),
            "a c or a d or b c or b d",
        )

    def test_dangling_or(self):
        self.assertEqual(websearch_or_groups("OR a b OR"), "a b")

    def test_bind_used_by_both_dialects(self):
        pg = DialectSQL("postgres")
        self.assertEqual(pg.fts_query_bind("cannabidiol OR CBD nausea"), "cannabidiol nausea or CBD nausea")
        self.assertEqual(pg.fts_rank_binds("cannabidiol OR CBD nausea"), ["cannabidiol nausea or CBD nausea"])
        lite = DialectSQL("sqlite")
        self.assertEqual(lite.fts_query_bind("cannabidiol OR CBD nausea"), "cannabidiol OR CBD nausea")

    def test_filter_clause_bind_on_postgres(self):
        clauses, params = build_filter_clauses(
            {"query": "cannabidiol OR CBD nausea"}, dialect=DialectSQL("postgres"), tab_columns_exist=False
        )
        self.assertIn("websearch_to_tsquery", clauses[0])
        self.assertEqual(params[0], "cannabidiol nausea or CBD nausea")


if __name__ == "__main__":
    unittest.main()
