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

    def test_quoted_phrase_kept_whole(self):
        self.assertEqual(
            websearch_or_groups('"cannabis use" OR cbd pain'),
            '"cannabis use" pain or cbd pain',
        )
        self.assertEqual(
            websearch_or_groups('pain "cannabis use" OR cbd'),
            'pain "cannabis use" or pain cbd',
        )
        out = websearch_or_groups('a OR "b c d" e')
        self.assertEqual(out, 'a e or "b c d" e')
        self.assertEqual(out.count('"') % 2, 0)

    def test_quoted_or_is_not_operator(self):
        self.assertEqual(websearch_or_groups('"a OR b" c'), '"a OR b" c')

    def test_unbalanced_quote_unchanged(self):
        self.assertEqual(websearch_or_groups('"cannabis use OR cbd pain'), '"cannabis use OR cbd pain')

    def test_huge_input_fast_and_unchanged(self):
        import time

        for q in ("t " * 200000 + "OR x", "a OR b " * 50000, "w" * 500000 + " OR y"):
            start = time.perf_counter()
            self.assertEqual(websearch_or_groups(q), q)
            self.assertLess(time.perf_counter() - start, 0.5)

    def test_expansion_cap(self):
        q = " ".join("a%d OR b%d" % (i, i) for i in range(7))  # 2**7 = 128 > 64
        self.assertEqual(websearch_or_groups(q), q)

    def test_search_papers_end_to_end_postgres_bind(self):
        """search_papers on a Postgres dialect sends the rewritten bind to the driver."""
        from unittest import mock

        from sqlalchemy import create_engine

        from repository import papers as papers_mod

        repo = papers_mod.PapersRepository(
            engine=create_engine("sqlite://"), tab_columns_exist=lambda: False
        )
        repo.dialect = DialectSQL("postgres")
        captured = []

        def fake_fetchall(conn, sql, params):
            captured.append((sql, list(params)))
            return []

        def fake_fetchone(conn, sql, params):
            captured.append((sql, list(params)))
            return {"total": 0}

        with mock.patch.object(papers_mod, "fetchall", fake_fetchall), mock.patch.object(
            papers_mod, "fetchone", fake_fetchone
        ):
            results, total = repo.search_papers(
                {"query": '"cannabis use" OR cbd pain', "limit": 5}, include_total=True
            )
        self.assertEqual((results, total), ([], 0))
        expected = '"cannabis use" pain or cbd pain'
        list_sql, list_params = captured[0]
        self.assertIn("websearch_to_tsquery", list_sql)
        self.assertEqual(list_sql.count("?"), len(list_params))
        self.assertEqual(list_params[:2], [expected, expected])
        count_sql, count_params = captured[1]
        self.assertEqual(count_params[0], expected)
        self.assertEqual(count_sql.count("?"), len(count_params))


if __name__ == "__main__":
    unittest.main()
