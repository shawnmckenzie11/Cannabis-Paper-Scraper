"""Dialect-aware SQL fragments for SQLite (dev) and Postgres (prod).

Generated at the call site so harvest/search/classify never run SQLite SQL
through a runtime rewriter.
"""

from __future__ import annotations

from typing import List


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
            return (
                "to_tsvector('english', papers.title || ' ' || coalesce(papers.abstract, '') "
                "|| ' ' || coalesce(papers.authors, '')) @@ websearch_to_tsquery('english', ?)"
            )
        return "papers_fts MATCH ?"

    def fts_rank_expr(self) -> str:
        """SELECT expression for relevance rank."""
        if self.is_postgres:
            return "0 AS rank"
        return "papers_fts.rank"

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
