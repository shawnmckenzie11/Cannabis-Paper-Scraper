"""Dialect-aware SQL fragments for SQLite (dev) and Postgres (prod).

Generated at the call site so harvest/search/classify never run SQLite SQL
through a runtime rewriter.
"""

from __future__ import annotations

import itertools
import re
from typing import List


# Same expression as the idx_papers_fts GIN index in db_manager.py.
PG_FTS_VECTOR = (
    "to_tsvector('english', papers.title || ' ' || coalesce(papers.abstract, '') "
    "|| ' ' || coalesce(papers.authors, ''))"
)


class DialectSQL:
    """Backend-specific JSON membership, FTS, and INSERT helpers."""

    def __init__(self, name: str):
        normalized = (name or "").lower()
        if normalized.startswith("postgres"):
            self.name = "postgresql"
        else:
            self.name = "sqlite"

    @property
    def is_postgres(self) -> bool:
        """True when the live engine is PostgreSQL."""
        return self.name == "postgresql"

    def json_contains_value(self, column: str) -> str:
        """Match one value in a JSON array column or as a scalar. Uses 2 binds."""
        if self.is_postgres:
            return (
                f"(EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"CASE WHEN {column} LIKE '[%' THEN {column}::jsonb ELSE '[]'::jsonb END"
                f") AS _j(val) WHERE _j.val = ?) OR {column} = ?)"
            )
        return (
            f"((json_valid({column}) AND json_type({column}) = 'array' AND EXISTS ("
            f"SELECT 1 FROM json_each({column}) WHERE json_each.value = ?"
            f")) OR ({column} = ?))"
        )

    def json_contains_any(self, column: str, count: int) -> str:
        """Match any of ``count`` values in a JSON array or scalar IN. Uses 2*count binds."""
        placeholders = ",".join(["?"] * count)
        if self.is_postgres:
            return (
                f"(EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"CASE WHEN {column} LIKE '[%' THEN {column}::jsonb ELSE '[]'::jsonb END"
                f") AS _j(val) WHERE _j.val IN ({placeholders})) "
                f"OR {column} IN ({placeholders}))"
            )
        return (
            f"((json_valid({column}) AND json_type({column}) = 'array' AND EXISTS ("
            f"SELECT 1 FROM json_each({column}) WHERE json_each.value IN ({placeholders})"
            f")) OR ({column} IN ({placeholders})))"
        )

    def json_exists_value(self, column: str) -> str:
        """EXISTS membership for a JSON array column. Uses 1 bind."""
        if self.is_postgres:
            return (
                f"EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"CASE WHEN {column} LIKE '[%' THEN {column}::jsonb ELSE '[]'::jsonb END"
                f") AS _j(val) WHERE _j.val = ?)"
            )
        return f"EXISTS (SELECT 1 FROM json_each({column}) WHERE value = ?)"

    def json_exists_any(self, column: str, count: int) -> str:
        """EXISTS membership for any of ``count`` JSON array values. Uses count binds."""
        placeholders = ",".join(["?"] * count)
        if self.is_postgres:
            return (
                f"EXISTS (SELECT 1 FROM jsonb_array_elements_text("
                f"CASE WHEN {column} LIKE '[%' THEN {column}::jsonb ELSE '[]'::jsonb END"
                f") AS _j(val) WHERE _j.val IN ({placeholders}))"
            )
        return f"EXISTS (SELECT 1 FROM json_each({column}) WHERE value IN ({placeholders}))"

    def fts_from_clause(self) -> str:
        """FROM clause for catalog full-text search."""
        if self.is_postgres:
            return "FROM papers"
        return "FROM papers JOIN papers_fts ON papers.id = papers_fts.rowid"

    def fts_match_clause(self) -> str:
        """WHERE fragment for a free-text query. Uses 1 bind."""
        if self.is_postgres:
            return f"{PG_FTS_VECTOR} @@ websearch_to_tsquery('english', ?)"
        return "papers_fts MATCH ?"

    def fts_rank_expr(self) -> str:
        """SELECT expression for relevance rank. Postgres uses 1 bind (see fts_rank_binds)."""
        if self.is_postgres:
            return f"ts_rank({PG_FTS_VECTOR}, websearch_to_tsquery('english', ?)) AS rank"
        return "papers_fts.rank"

    def fts_query_bind(self, cleaned_query: str) -> str:
        """Bind value for fts_match_clause / fts_rank_expr.

        SQLite FTS5 binds OR tighter than implicit AND, so ``a OR b c`` means
        ``(a OR b) AND c``. Postgres websearch_to_tsquery binds AND tighter
        (``a | (b & c)``) and has no grouping, so on Postgres the query is
        rewritten to the equivalent disjunctive form ``a c OR b c``.
        """
        if not self.is_postgres:
            return cleaned_query
        return websearch_or_groups(cleaned_query)

    def fts_rank_binds(self, cleaned_query: str) -> List[str]:
        """Binds consumed by fts_rank_expr (Postgres only)."""
        return [self.fts_query_bind(cleaned_query)] if self.is_postgres else []

    def fts_rank_order(self) -> str:
        """ORDER BY direction for rank: ts_rank higher is better; SQLite bm25 lower is better."""
        return "rank DESC" if self.is_postgres else "rank ASC"

    def collate_c(self) -> str:
        """Deterministic text collation for ORDER BY (Postgres only)."""
        return ' COLLATE "C"' if self.is_postgres else ""

    def insert_returning(self, sql: str) -> str:
        """Append RETURNING id on Postgres INSERT statements."""
        stripped = sql.rstrip().rstrip(";")
        if self.is_postgres and "RETURNING" not in stripped.upper():
            return stripped + " RETURNING id"
        return stripped


def expand_study_types(study_types: List[str]) -> List[str]:
    """Expand legacy category tokens into Stage 2 study_type labels."""
    expanded: List[str] = []
    for item in study_types:
        if item == "RCT":
            expanded.extend(["Clinical (RCT)", "RCT"])
        elif item == "observational":
            expanded.extend(
                [
                    "Clinical (prospective)",
                    "Clinical (observational)",
                    "Clinical (retrospective)",
                    "observational",
                ]
            )
        elif item == "animal":
            expanded.extend(
                [
                    "Animal Models (Mouse)",
                    "Animal Models (Rat)",
                    "Animal Models (Other Rodents)",
                    "Animal Models (Non-Human Primates)",
                    "Animal Models (Other)",
                    "animal",
                ]
            )
        elif item == "in vitro":
            expanded.extend(
                [
                    "Cell Culture (Primary Cells)",
                    "Cell Culture (Cell Lines)",
                    "Cell Culture (Organoids)",
                    "Cell Culture (Co-Culture)",
                    "Cell Culture (PCLS)",
                    "Cell Culture (Other In Vitro)",
                    "in vitro",
                ]
            )
        else:
            expanded.append(item)
    return expanded


_MAX_OR_EXPANSION = 64
# Inputs beyond these caps are passed through unrewritten (websearch_to_tsquery
# still parses them; only the FTS5 OR-precedence fix-up is skipped).
_MAX_OR_REWRITE_CHARS = 2000
_MAX_OR_REWRITE_TOKENS = 200

# A token is a run of unquoted non-space chars and/or complete "quoted spans",
# so ``"cannabis use"`` (and ``foo"bar baz"``) stays a single token.
_WEBSEARCH_TOKEN_RE = re.compile(r'(?:[^\s"]+|"[^"]*")+')


def websearch_or_groups(cleaned_query: str) -> str:
    """Rewrite FTS5-precedence OR (``a OR b c`` = ``(a|b) & c``) for websearch_to_tsquery.

    Only an uppercase ``OR`` token is an operator, as in FTS5. Quoted phrases are kept
    intact as single tokens. The AND of OR-groups is expanded into an OR of
    AND-conjunctions (DNF); the query is returned unchanged if it has unbalanced
    quotes, exceeds the size caps, or the expansion would exceed _MAX_OR_EXPANSION.
    """
    query = cleaned_query or ""
    if "OR" not in query:
        return cleaned_query
    if len(query) > _MAX_OR_REWRITE_CHARS or query.count('"') % 2:
        return cleaned_query
    tokens = _WEBSEARCH_TOKEN_RE.findall(query)
    if len(tokens) > _MAX_OR_REWRITE_TOKENS or "OR" not in tokens:
        return cleaned_query
    groups: List[List[str]] = []
    pending_or = False
    for tok in tokens:
        if tok == "OR":
            pending_or = bool(groups)
            continue
        if pending_or:
            groups[-1].append(tok)
        else:
            groups.append([tok])
        pending_or = False
    if not groups:
        return cleaned_query
    total = 1
    for g in groups:
        total *= len(g)
        if total > _MAX_OR_EXPANSION:
            return cleaned_query
    # Linear in output size: at most 64 conjunctions of <=200 tokens each.
    return " or ".join(" ".join(c) for c in itertools.product(*groups))
