"""Paper catalog repository — harvest, search, and list I/O."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

from repository.constants import (
    DASHBOARD_TAB_KEYS,
    JSON_LIST_FIELDS,
    PAPER_WRITE_FIELDS,
    TABLE_LIST_COLUMNS,
)
from repository.dialect import DialectSQL
from repository.engine import get_engine
from repository.filters import apply_sort_sql, build_filter_clauses, resolve_tab_sql
from repository.session import execute, fetchall, fetchone, inserted_id

logger = logging.getLogger(__name__)

TabColumnsFn = Optional[Callable[[], bool]]


def _parse_json_array(value: Any, *, require_brackets: bool) -> Any:
    """Parse a JSON array stored as text, leaving scalars alone when required."""
    if value is None:
        return [] if not require_brackets else None
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if require_brackets and not (text.startswith("[") and text.endswith("]")):
        return value
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        if not require_brackets:
            return []
    return value if require_brackets else []


def parse_list_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize JSON fields on a catalog list/search row."""
    res = dict(row)
    for field in ("authors", "outcome_domain"):
        if res.get(field):
            try:
                parsed = res[field] if isinstance(res[field], list) else json.loads(res[field])
                res[field] = parsed if isinstance(parsed, list) else []
            except Exception:
                res[field] = []
        else:
            res[field] = []
    for field in ("study_type", "exposure_method", "cannabis_type", "expert_locked_fields"):
        if res.get(field):
            parsed = _parse_json_array(res[field], require_brackets=True)
            if parsed is not None:
                res[field] = parsed
    return res


def parse_detail_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize JSON fields on a single-paper detail row."""
    res = dict(row)
    for field in JSON_LIST_FIELDS:
        if res.get(field):
            parsed = _parse_json_array(res[field], require_brackets=True)
            if parsed is not None:
                res[field] = parsed
    return res


def _prepare_paper_copy(paper: Dict[str, Any]) -> Tuple[Dict[str, Any], Any, Any]:
    """Normalize list/bool fields and pop harvest-only keys before write."""
    paper_copy = paper.copy()
    llm_metrics = paper_copy.pop("_llm_call_metrics", None)
    harvest_batch_id = paper_copy.pop("_harvest_batch_id", None)
    for list_field in JSON_LIST_FIELDS:
        if list_field in paper_copy and not isinstance(paper_copy[list_field], str):
            paper_copy[list_field] = json.dumps(paper_copy[list_field])
    if "open_access" in paper_copy:
        paper_copy["open_access"] = 1 if paper_copy["open_access"] else 0
    if "date_harvested" not in paper_copy or not paper_copy["date_harvested"]:
        paper_copy["date_harvested"] = datetime.now().isoformat()
    if "publication_date" not in paper_copy or not paper_copy["publication_date"]:
        if paper_copy.get("year"):
            paper_copy["publication_date"] = f"{paper_copy['year']}-01-01"
        else:
            paper_copy["publication_date"] = paper_copy["date_harvested"][:10]
    if "publication_type" not in paper_copy or not paper_copy["publication_type"]:
        import extractor

        paper_copy["publication_type"] = extractor.infer_publication_type(
            paper_copy.get("title") or "",
            paper_copy.get("abstract") or "",
        )
    return paper_copy, llm_metrics, harvest_batch_id


class PapersRepository:
    """Backend-neutral paper catalog access over SQLAlchemy Core."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        engine: Optional[Engine] = None,
        tab_columns_exist: TabColumnsFn = None,
    ):
        self.db_path = db_path
        self.engine = engine or get_engine(db_path)
        self.dialect = DialectSQL(self.engine.dialect.name)
        self._tab_columns_exist_fn = tab_columns_exist
        self._tab_columns_exist_cache: Optional[bool] = None

    def tab_columns_exist(self) -> bool:
        """Return True when indexed tab_* columns are present on papers."""
        if self._tab_columns_exist_fn is not None:
            return bool(self._tab_columns_exist_fn())
        if self._tab_columns_exist_cache is not None:
            return self._tab_columns_exist_cache
        inspector = inspect(self.engine)
        columns = {col["name"] for col in inspector.get_columns("papers")}
        exists = "tab_preclinical" in columns
        self._tab_columns_exist_cache = exists
        return exists

    def build_filter_clauses(self, filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
        """Public filter builder used by DatabaseManager tests and search."""
        return build_filter_clauses(
            filters,
            dialect=self.dialect,
            tab_columns_exist=self.tab_columns_exist(),
        )

    def get_paper(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """Return one paper by primary key, or None."""
        with self.engine.connect() as conn:
            row = fetchone(conn, "SELECT * FROM papers WHERE id = ?", (paper_id,))
        if not row:
            return None
        return parse_detail_row(row)

    def delete_paper(self, paper_id: int) -> bool:
        """Delete one paper by primary key."""
        with self.engine.begin() as conn:
            result = execute(conn, "DELETE FROM papers WHERE id = ?", (paper_id,))
            return int(result.rowcount or 0) > 0

    def get_all_pmids(self) -> set:
        """Return every stored PMID for harvest skip checks."""
        with self.engine.connect() as conn:
            rows = fetchall(conn, "SELECT pmid FROM papers WHERE pmid IS NOT NULL")
        return {row["pmid"] for row in rows}

    def find_paper_id_by_title(self, title: str) -> Optional[int]:
        """Return the paper id for an exact title match (case-insensitive)."""
        normalized = (title or "").strip()
        if not normalized:
            return None
        with self.engine.connect() as conn:
            row = fetchone(
                conn,
                "SELECT id FROM papers WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) LIMIT 1",
                (normalized,),
            )
        return int(row["id"]) if row else None

    def find_top_title_matches(
        self,
        title: str,
        *,
        limit: int = 5,
        min_ratio: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """Return up to ``limit`` candidate papers ranked by title similarity."""
        import pdf_upload_merge

        normalized = (title or "").strip()
        if not normalized:
            return []

        cleaned = pdf_upload_merge.clean_title_for_matching(normalized)
        query_for_match = cleaned or normalized
        exact_id = self.find_paper_id_by_title(normalized)
        if exact_id is None and cleaned and cleaned != normalized:
            exact_id = self.find_paper_id_by_title(cleaned)

        tokens = pdf_upload_merge.significant_title_tokens(query_for_match, limit=8)
        if not tokens:
            seed = pdf_upload_merge.normalize_title(query_for_match)
            tokens = [seed.split()[0]] if seed.split() else []

        select_cols = "id, title, year, journal, publication_type, pmid, doi, full_text_link"
        rows_by_id: Dict[int, Dict[str, Any]] = {}

        with self.engine.connect() as conn:
            if exact_id is not None:
                row = fetchone(
                    conn,
                    f"SELECT {select_cols} FROM papers WHERE id = ?",
                    (exact_id,),
                )
                if row:
                    rows_by_id[int(row["id"])] = row

            and_tokens = tokens[:6]
            if len(and_tokens) >= 3:
                clauses = " AND ".join(["LOWER(title) LIKE ?" for _ in and_tokens])
                for row in fetchall(
                    conn,
                    f"SELECT {select_cols} FROM papers WHERE {clauses} LIMIT 80",
                    tuple(f"%{tok[:28]}%" for tok in and_tokens),
                ):
                    rows_by_id[int(row["id"])] = row

            pattern = pdf_upload_merge.title_token_like_pattern(query_for_match, max_tokens=8)
            if pattern and pattern != "%%":
                for row in fetchall(
                    conn,
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 80",
                    (pattern,),
                ):
                    rows_by_id[int(row["id"])] = row

            phrase_tokens = pdf_upload_merge.normalize_title(query_for_match).split()[:6]
            if len(phrase_tokens) >= 4:
                short_pattern = "%" + "%".join(phrase_tokens) + "%"
                for row in fetchall(
                    conn,
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 80",
                    (short_pattern,),
                ):
                    rows_by_id[int(row["id"])] = row

            for token in and_tokens[:4]:
                for row in fetchall(
                    conn,
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 120",
                    (f"%{token[:28]}%",),
                ):
                    rows_by_id[int(row["id"])] = row

        scored: List[Dict[str, Any]] = []
        for row in rows_by_id.values():
            ratio = pdf_upload_merge.title_similarity(normalized, row.get("title") or "")
            if ratio < min_ratio and (exact_id is None or int(row["id"]) != exact_id):
                continue
            scored.append(
                {
                    "id": int(row["id"]),
                    "title": row.get("title") or "",
                    "year": row.get("year"),
                    "journal": row.get("journal") or "",
                    "publication_type": row.get("publication_type") or "",
                    "pmid": row.get("pmid"),
                    "doi": row.get("doi"),
                    "full_text_link": row.get("full_text_link") or "",
                    "similarity": round(ratio, 3),
                }
            )
        return pdf_upload_merge.collapse_title_match_rows(
            scored,
            query_title=normalized,
            limit=limit,
        )

    def find_fuzzy_paper_by_title(
        self,
        title: str,
        *,
        min_ratio: float = 0.82,
    ) -> Tuple[Optional[int], float]:
        """Return (paper_id, similarity) for the best title match at or above min_ratio."""
        matches = self.find_top_title_matches(title, limit=1, min_ratio=min_ratio)
        if not matches:
            soft = self.find_top_title_matches(title, limit=1, min_ratio=0.0)
            if soft:
                return None, float(soft[0]["similarity"])
            return None, 0.0
        return int(matches[0]["id"]), float(matches[0]["similarity"])

    def _lookup_existing_id(
        self,
        conn: Connection,
        paper_copy: Dict[str, Any],
        force_id: Optional[int],
    ) -> Optional[int]:
        """Resolve an existing paper id from force_id or identifiers."""
        existing_id = int(force_id) if force_id is not None else None
        if existing_id:
            return existing_id
        for field in ("pmid", "doi", "semantic_scholar_id"):
            value = paper_copy.get(field)
            if not value:
                continue
            row = fetchone(conn, f"SELECT id FROM papers WHERE {field} = ?", (value,))
            if row:
                return int(row["id"])
        # Title is not a uniqueness key: harvest can ingest distinct PMIDs that
        # share a title. PDF review uses force_id / find_fuzzy_paper_by_title.
        return None

    def _log_llm_call(
        self,
        conn: Connection,
        paper_id: Optional[int],
        metrics: Dict[str, Any],
        batch_id: Optional[str] = None,
    ) -> None:
        """Insert one llm_calls_log row on the current transaction."""
        sql = """
            INSERT INTO llm_calls_log (
                paper_id, timestamp, model, input_tokens, cache_read_tokens, cache_write_tokens,
                output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence,
                classifier_version, batch_id, bm25_retrieval_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            paper_id,
            datetime.now().isoformat(),
            metrics.get("model", "unknown"),
            metrics.get("input_tokens", 0),
            metrics.get("cache_read_tokens", 0),
            metrics.get("cache_write_tokens", 0),
            metrics.get("output_tokens", 0),
            metrics.get("cost", 0.0),
            metrics.get("few_shot_similarity", 0.0),
            metrics.get("few_shot_count", 0),
            metrics.get("classification_confidence", 0.0),
            metrics.get("classifier_version", "1.0.0"),
            batch_id,
            int(metrics.get("bm25_retrieval_used", 0) or 0),
        )
        execute(conn, sql, params)

    def sync_tab_flags_for_paper(
        self,
        paper_id: int,
        *,
        conn: Optional[Connection] = None,
        publication_type: Optional[str] = None,
        study_type: Any = None,
        ingestion_status: Optional[str] = None,
    ) -> None:
        """Update denormalized tab_* columns for one paper when those columns exist."""
        from paper_tab_flags import TAB_FLAG_FIELDS, compute_tab_flags

        def _apply(active_conn: Connection) -> None:
            ptype, stype, status = publication_type, study_type, ingestion_status
            if ptype is None and stype is None and status is None:
                row = fetchone(
                    active_conn,
                    "SELECT publication_type, study_type, ingestion_status FROM papers WHERE id = ?",
                    (paper_id,),
                )
                if not row:
                    return
                ptype = row["publication_type"]
                stype = row["study_type"]
                status = row["ingestion_status"]
            flags = compute_tab_flags(
                publication_type=ptype,
                study_type=stype,
                ingestion_status=status,
            )
            set_parts = []
            params: List[Any] = []
            for column in TAB_FLAG_FIELDS.values():
                if column in flags:
                    set_parts.append(f"{column} = ?")
                    params.append(flags[column])
            if not set_parts:
                return
            params.append(paper_id)
            execute(
                active_conn,
                f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )

        try:
            if conn is not None:
                _apply(conn)
                return
            with self.engine.begin() as owned:
                _apply(owned)
        except Exception as exc:
            message = str(exc).lower()
            if "no such column" in message or "does not exist" in message:
                logger.debug("Tab flag columns unavailable; skipping sync for paper %s", paper_id)
                return
            raise

    def insert_paper(self, paper: Dict[str, Any], *, force_id: Optional[int] = None) -> int:
        """Insert or update a paper; returns the row id.

        Conflicts are resolved on ``force_id``, then PMID, DOI, and Semantic
        Scholar id — not title.
        """
        paper_copy, llm_metrics, harvest_batch_id = _prepare_paper_copy(paper)
        try:
            with self.engine.begin() as conn:
                existing_id = self._lookup_existing_id(conn, paper_copy, force_id)
                if existing_id:
                    update_pairs = []
                    values: List[Any] = []
                    for field in PAPER_WRITE_FIELDS:
                        if field in paper_copy:
                            update_pairs.append(f"{field} = ?")
                            values.append(paper_copy[field])
                    values.append(existing_id)
                    execute(
                        conn,
                        f"UPDATE papers SET {', '.join(update_pairs)} WHERE id = ?",
                        values,
                    )
                    row_id = existing_id
                else:
                    present_fields = [field for field in PAPER_WRITE_FIELDS if field in paper_copy]
                    placeholders = ",".join(["?"] * len(present_fields))
                    values = [paper_copy[field] for field in present_fields]
                    insert_sql = self.dialect.insert_returning(
                        f"INSERT INTO papers ({', '.join(present_fields)}) VALUES ({placeholders})"
                    )
                    result = execute(conn, insert_sql, values)
                    row_id = inserted_id(result, returning=self.dialect.is_postgres)
                    if row_id is None:
                        raise RuntimeError("INSERT did not return a paper id")

                if llm_metrics:
                    self._log_llm_call(
                        conn,
                        paper_id=row_id,
                        metrics=llm_metrics,
                        batch_id=harvest_batch_id or "harvest",
                    )
                try:
                    self.sync_tab_flags_for_paper(
                        int(row_id),
                        conn=conn,
                        publication_type=paper_copy.get("publication_type"),
                        study_type=paper.get("study_type", paper_copy.get("study_type")),
                        ingestion_status=paper_copy.get("ingestion_status"),
                    )
                except Exception as flag_exc:
                    logger.error("Tab flag sync failed for paper %s: %s", row_id, flag_exc)
                return int(row_id)
        except Exception as exc:
            raise RuntimeError(f"Database error during insert/update: {exc}") from exc

    def _search_sql(
        self,
        filters: Dict[str, Any],
        *,
        columns_sql: str,
        include_rank: bool,
    ) -> Tuple[str, List[Any]]:
        """Assemble SELECT + WHERE + ORDER + LIMIT for a catalog query."""
        query_val = filters.get("query")
        if query_val:
            rank = f", {self.dialect.fts_rank_expr()}" if include_rank else ""
            select_sql = f"SELECT {columns_sql}{rank} {self.dialect.fts_from_clause()}"
        else:
            select_sql = f"SELECT {columns_sql} FROM papers"
        where_clauses, params = self.build_filter_clauses(filters)
        sql = select_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        return sql, params

    def count_papers(self, filters: Dict[str, Any], *, conn: Optional[Connection] = None) -> int:
        """Count papers matching filters."""
        query_val = filters.get("query")
        if query_val:
            select_sql = f"SELECT COUNT(*) AS total {self.dialect.fts_from_clause()}"
        else:
            select_sql = "SELECT COUNT(*) AS total FROM papers"
        where_clauses, params = self.build_filter_clauses(filters)
        sql = select_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        def _run(active: Connection) -> int:
            row = fetchone(active, sql, params)
            if not row:
                return 0
            return int(row.get("total") or 0)

        if conn is not None:
            return _run(conn)
        with self.engine.connect() as owned:
            return _run(owned)

    def search_papers(
        self,
        filters: Dict[str, Any],
        include_total: bool = False,
    ):
        """Query the catalog with the same filters as the dashboard list API."""
        list_columns_sql = ", ".join(TABLE_LIST_COLUMNS)
        sql, params = self._search_sql(filters, columns_sql=list_columns_sql, include_rank=True)
        sql = apply_sort_sql(sql, filters, dialect=self.dialect, query_val=filters.get("query"))
        limit = filters.get("limit")
        offset = filters.get("offset")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                sql += " OFFSET ?"
                params.append(int(offset))

        with self.engine.connect() as conn:
            rows = fetchall(conn, sql, params)
            results = [parse_list_row(row) for row in rows]
            if include_total:
                count_filters = {
                    key: value
                    for key, value in filters.items()
                    if key not in ("limit", "offset")
                }
                return results, self.count_papers(count_filters, conn=conn)
            return results

    def search_papers_for_analysis(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return all papers matching analysis filter settings."""
        return self.search_papers(filters)

    def search_papers_minimal_for_section_stats(
        self,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return lightweight rows for section-stats (optional hard cap)."""
        columns_sql = (
            "papers.id, papers.title, papers.abstract, papers.full_text_link, papers.classifier_version"
        )
        sql, params = self._search_sql(filters, columns_sql=columns_sql, include_rank=False)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.engine.connect() as conn:
            return fetchall(conn, sql, params)

    def search_papers_by_ids(self, paper_ids: List[Any]) -> List[Dict[str, Any]]:
        """Return list-column rows for the given paper ids, preserving id order."""
        ids: List[int] = []
        for raw in paper_ids:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        list_columns_sql = ", ".join(TABLE_LIST_COLUMNS)
        placeholders = ",".join(["?"] * len(ids))
        sql = f"SELECT {list_columns_sql} FROM papers WHERE papers.id IN ({placeholders})"
        with self.engine.connect() as conn:
            rows = fetchall(conn, sql, ids)
        by_id = {int(row["id"]): parse_list_row(row) for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def get_metadata(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Read one system_metadata value."""
        with self.engine.connect() as conn:
            row = fetchone(conn, "SELECT value FROM system_metadata WHERE key = ?", (key,))
        if not row:
            return default
        value = row.get("value")
        return default if value is None else str(value)

    def set_metadata(self, key: str, value: str) -> None:
        """Upsert one system_metadata value."""
        with self.engine.begin() as conn:
            existing = fetchone(conn, "SELECT key FROM system_metadata WHERE key = ?", (key,))
            if existing:
                execute(conn, "UPDATE system_metadata SET value = ? WHERE key = ?", (value, key))
            else:
                execute(conn, "INSERT INTO system_metadata (key, value) VALUES (?, ?)", (key, value))

    def get_tab_counts(self) -> Dict[str, int]:
        """Return paper counts for each primary dashboard tab."""
        cache_key = "dashboard_tab_counts_json"
        cache_at_key = "dashboard_tab_counts_cached_at"
        import os

        cache_ttl = int(os.getenv("TAB_COUNTS_CACHE_SECONDS", "120"))
        try:
            cached_raw = self.get_metadata(cache_key)
            cached_at_raw = self.get_metadata(cache_at_key)
            if cached_raw and cached_at_raw:
                age = time.time() - float(cached_at_raw)
                if age < cache_ttl:
                    parsed = json.loads(cached_raw)
                    if isinstance(parsed, dict):
                        return {str(k): int(v) for k, v in parsed.items()}
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        counts: Dict[str, int] = {}
        tab_ready = self.tab_columns_exist()
        with self.engine.connect() as conn:
            for tab_key in DASHBOARD_TAB_KEYS:
                tab_sql = resolve_tab_sql(
                    tab_key, dialect=self.dialect, tab_columns_exist=tab_ready
                )
                if not tab_sql:
                    counts[tab_key] = 0
                    continue
                row = fetchone(conn, f"SELECT COUNT(*) AS total FROM papers WHERE {tab_sql}")
                counts[tab_key] = int(row["total"]) if row else 0
        try:
            self.set_metadata(cache_key, json.dumps(counts))
            self.set_metadata(cache_at_key, str(time.time()))
        except Exception as exc:
            logger.debug("Tab count cache write failed: %s", exc)
        return counts


def get_papers_repository(db_path: Optional[str] = None) -> PapersRepository:
    """Return a PapersRepository bound to the live engine (Postgres or SQLite)."""
    return PapersRepository(db_path=db_path)
