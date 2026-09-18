"""Classification / feedback-audit access used by the classify path."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.engine import Engine

from repository.dialect import DialectSQL
from repository.engine import get_engine
from repository.session import fetchall, fetchone

logger = logging.getLogger(__name__)


def build_bm25_query(text: str, max_terms: int = 8) -> str:
    """Build an FTS-friendly OR query from free text for correction retrieval."""
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stopwords = {
        "the", "a", "an", "of", "in", "with", "after", "were", "was", "we", "and", "to",
        "for", "this", "that", "using", "used", "from", "by", "on", "at", "as", "is", "are",
        "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "should", "could", "may", "might", "must", "can", "into", "through", "during",
        "before", "between", "out", "off", "over", "under", "again", "further", "then",
        "once", "here", "there", "when", "where", "why", "how", "all", "each", "few",
        "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same",
        "so", "than", "too", "very", "just", "also", "our", "their", "its", "it", "they",
    }
    selected: List[str] = []
    seen = set()
    for token in tokens:
        if len(token) <= 2 or token in stopwords or token in seen:
            continue
        seen.add(token)
        selected.append(token)
        if len(selected) >= max_terms:
            break
    if not selected:
        return ""
    return " OR ".join(selected)


class ClassifyRepository:
    """Backend-neutral feedback-audit reads for few-shot classify."""

    def __init__(self, db_path: Optional[str] = None, *, engine: Optional[Engine] = None):
        self.engine = engine or get_engine(db_path)
        self.dialect = DialectSQL(self.engine.dialect.name)

    def _fts_table_exists(self, table_name: str) -> bool:
        """Return True when a SQLite FTS table exists."""
        with self.engine.connect() as conn:
            row = fetchone(
                conn,
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (table_name,),
            )
        return row is not None

    def get_historical_corrections(self) -> List[Dict[str, Any]]:
        """Return unique corrected papers from feedback_audit."""
        sql = """
            SELECT paper_id, title, abstract
            FROM feedback_audit
            GROUP BY paper_id, title, abstract
        """
        try:
            with self.engine.connect() as conn:
                return fetchall(conn, sql)
        except Exception as exc:
            logger.error("Failed to fetch historical corrections: %s", exc)
            return []

    def get_feedback_audit_for_paper(self, paper_id: int) -> List[Dict[str, Any]]:
        """Return all feedback-audit field corrections for one paper."""
        with self.engine.connect() as conn:
            return fetchall(
                conn,
                """
                SELECT field_name, old_value, new_value
                FROM feedback_audit
                WHERE paper_id = ?
                ORDER BY id ASC
                """,
                (paper_id,),
            )

    def search_feedback_corrections_bm25(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve feedback_audit rows ranked by full-text relevance."""
        cleaned_query = build_bm25_query(query_text)
        if not cleaned_query:
            return []

        try:
            with self.engine.connect() as conn:
                if self.dialect.is_postgres:
                    pg_query = cleaned_query.replace(" OR ", " | ")
                    rows = fetchall(
                        conn,
                        """
                        SELECT
                            id,
                            paper_id,
                            field_name,
                            old_value,
                            new_value,
                            title,
                            abstract,
                            ts_rank_cd(
                                to_tsvector(
                                    'english',
                                    coalesce(title, '') || ' ' || coalesce(abstract, '') || ' ' ||
                                    coalesce(field_name, '') || ' ' || coalesce(old_value, '') || ' ' || coalesce(new_value, '')
                                ),
                                to_tsquery('english', ?)
                            ) AS bm25_score
                        FROM feedback_audit
                        WHERE to_tsvector(
                            'english',
                            coalesce(title, '') || ' ' || coalesce(abstract, '') || ' ' ||
                            coalesce(field_name, '') || ' ' || coalesce(old_value, '') || ' ' || coalesce(new_value, '')
                        ) @@ to_tsquery('english', ?)
                        ORDER BY bm25_score DESC
                        LIMIT ?
                        """,
                        (pg_query, pg_query, limit),
                    )
                else:
                    if not self._fts_table_exists("feedback_audit_fts"):
                        return []
                    rows = fetchall(
                        conn,
                        """
                        SELECT
                            fa.id,
                            fa.paper_id,
                            fa.field_name,
                            fa.old_value,
                            fa.new_value,
                            fa.title,
                            fa.abstract,
                            bm25(feedback_audit_fts) AS bm25_score
                        FROM feedback_audit_fts
                        JOIN feedback_audit fa ON fa.id = feedback_audit_fts.rowid
                        WHERE feedback_audit_fts MATCH ?
                        ORDER BY bm25_score
                        LIMIT ?
                        """,
                        (cleaned_query, limit),
                    )
            for row in rows:
                score = float(row.get("bm25_score") or 0.0)
                if self.dialect.is_postgres:
                    row["retrieval_similarity"] = max(0.0, min(1.0, score))
                else:
                    row["retrieval_similarity"] = max(0.0, min(1.0, 1.0 / (1.0 + abs(score))))
            return rows
        except Exception:
            return []


def get_classify_repository(db_path: Optional[str] = None) -> ClassifyRepository:
    """Return a ClassifyRepository bound to the live engine."""
    return ClassifyRepository(db_path=db_path)
