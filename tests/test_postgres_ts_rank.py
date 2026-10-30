"""SQL-shape checks for catalog search ranking (no live Postgres needed)."""
import unittest

from sqlalchemy import create_engine

from repository.dialect import DialectSQL
from repository.filters import apply_sort_sql
from repository.papers import PapersRepository


def _search_sql(dialect_name, filters):
    repo = PapersRepository(engine=create_engine("sqlite://"), tab_columns_exist=lambda: True)
    repo.dialect = DialectSQL(dialect_name)
    sql, params = repo._search_sql(filters, columns_sql="papers.id", include_rank=True)
    sql = apply_sort_sql(sql, filters, dialect=repo.dialect, query_val=filters.get("query"))
    return sql, params


class PostgresTsRankTests(unittest.TestCase):
    def test_postgres_orders_by_plain_ts_rank_desc(self):
        sql, params = _search_sql("postgresql", {"query": "cannabidiol epilepsy", "year_min": 2015})
        self.assertIn(
            "ts_rank(to_tsvector('english', papers.title || ' ' || coalesce(papers.abstract, '') "
            "|| ' ' || coalesce(papers.authors, '')), websearch_to_tsquery('english', ?)) AS rank",
            sql,
        )
        self.assertNotIn("0 AS rank", sql)
        self.assertTrue(sql.endswith(" ORDER BY rank DESC"))
        self.assertEqual(sql.count("?"), len(params))
        self.assertEqual(params, ["cannabidiol epilepsy", "cannabidiol epilepsy", 2015])

    def test_postgres_explicit_sort_still_wins(self):
        sql, params = _search_sql("postgresql", {"query": "cbd", "sort_by": "year"})
        self.assertIn("ORDER BY papers.year DESC", sql)
        self.assertEqual(sql.count("?"), len(params))

    def test_sqlite_ranking_unchanged(self):
        sql, params = _search_sql("sqlite", {"query": "cannabidiol epilepsy"})
        self.assertIn("papers.id, papers_fts.rank FROM papers JOIN papers_fts", sql)
        self.assertTrue(sql.endswith(" ORDER BY rank ASC"))
        self.assertEqual(params, ["cannabidiol epilepsy"])


if __name__ == "__main__":
    unittest.main()
