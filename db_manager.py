# db_manager.py
import sqlite3
import os
import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Sequence
import logging
import time

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

from repository.constants import (
    DASHBOARD_TAB_KEYS as _DASHBOARD_TAB_KEYS,
    SQL_CLINICAL_STUDY as _SQL_CLINICAL_STUDY,
    SQL_HAS_FULL_TEXT_LINK as _SQL_HAS_FULL_TEXT_LINK,
    SQL_HAS_PDF_LINK as _SQL_HAS_PDF_LINK,
    SQL_INGESTION_IRRELEVANT as _SQL_INGESTION_IRRELEVANT,
    SQL_INGESTION_NOT_CANNABIS as _SQL_INGESTION_NOT_CANNABIS,
    SQL_INGESTION_ROUTED as _SQL_INGESTION_ROUTED,
    SQL_INGESTION_TANGENTIAL as _SQL_INGESTION_TANGENTIAL,
    SQL_ORIGINAL_RESEARCH as _SQL_ORIGINAL_RESEARCH,
    SQL_PRECLINICAL_STUDY as _SQL_PRECLINICAL_STUDY,
    SQL_REVIEW_PUBLICATION as _SQL_REVIEW_PUBLICATION,
    TABLE_LIST_COLUMNS,
    TAB_SQL as _TAB_SQL,
)

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")
SCHEMA_FILE = "schema.sql"


class PostgresCursorWrapper:
    """DEPRECATED temporary bridge for unmigrated scripts.

    Harvest, classify, and catalog search/list use ``repository.PapersRepository``
    / ``repository.ClassifyRepository`` and must not go through this rewriter.
    New call sites should emit dialect-aware SQL via ``repository.dialect`` and
    execute it with SQLAlchemy Core. This wrapper remains only for user/auth,
    analyses, heuristics-editor, and sync scripts that still issue raw SQLite SQL.
    """
    def __init__(self, cursor):
        self.cursor = cursor
        self.lastrowid_value = None

    def execute(self, sql, params=None):
        if params is None:
            params = ()
        
        # 1. Replace SQLite parameter placeholder ? or ?1, ?2, etc. with PostgreSQL %s
        sql = re.sub(r"\?\d*", "%s", sql)
        
        # 2. Translate FTS Match
        if "papers_fts" in sql:
            # Remove content JOIN and replace MATCH with Postgres @@ search
            sql = re.sub(
                r"JOIN\s+papers_fts\s+ON\s+papers\.id\s+=\s+papers_fts\.rowid", 
                "", 
                sql, 
                flags=re.IGNORECASE
            )
            sql = re.sub(
                r"JOIN\s+papers_fts\s+ON\s+papers_fts\.rowid\s+=\s+papers\.id", 
                "", 
                sql, 
                flags=re.IGNORECASE
            )
            sql = re.sub(
                r"papers_fts\.rank", 
                "0 AS rank", 
                sql, 
                flags=re.IGNORECASE
            )
            sql = re.sub(
                r"papers_fts\s+MATCH\s+(%s|\?)", 
                "to_tsvector('english', papers.title || ' ' || coalesce(papers.abstract, '') || ' ' || coalesce(papers.authors, '')) @@ websearch_to_tsquery('english', \\1)", 
                sql, 
                flags=re.IGNORECASE
            )
            
        sql = self.translate_json_queries(sql)
        
        # 3. Handle RETURNING clause for INSERT queries to emulate lastrowid
        is_insert = sql.strip().upper().startswith("INSERT INTO")
        if is_insert and "RETURNING" not in sql.upper():
            match = re.match(r"INSERT\s+INTO\s+[\"`\[]?([a-zA-Z0-9_]+)[\"`\]]?", sql.strip(), re.IGNORECASE)
            table_name = match.group(1).lower() if match else ""
            if table_name != "system_metadata":
                sql = sql.rstrip(';').strip() + " RETURNING id"
            
        # Escape literal % characters (not part of %s placeholders) as %% for psycopg2
        sql = re.sub(r"%(?!s\b)", "%%", sql)
        
        try:
            self.cursor.execute(sql, params)
            if is_insert and "RETURNING" in sql.upper():
                try:
                    row = self.cursor.fetchone()
                    if row:
                        self.lastrowid_value = row.get("id") or list(row.values())[0]
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Postgres execution failed. SQL: {sql}, Params: {params}, Error: {e}")
            raise e
        return self

    def executemany(self, sql, seq_of_parameters):
        sql = re.sub(r"\?\d*", "%s", sql)
        sql = self.translate_json_queries(sql)
        sql = re.sub(r"%(?!s\b)", "%%", sql)
        try:
            self.cursor.executemany(sql, seq_of_parameters)
        except Exception as e:
            logger.error(f"Postgres executemany failed. SQL: {sql}, Error: {e}")
            raise e
        return self
        
    def translate_json_queries(self, sql):
        # Convert EXISTS json_each pattern
        sql = re.sub(
            r"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\((papers\.)?outcome_domain\)\s+WHERE\s+value\s+=\s+(%s|\?)\s*\)",
            r"(case when \1outcome_domain like '[%]' then (\1outcome_domain)::jsonb else '[]'::jsonb end) @> jsonb_build_array(\2::text)",
            sql,
            flags=re.IGNORECASE
        )
        def replace_outcome_in(match):
            prefix = match.group(1) or ""
            placeholders = match.group(2)
            return f"(case when {prefix}outcome_domain like '[%]' then ({prefix}outcome_domain)::jsonb else '[]'::jsonb end) ?| array[{placeholders}]"
        sql = re.sub(
            r"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\((papers\.)?outcome_domain\)\s+WHERE\s+value\s+IN\s*\(([^)]+)\)\s*\)",
            replace_outcome_in,
            sql,
            flags=re.IGNORECASE
        )
        
        # Convert study_type, exposure_method, cannabis_type checks
        for col in ["study_type", "exposure_method", "cannabis_type"]:
            # Single value check
            pattern_single = rf"\(\(\s*json_valid\((papers\.)?{col}\)\s+AND\s+json_type\(\1{col}\)\s+=\s+'array'\s+AND\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\(\1{col}\)\s+WHERE\s+json_each\.value\s+=\s+(%s|\?)\s*\)\s*\)\s+OR\s+\(\1{col}\s+=\s+(%s|\?)\)\)"
            sql = re.sub(
                pattern_single,
                rf"(case when \1{col} like '[%]' then (\1{col})::jsonb else '[]'::jsonb end @> jsonb_build_array(\2::text) OR \1{col} = \3::text)",
                sql,
                flags=re.IGNORECASE
            )
            # Multi value check (IN)
            pattern_multi = rf"\(\(\s*json_valid\((papers\.)?{col}\)\s+AND\s+json_type\(\1{col}\)\s+=\s+'array'\s+AND\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\(\1{col}\)\s+WHERE\s+json_each\.value\s+IN\s*\(([^)]+)\)\s*\)\s*\)\s+OR\s+\(\1{col}\s+IN\s*\(([^)]+)\)\)\)"
            def replace_multi(match):
                prefix = match.group(1) or ""
                p1 = match.group(2)
                p2 = match.group(3)
                return f"(case when {prefix}{col} like '[%]' then ({prefix}{col})::jsonb else '[]'::jsonb end ?| array[{p1}] OR {prefix}{col} IN ({p2}))"
            sql = re.sub(
                pattern_multi,
                replace_multi,
                sql,
                flags=re.IGNORECASE
            )
            
            # Simple NOT EXISTS checks for tab logic
            pattern_not_exists = rf"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\((papers\.)?{col}\)\s+WHERE\s+json_each\.value\s+IN\s*\(([^)]+)\)\s*\)"
            sql = re.sub(
                pattern_not_exists,
                r"NOT (case when \1" + col + r" like '[%]' then (\1" + col + r")::jsonb else '[]'::jsonb end ?| array[\2])",
                sql,
                flags=re.IGNORECASE
            )
            
            # EXISTS checks for tab logic
            pattern_exists = rf"EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+json_each\((papers\.)?{col}\)\s+WHERE\s+json_each\.value\s+IN\s*\(([^)]+)\)\s*\)"
            sql = re.sub(
                pattern_exists,
                r"(case when \1" + col + r" like '[%]' then (\1" + col + r")::jsonb else '[]'::jsonb end ?| array[\2])",
                sql,
                flags=re.IGNORECASE
            )
            
        return sql

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            return dict(row)
        return None
        
    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(row) for row in rows]
        
    def close(self):
        return self.cursor.close()
        
    @property
    def rowcount(self):
        return self.cursor.rowcount
        
    @property
    def description(self):
        return self.cursor.description
        
    @property
    def lastrowid(self):
        return self.lastrowid_value

class PostgresConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
        
    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())
        
    def commit(self):
        return self.conn.commit()
        
    def rollback(self):
        return self.conn.rollback()
        
    def close(self):
        return self.conn.close()
        
    def executescript(self, script):
        cursor = self.cursor()
        cursor.execute(script)
        self.commit()
        cursor.close()
        
    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    @property
    def row_factory(self):
        return None
        
    @row_factory.setter
    def row_factory(self, value):
        pass

# UI species checkbox values map to study_type labels and optional papers.species tokens.
_SPECIES_FILTER_TARGETS: Dict[str, Dict[str, Any]] = {
    "mouse": {
        "study_types": ["Animal Models (Mouse)"],
        "species_like": ["%mouse%"],
    },
    "rat": {
        "study_types": ["Animal Models (Rat)"],
        "species_like": ["%rat%"],
    },
    "rodent": {
        "study_types": [
            "Animal Models (Mouse)",
            "Animal Models (Rat)",
            "Animal Models (Other Rodents)",
        ],
        "species_like": ["%rodent%", "%rodent_other%", "%mouse%", "%rat%"],
    },
    "hamster": {
        "study_types": ["Animal Models (Other Rodents)"],
        "species_like": ["%hamster%", "%rodent_other%"],
    },
    "guinea pig": {
        "study_types": ["Animal Models (Other Rodents)"],
        "species_like": ["%guinea%", "%rodent_other%"],
    },
    "non-human primate": {
        "study_types": ["Animal Models (Non-Human Primates)"],
        "species_like": ["%non_human_primate%", "%non-human primate%"],
    },
    "rabbit": {
        "study_types": ["Animal Models (Other Rodents)", "Animal Models (Other)"],
        "species_like": ["%rabbit%", "%rodent_other%", "%other_mammal%"],
    },
    "dog": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%dog%", "%other_mammal%"],
    },
    "pig": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%pig%", "%porcine%", "%other_mammal%"],
    },
    "zebrafish": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%zebrafish%", "%vertebrate_non_mammal%"],
    },
}


def _study_type_label_match_clause(label: str) -> Tuple[str, List[Any]]:
    """Return SQL matching a canonical study_type label in JSON or scalar form."""
    clause = (
        "((json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
        "SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value = ?"
        ")) OR (papers.study_type = ?))"
    )
    return clause, [label, label]


def _species_ui_match_clause(species_key: str) -> Tuple[str, List[Any]]:
    """Build species filter SQL for a dashboard checkbox value."""
    normalized = (species_key or "").strip().lower()
    targets = _SPECIES_FILTER_TARGETS.get(normalized)
    if not targets:
        return "LOWER(COALESCE(papers.species, '')) LIKE ?", [f"%{normalized}%"]

    parts: List[str] = []
    params: List[Any] = []
    for pattern in targets.get("species_like", []):
        parts.append("LOWER(COALESCE(papers.species, '')) LIKE ?")
        params.append(pattern)
    for study_label in targets.get("study_types", []):
        clause, clause_params = _study_type_label_match_clause(study_label)
        parts.append(clause)
        params.extend(clause_params)
    return "(" + " OR ".join(parts) + ")", params


TAB_FLAGS_READY_METADATA_KEY = "tab_flags_backfill_complete"


class DatabaseManager:
    """Manages SQLite and PostgreSQL operations, indexing, and dynamic querying for cannabis papers."""
    
    _initialized = False
    _postgres_compat_ready = False
    _tab_flags_ready_cache: Optional[bool] = None
    _tab_columns_exist_cache: Optional[bool] = None
    
    @property
    def is_postgres(self):
        db_url = os.getenv("DATABASE_URL")
        return db_url is not None and (db_url.startswith("postgres://") or db_url.startswith("postgresql://"))
        
    @property
    def database_url(self):
        return os.getenv("DATABASE_URL")
        
    def __init__(self, db_path: str = DATABASE_FILE):
        self.db_path = db_path
            
        # Ensure the parent directory for the database exists (only if SQLite)
        if not self.is_postgres:
            dir_name = os.path.dirname(self.db_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            
        # Check if papers table exists in this DB
        db_exists = False
        if self.is_postgres:
            try:
                if not DatabaseManager._postgres_compat_ready:
                    conn_check = self.get_connection()
                    unwrapped_conn = conn_check.conn
                    cursor = unwrapped_conn.cursor()
                    cursor.execute("""
                        CREATE OR REPLACE FUNCTION json_valid(p_val text)
                        RETURNS boolean AS $$
                        BEGIN
                          IF p_val IS NULL THEN
                            RETURN NULL;
                          END IF;
                          PERFORM p_val::jsonb;
                          RETURN true;
                        EXCEPTION
                          WHEN others THEN
                            RETURN false;
                        END;
                        $$ LANGUAGE plpgsql IMMUTABLE;
                    """)
                    cursor.execute("""
                        CREATE OR REPLACE FUNCTION json_type(p_val text)
                        RETURNS text AS $$
                        DECLARE
                          v_json jsonb;
                        BEGIN
                          IF p_val IS NULL THEN
                            RETURN NULL;
                          END IF;
                          v_json := p_val::jsonb;
                          RETURN jsonb_typeof(v_json);
                        EXCEPTION
                          WHEN others THEN
                            RETURN NULL;
                        END;
                        $$ LANGUAGE plpgsql IMMUTABLE;
                    """)
                    unwrapped_conn.commit()
                    DatabaseManager._postgres_compat_ready = True
                    conn_check.close()
                conn_check = self.get_connection()
                unwrapped_conn = conn_check.conn
                cursor = unwrapped_conn.cursor()
                cursor.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'papers');"
                )
                row = cursor.fetchone()
                db_exists = list(row.values())[0] if isinstance(row, dict) else row[0]

                if db_exists:
                    pass  # FTS GIN index: run via init_db or manual migration only — not on worker boot.

                conn_check.close()
            except Exception as e:
                logger.error(f"Postgres connection check/compat functions failed: {e}")
                db_exists = True
        else:
            if os.path.exists(self.db_path) and os.path.getsize(self.db_path) > 0:
                try:
                    conn_check = sqlite3.connect(self.db_path, timeout=5.0)
                    cursor = conn_check.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='papers';")
                    if cursor.fetchone():
                        db_exists = True
                    conn_check.close()
                except sqlite3.Error:
                    pass

        if not db_exists:
            if not self.is_postgres:
                self.init_db()
            DatabaseManager._initialized = True
        elif not DatabaseManager._initialized:
            if self.is_postgres and db_exists:
                try:
                    self._ensure_postgres_schema_patches()
                except Exception as exc:
                    logger.error("Postgres schema patches failed: %s", exc)
            elif not self.is_postgres:
                self.init_db()
            DatabaseManager._initialized = True

    def _papers_repo(self):
        """Return the SQLAlchemy Core papers repository for this manager."""
        from repository.papers import PapersRepository

        repo = getattr(self, "_papers_repository", None)
        if repo is None:
            repo = PapersRepository(
                db_path=self.db_path,
                tab_columns_exist=self._tab_flag_columns_exist,
            )
            self._papers_repository = repo
        return repo

    def _classify_repo(self):
        """Return the SQLAlchemy Core classify/feedback repository."""
        from repository.classify import ClassifyRepository

        repo = getattr(self, "_classify_repository", None)
        if repo is None:
            repo = ClassifyRepository(db_path=self.db_path)
            self._classify_repository = repo
        return repo

    def get_connection(self, retries: int = 3):
        """DEPRECATED for harvest/classify/catalog search.

        Unmigrated callers (user auth, analyses, heuristics editor, sync scripts)
        still use this wrapper. New request-path code should use ``_papers_repo()``.
        """
        if self.is_postgres:
            if psycopg2 is None:
                raise ImportError("PostgreSQL connection requested but psycopg2 is not installed.")
            url = self.database_url
            last_exc = None
            for attempt in range(max(1, retries)):
                try:
                    conn = psycopg2.connect(
                        url,
                        cursor_factory=psycopg2.extras.RealDictCursor,
                        connect_timeout=int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5")),
                    )
                    return PostgresConnectionWrapper(conn)
                except Exception as exc:
                    last_exc = exc
                    if attempt + 1 >= retries:
                        break
                    time.sleep(0.4 * (attempt + 1))
            raise last_exc
        else:
            conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn

    _PAPERS_PATCH_COLUMNS: Tuple[Tuple[str, str], ...] = (
        ("publication_date", "TEXT"),
        ("cannabis_type", "TEXT"),
        ("summary", "TEXT"),
        ("publication_type", "TEXT"),
        ("expert_locked_fields", "TEXT DEFAULT '[]'"),
        ("classification_confidence", "REAL"),
        ("classification_timestamp", "TEXT"),
        ("classifier_version", "TEXT"),
        ("puff_count", "INTEGER"),
        ("thc_mg_ml", "REAL"),
        ("thc_mg_g", "REAL"),
        ("thc_mg_kg", "REAL"),
        ("cbd_mg_ml", "REAL"),
        ("cbd_mg_g", "REAL"),
        ("cbd_mg_kg", "REAL"),
        ("thc_uM", "REAL"),
        ("cbd_uM", "REAL"),
        ("inhaled_exposure_duration", "TEXT"),
        ("administration_frequency", "TEXT"),
        ("treatment_duration", "TEXT"),
        ("ingestion_status", "TEXT"),
        ("species", "TEXT"),
        ("population_age", "TEXT"),
        ("population_sex", "TEXT"),
        ("inclusion_criteria", "TEXT"),
        ("exclusion_criteria", "TEXT"),
        ("tab_preclinical", "INTEGER DEFAULT 0"),
        ("tab_clinical", "INTEGER DEFAULT 0"),
        ("tab_unclassified_preclinical", "INTEGER DEFAULT 0"),
        ("tab_tangential", "INTEGER DEFAULT 0"),
        ("tab_review", "INTEGER DEFAULT 0"),
    )

    _PAPERS_SEARCH_INDEXES: Tuple[str, ...] = (
        "CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);",
        "CREATE INDEX IF NOT EXISTS idx_papers_citations ON papers(citation_count);",
        "CREATE INDEX IF NOT EXISTS idx_papers_version ON papers(classifier_version);",
        "CREATE INDEX IF NOT EXISTS idx_papers_pubtype ON papers(publication_type);",
        "CREATE INDEX IF NOT EXISTS idx_papers_harvested ON papers(date_harvested);",
        "CREATE INDEX IF NOT EXISTS idx_papers_thc ON papers(thc_pct);",
        "CREATE INDEX IF NOT EXISTS idx_papers_tab_preclinical ON papers(tab_preclinical);",
        "CREATE INDEX IF NOT EXISTS idx_papers_tab_clinical ON papers(tab_clinical);",
        "CREATE INDEX IF NOT EXISTS idx_papers_tab_review ON papers(tab_review);",
        "CREATE INDEX IF NOT EXISTS idx_papers_tab_unclassified ON papers(tab_unclassified_preclinical);",
        "CREATE INDEX IF NOT EXISTS idx_papers_tab_tangential ON papers(tab_tangential);",
    )

    def _ensure_postgres_schema_patches(self) -> None:
        """Apply idempotent column and index migrations on Postgres without full init_db."""
        conn = self.get_connection()
        try:
            for col_name, col_type in self._PAPERS_PATCH_COLUMNS:
                if self.column_exists("papers", col_name, conn):
                    continue
                pg_type = col_type
                if pg_type.startswith("TEXT DEFAULT '[]'"):
                    pg_type = "JSONB DEFAULT '[]'::jsonb"
                elif pg_type == "REAL":
                    pg_type = "DOUBLE PRECISION"
                try:
                    conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {pg_type};")
                    conn.commit()
                except Exception as exc:
                    logger.error("Failed to add papers.%s: %s", col_name, exc)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            conn.commit()

            users_sql = """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    google_id TEXT,
                    is_verified INTEGER DEFAULT 0,
                    verification_code TEXT,
                    created_at TEXT DEFAULT NOW()
                );
            """
            conn.execute(users_sql)
            conn.commit()

            if not self.column_exists("users", "dashboard_preferences", conn):
                try:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN dashboard_preferences JSONB DEFAULT '{}'::jsonb;"
                    )
                    conn.commit()
                except Exception as exc:
                    logger.error("Failed to add users.dashboard_preferences: %s", exc)

            self._ensure_user_profile_columns(conn)

            for idx_stmt in self._PAPERS_SEARCH_INDEXES:
                try:
                    conn.execute(idx_stmt)
                    conn.commit()
                except Exception as exc:
                    logger.debug("Index patch skipped: %s (%s)", idx_stmt, exc)
        finally:
            conn.close()

    def column_exists(self, table_name, column_name, conn):
        try:
            if self.is_postgres:
                unwrapped = conn.conn if hasattr(conn, "conn") else conn
                cursor = unwrapped.cursor()
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s);",
                    (table_name.lower(), column_name.lower())
                )
                row = cursor.fetchone()
                exists = list(row.values())[0] if isinstance(row, dict) else row[0]
                cursor.close()
                return exists
            else:
                cursor = conn.cursor()
                cursor.execute(f"PRAGMA table_info({table_name});")
                columns = [row["name"] for row in cursor.fetchall()]
                cursor.close()
                return column_name in columns
        except Exception:
            return False

    def _ensure_user_profile_columns(self, conn) -> None:
        """Add optional profile columns used by the Settings page."""
        pref_type = "JSONB DEFAULT '{}'::jsonb" if self.is_postgres else "TEXT DEFAULT '{}'"
        migrations = [
            ("last_login_at", "TEXT"),
            ("notification_preferences", pref_type),
        ]
        for column_name, column_type in migrations:
            if self.column_exists("users", column_name, conn):
                continue
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type};")
                conn.commit()
            except Exception as exc:
                logger.error("Failed to add users.%s: %s", column_name, exc)

    def table_exists(self, table_name, conn):
        try:
            if self.is_postgres:
                unwrapped = conn.conn if hasattr(conn, "conn") else conn
                cursor = unwrapped.cursor()
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);",
                    (table_name.lower(),)
                )
                row = cursor.fetchone()
                exists = list(row.values())[0] if isinstance(row, dict) else row[0]
                cursor.close()
                return exists
            else:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table_name,))
                exists = cursor.fetchone() is not None
                cursor.close()
                return exists
        except Exception:
            return False

    def init_db(self):
        """Initializes the database with schema.sql and all required tables/migrations."""
        if not os.path.exists(SCHEMA_FILE):
            raise FileNotFoundError(f"Schema file '{SCHEMA_FILE}' is required for database initialization.")
            
        with open(SCHEMA_FILE, "r") as f:
            schema_script = f.read()

        # Remove CREATE TRIGGER blocks from PostgreSQL schema script before split
        if self.is_postgres:
            schema_script = re.sub(
                r"CREATE\s+TRIGGER\s+.*?BEGIN.*?END\s*;", 
                "", 
                schema_script, 
                flags=re.IGNORECASE | re.DOTALL
            )

        # SQLite FTS migration check
        fts_needs_migration = False
        if not self.is_postgres and os.path.exists(self.db_path):
            try:
                conn_check = sqlite3.connect(self.db_path, timeout=30.0)
                conn_check.row_factory = sqlite3.Row
                cursor = conn_check.cursor()
                cursor.execute("PRAGMA table_info(papers_fts);")
                columns = [row["name"] for row in cursor.fetchall()]
                if columns and "authors" not in columns:
                    fts_needs_migration = True
            except sqlite3.Error:
                pass
            finally:
                if 'conn_check' in locals():
                    conn_check.close()

        conn = self.get_connection()
        try:
            if self.is_postgres:
                statements = []
                for stmt in schema_script.split(";"):
                    stmt_clean = stmt.strip()
                    if not stmt_clean:
                        continue
                    if "CREATE VIRTUAL TABLE" in stmt_clean.upper():
                        continue
                    stmt_clean = re.sub(
                        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", 
                        "SERIAL PRIMARY KEY", 
                        stmt_clean, 
                        flags=re.IGNORECASE
                    )
                    stmt_clean = re.sub(
                        r"AUTOINCREMENT", 
                        "", 
                        stmt_clean, 
                        flags=re.IGNORECASE
                    )
                    statements.append(stmt_clean)

                for stmt in statements:
                    try:
                        conn.execute(stmt)
                    except Exception:
                        pass
                conn.commit()

                # Create functional GIN index for search optimization
                try:
                    conn.execute("SET maintenance_work_mem = '16MB';")
                    conn.execute("DROP INDEX IF EXISTS idx_papers_fts;")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_fts ON papers USING GIN (to_tsvector('english', title || ' ' || coalesce(abstract, '') || ' ' || coalesce(authors, '')));")
                    conn.commit()
                except Exception:
                    pass
            else:
                if fts_needs_migration:
                    conn.execute("DROP TRIGGER IF EXISTS papers_ai;")
                    conn.execute("DROP TRIGGER IF EXISTS papers_ad;")
                    conn.execute("DROP TRIGGER IF EXISTS papers_au;")
                    conn.execute("DROP TABLE IF EXISTS papers_fts;")
                    conn.commit()

                conn.executescript(schema_script)

                if fts_needs_migration:
                    conn.execute("INSERT INTO papers_fts(rowid, title, abstract, authors) SELECT id, title, abstract, authors FROM papers;")
                    conn.commit()

            # Ensure columns exist in papers table (dynamic migration)
            columns_to_add = [
                ("publication_date", "TEXT"),
                ("cannabis_type", "TEXT"),
                ("summary", "TEXT"),
                ("publication_type", "TEXT"),
                ("expert_locked_fields", "TEXT DEFAULT '[]'"),
                ("classification_confidence", "REAL"),
                ("classification_timestamp", "TEXT"),
                ("classifier_version", "TEXT"),
                ("puff_count", "INTEGER"),
                ("thc_mg_ml", "REAL"),
                ("thc_mg_g", "REAL"),
                ("thc_mg_kg", "REAL"),
                ("cbd_mg_ml", "REAL"),
                ("cbd_mg_g", "REAL"),
                ("cbd_mg_kg", "REAL"),
                ("thc_uM", "REAL"),
                ("cbd_uM", "REAL"),
                ("inhaled_exposure_duration", "TEXT"),
                ("administration_frequency", "TEXT"),
                ("treatment_duration", "TEXT"),
                ("ingestion_status", "TEXT"),
                ("species", "TEXT"),
                ("population_age", "TEXT"),
                ("population_sex", "TEXT"),
                ("inclusion_criteria", "TEXT"),
                ("exclusion_criteria", "TEXT"),
                ("tab_preclinical", "INTEGER DEFAULT 0"),
                ("tab_clinical", "INTEGER DEFAULT 0"),
                ("tab_unclassified_preclinical", "INTEGER DEFAULT 0"),
                ("tab_tangential", "INTEGER DEFAULT 0"),
                ("tab_review", "INTEGER DEFAULT 0"),
            ]
            
            for col_name, col_type in columns_to_add:
                if not self.column_exists("papers", col_name, conn):
                    pg_type = col_type
                    if self.is_postgres:
                        if pg_type.startswith("TEXT DEFAULT '[]'"):
                            pg_type = "JSONB DEFAULT '[]'::jsonb"
                        elif pg_type == "REAL":
                            pg_type = "DOUBLE PRECISION"
                    try:
                        conn.execute(f"ALTER TABLE papers ADD COLUMN {col_name} {pg_type};")
                        conn.commit()
                    except Exception as e:
                        logger.error(f"Failed to add column {col_name}: {e}")
                        pass

            # Ensure system_metadata table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            conn.commit()

            # Ensure users table exists
            users_sql = """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    google_id TEXT,
                    is_verified INTEGER DEFAULT 0,
                    verification_code TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """
            if self.is_postgres:
                users_sql = users_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                users_sql = users_sql.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT NOW()")
            conn.execute(users_sql)
            conn.commit()

            if not self.column_exists("users", "dashboard_preferences", conn):
                pref_type = "JSONB DEFAULT '{}'::jsonb" if self.is_postgres else "TEXT DEFAULT '{}'"
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN dashboard_preferences {pref_type};")
                    conn.commit()
                except Exception as e:
                    logger.error(f"Failed to add users.dashboard_preferences column: {e}")

            self._ensure_user_profile_columns(conn)

            # Ensure analyses table exists
            self.init_analyses_table()

            # Ensure citation_edges table exists
            citation_edges_sql = """
                CREATE TABLE IF NOT EXISTS citation_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_paper_id INTEGER NOT NULL,
                    target_paper_id INTEGER,
                    target_external_id TEXT,
                    target_title TEXT,
                    target_year INTEGER,
                    relationship TEXT NOT NULL DEFAULT 'cites',
                    confidence TEXT DEFAULT 'medium',
                    source TEXT DEFAULT 'semantic_scholar',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY(source_paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_paper_id) REFERENCES papers(id) ON DELETE SET NULL
                );
            """
            if self.is_postgres:
                citation_edges_sql = citation_edges_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                citation_edges_sql = citation_edges_sql.replace("DEFAULT (datetime('now'))", "DEFAULT NOW()")
                citation_edges_sql = citation_edges_sql.replace("metadata TEXT DEFAULT '{}'", "metadata JSONB DEFAULT '{}'::jsonb")
            conn.execute(citation_edges_sql)
            conn.commit()

            # Create citation_edges indexes
            for idx_stmt in [
                "CREATE INDEX IF NOT EXISTS idx_ce_source ON citation_edges(source_paper_id);",
                "CREATE INDEX IF NOT EXISTS idx_ce_target ON citation_edges(target_paper_id);",
                "CREATE INDEX IF NOT EXISTS idx_ce_rel ON citation_edges(relationship);",
                "CREATE INDEX IF NOT EXISTS idx_ce_ext ON citation_edges(target_external_id);"
            ]:
                try:
                    conn.execute(idx_stmt)
                    conn.commit()
                except Exception:
                    pass

            # Create papers indexes for search optimization
            for idx_stmt in [
                "CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);",
                "CREATE INDEX IF NOT EXISTS idx_papers_citations ON papers(citation_count);",
                "CREATE INDEX IF NOT EXISTS idx_papers_version ON papers(classifier_version);",
                "CREATE INDEX IF NOT EXISTS idx_papers_pubtype ON papers(publication_type);",
                "CREATE INDEX IF NOT EXISTS idx_papers_harvested ON papers(date_harvested);",
                "CREATE INDEX IF NOT EXISTS idx_papers_thc ON papers(thc_pct);",
                "CREATE INDEX IF NOT EXISTS idx_papers_tab_preclinical ON papers(tab_preclinical);",
                "CREATE INDEX IF NOT EXISTS idx_papers_tab_clinical ON papers(tab_clinical);",
                "CREATE INDEX IF NOT EXISTS idx_papers_tab_review ON papers(tab_review);",
                "CREATE INDEX IF NOT EXISTS idx_papers_tab_unclassified ON papers(tab_unclassified_preclinical);",
                "CREATE INDEX IF NOT EXISTS idx_papers_tab_tangential ON papers(tab_tangential);",
            ]:
                try:
                    conn.execute(idx_stmt)
                    conn.commit()
                except Exception:
                    pass

            # Ensure llm_calls_log table exists
            llm_calls_sql = """
                CREATE TABLE IF NOT EXISTS llm_calls_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id INTEGER,
                    timestamp TEXT,
                    model TEXT,
                    input_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_write_tokens INTEGER,
                    output_tokens INTEGER,
                    cost REAL,
                    few_shot_similarity REAL,
                    few_shot_count INTEGER,
                    classification_confidence REAL,
                    classifier_version TEXT,
                    batch_id TEXT,
                    bm25_retrieval_used INTEGER DEFAULT 0,
                    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE SET NULL
                );
            """
            if self.is_postgres:
                llm_calls_sql = llm_calls_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                llm_calls_sql = llm_calls_sql.replace("cost REAL", "cost DOUBLE PRECISION")
                llm_calls_sql = llm_calls_sql.replace("classification_confidence REAL", "classification_confidence DOUBLE PRECISION")
            conn.execute(llm_calls_sql)
            conn.commit()

            self._ensure_rl_learning_tables(conn)

            # Populate publication_date for existing rows using year (only needed for SQLite migrations)
            if not self.is_postgres:
                try:
                    conn.execute("UPDATE papers SET publication_date = year || '-01-01' WHERE publication_date IS NULL AND year IS NOT NULL;")
                    conn.commit()
                except Exception:
                    pass

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise RuntimeError(f"Failed to initialize database: {e}")
        finally:
            conn.close()

    def clear_all_tables(self):
        """Clears all tables in the database (useful for testing)."""
        conn = self.get_connection()
        try:
            if self.is_postgres:
                # Truncate all tables and restart sequences
                cursor = conn.cursor()
                cursor.execute("TRUNCATE TABLE papers, users, analyses, system_metadata RESTART IDENTITY CASCADE;")
                conn.commit()
                cursor.close()
            else:
                # For SQLite, we can drop and recreate the file or delete all rows
                cursor = conn.cursor()
                cursor.execute("PRAGMA foreign_keys = OFF;")
                cursor.execute("DELETE FROM papers;")
                cursor.execute("DELETE FROM users;")
                cursor.execute("DELETE FROM analyses;")
                cursor.execute("DELETE FROM system_metadata;")
                cursor.execute("DELETE FROM citation_edges;")
                cursor.execute("DELETE FROM llm_calls_log;")
                cursor.execute("DELETE FROM feedback_audit;")
                cursor.execute("DELETE FROM optimization_log;")
                try:
                    cursor.execute("DELETE FROM feedback_audit_fts;")
                except Exception:
                    pass
                cursor.execute("DELETE FROM sqlite_sequence;")
                cursor.execute("PRAGMA foreign_keys = ON;")
                conn.commit()
                cursor.close()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.error(f"Failed to clear database tables: {e}")
            raise e
        finally:
            conn.close()

    def _ensure_rl_learning_tables(self, conn) -> None:
        """Creates RL learning tables/columns used for BM25 retrieval and optimization logging."""
        optimization_log_sql = """
            CREATE TABLE IF NOT EXISTS optimization_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                field_group_scores TEXT,
                reward REAL,
                gate_passed INTEGER DEFAULT 0,
                failed_attempts INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                patch_summary TEXT,
                rules_version_before TEXT,
                rules_version_after TEXT
            );
        """
        if self.is_postgres:
            optimization_log_sql = optimization_log_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            optimization_log_sql = optimization_log_sql.replace("field_group_scores TEXT", "field_group_scores JSONB")
            optimization_log_sql = optimization_log_sql.replace("patch_summary TEXT", "patch_summary JSONB")
        conn.execute(optimization_log_sql)
        conn.commit()

        if not self.column_exists("llm_calls_log", "bm25_retrieval_used", conn):
            conn.execute("ALTER TABLE llm_calls_log ADD COLUMN bm25_retrieval_used INTEGER DEFAULT 0;")
            conn.commit()

        if self.is_postgres:
            if not self.table_exists("feedback_audit", conn):
                return
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_feedback_audit_search
                    ON feedback_audit USING GIN (
                        to_tsvector(
                            'english',
                            coalesce(title, '') || ' ' || coalesce(abstract, '') || ' ' ||
                            coalesce(field_name, '') || ' ' || coalesce(old_value, '') || ' ' || coalesce(new_value, '')
                        )
                    );
                    """
                )
                conn.commit()
            except Exception:
                pass
            return

        if not self.table_exists("feedback_audit", conn):
            return

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS feedback_audit_fts USING fts5(
                title,
                abstract,
                field_name,
                correction_text,
                tokenize='porter'
            );
            """
        )
        conn.commit()

        for trigger_sql in [
            "DROP TRIGGER IF EXISTS feedback_audit_ai;",
            """
            CREATE TRIGGER feedback_audit_ai AFTER INSERT ON feedback_audit BEGIN
                INSERT INTO feedback_audit_fts(
                    rowid, title, abstract, field_name, correction_text
                ) VALUES (
                    new.id,
                    coalesce(new.title, ''),
                    coalesce(new.abstract, ''),
                    coalesce(new.field_name, ''),
                    coalesce(new.field_name, '') || ' ' || coalesce(new.old_value, '') || ' -> ' || coalesce(new.new_value, '')
                );
            END;
            """,
            "DROP TRIGGER IF EXISTS feedback_audit_ad;",
            """
            CREATE TRIGGER feedback_audit_ad AFTER DELETE ON feedback_audit BEGIN
                INSERT INTO feedback_audit_fts(
                    feedback_audit_fts, rowid, title, abstract, field_name, correction_text
                ) VALUES (
                    'delete', old.id, old.title, old.abstract, old.field_name, ''
                );
            END;
            """,
        ]:
            try:
                conn.execute(trigger_sql)
                conn.commit()
            except Exception:
                pass

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total FROM feedback_audit_fts;")
            row = cursor.fetchone()
            fts_count = row["total"] if isinstance(row, dict) else row[0]
            cursor.execute("SELECT COUNT(*) AS total FROM feedback_audit;")
            row = cursor.fetchone()
            audit_count = row["total"] if isinstance(row, dict) else row[0]
            if fts_count == 0 and audit_count > 0:
                cursor.execute(
                    """
                    INSERT INTO feedback_audit_fts(rowid, title, abstract, field_name, correction_text)
                    SELECT
                        id,
                        coalesce(title, ''),
                        coalesce(abstract, ''),
                        coalesce(field_name, ''),
                        coalesce(field_name, '') || ' ' || coalesce(old_value, '') || ' -> ' || coalesce(new_value, '')
                    FROM feedback_audit;
                    """
                )
                conn.commit()
        except Exception:
            pass

    def insert_feedback_audit(
        self,
        paper_id: int,
        field_name: str,
        old_value: Optional[str],
        new_value: Optional[str],
        title: Optional[str],
        abstract: Optional[str],
        timestamp: str,
        confidence_before_review: Optional[float] = None,
        classifier_version: Optional[str] = None,
        cursor=None,
    ) -> int:
        """Inserts a feedback audit row and keeps feedback_audit_fts synchronized on SQLite."""
        sql = """
            INSERT INTO feedback_audit (
                paper_id, field_name, old_value, new_value, title, abstract,
                timestamp, confidence_before_review, classifier_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            paper_id,
            field_name,
            old_value,
            new_value,
            title,
            abstract,
            timestamp,
            confidence_before_review,
            classifier_version,
        )
        if cursor is not None:
            cursor.execute(sql, params)
            row_id = cursor.lastrowid
        else:
            conn = self.get_connection()
            try:
                cur = conn.cursor()
                cur.execute(sql, params)
                row_id = cur.lastrowid
                conn.commit()
            finally:
                conn.close()
        return int(row_id)

    @staticmethod
    def build_bm25_query(text: str, max_terms: int = 8) -> str:
        """Builds an FTS-friendly OR query from free text for correction retrieval."""
        from repository.classify import build_bm25_query
        return build_bm25_query(text, max_terms=max_terms)

    def search_feedback_corrections_bm25(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieves feedback audit rows ranked by BM25/full-text relevance to a query."""
        return self._classify_repo().search_feedback_corrections_bm25(query_text, limit=limit)

    def get_feedback_audit_for_paper(self, paper_id: int) -> List[Dict[str, Any]]:
        """Returns all feedback audit field corrections for one paper."""
        return self._classify_repo().get_feedback_audit_for_paper(paper_id)

    def fetch_feedback_audit_since(
        self,
        since_ts: str,
        *,
        expert_drawer_only: bool = True,
        paper_ids: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Returns feedback_audit rows after a timestamp (expert drawer edits by default).

        When expert_drawer_only is True, excludes auto-calibration rows (field_name LIKE 'maude:%').
        Does not filter by paper classifier_version — Maude, LLM, and heuristic papers are included.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        clauses = ["timestamp > ?"]
        params: List[Any] = [since_ts]
        if expert_drawer_only:
            clauses.append("field_name NOT LIKE 'maude:%'")
        if paper_ids:
            placeholders = ", ".join("?" for _ in paper_ids)
            clauses.append(f"paper_id IN ({placeholders})")
            params.extend(int(pid) for pid in paper_ids)
        sql = f"""
            SELECT id, paper_id, field_name, old_value, new_value, title, abstract,
                   timestamp, confidence_before_review, classifier_version
            FROM feedback_audit
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp ASC, id ASC
        """
        try:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def count_expert_edits_since(
        self,
        since_ts: str,
        *,
        expert_drawer_only: bool = True,
    ) -> int:
        """Counts distinct expert field corrections since a timestamp."""
        conn = self.get_connection()
        cursor = conn.cursor()
        clauses = ["timestamp > ?"]
        params: List[Any] = [since_ts]
        if expert_drawer_only:
            clauses.append("field_name NOT LIKE 'maude:%'")
        sql = f"""
            SELECT COUNT(*) AS total
            FROM feedback_audit
            WHERE {' AND '.join(clauses)}
        """
        try:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
            if not row:
                return 0
            try:
                return int(row["total"])
            except Exception:
                return int(row[0])
        finally:
            conn.close()

    def insert_optimization_log(
        self,
        run_id: str,
        field_group_scores: Dict[str, Any],
        reward: float,
        gate_passed: bool,
        failed_attempts: int,
        status: str,
        patch_summary: Optional[Dict[str, Any]] = None,
        rules_version_before: Optional[str] = None,
        rules_version_after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persists one optimization run with field-group Hamming breakdown."""
        timestamp = datetime.now().isoformat()
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO optimization_log (
                    run_id, timestamp, field_group_scores, reward, gate_passed,
                    failed_attempts, status, patch_summary, rules_version_before, rules_version_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    timestamp,
                    json.dumps(field_group_scores),
                    reward,
                    1 if gate_passed else 0,
                    failed_attempts,
                    status,
                    json.dumps(patch_summary or {}),
                    rules_version_before,
                    rules_version_after,
                ),
            )
            conn.commit()
            return {"id": cursor.lastrowid, "run_id": run_id, "status": status}
        finally:
            conn.close()

    def get_metadata(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Fetches a metadata value from the database."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_metadata WHERE key = ?;", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return row["value"]
                except Exception:
                    return row[0]
            return default
        except Exception:
            return default
        finally:
            conn.close()

    def set_metadata(self, key: str, value: str):
        """Sets a metadata value in the database, overwriting if already exists."""
        conn = self.get_connection()
        try:
            if self.is_postgres:
                conn.execute(
                    "INSERT INTO system_metadata (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;", 
                    (key, value)
                )
            else:
                conn.execute("INSERT OR REPLACE INTO system_metadata (key, value) VALUES (?, ?);", (key, value))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Failed to set metadata key '{key}': {e}")
        finally:
            conn.close()

    def increment_metadata(self, key: str, amount: int = 1) -> int:
        """Increments an integer metadata value and returns the updated value."""
        current_raw = self.get_metadata(key, "0")
        try:
            current = int(current_raw or 0)
        except (TypeError, ValueError):
            current = 0
        updated = current + amount
        self.set_metadata(key, str(updated))
        return updated

    def count_low_confidence_papers(self, confidence_max: float) -> int:
        """Counts unlocked papers whose classification confidence is at or below a threshold."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM papers
                WHERE classification_confidence IS NOT NULL
                  AND classification_confidence <= ?
                  AND (
                    expert_locked_fields IS NULL
                    OR expert_locked_fields = ''
                    OR expert_locked_fields = '[]'
                  )
                """,
                (confidence_max,)
            )
            row = cursor.fetchone()
            return row["total"] if row else 0
        finally:
            conn.close()

    def get_low_confidence_papers(self, confidence_max: float, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns unlocked low-confidence papers for expert or agent review."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id, pmid, doi, title, abstract, study_type, exposure_method,
                    cannabis_type, outcome_domain, publication_type,
                    classification_confidence, classification_timestamp,
                    classifier_version, expert_locked_fields
                FROM papers
                WHERE classification_confidence IS NOT NULL
                  AND classification_confidence <= ?
                  AND (
                    expert_locked_fields IS NULL
                    OR expert_locked_fields = ''
                    OR expert_locked_fields = '[]'
                  )
                ORDER BY classification_confidence ASC, classification_timestamp DESC, id DESC
                LIMIT ?
                """,
                (confidence_max, limit)
            )
            results = []
            for row in cursor.fetchall():
                paper = dict(row)
                for json_field in ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "expert_locked_fields"]:
                    if paper.get(json_field):
                        try:
                            parsed = json.loads(paper[json_field])
                            if isinstance(parsed, list):
                                paper[json_field] = parsed
                        except Exception:
                            pass
                    elif json_field == "expert_locked_fields":
                        paper[json_field] = []
                results.append(paper)
            return results
        finally:
            conn.close()

    def get_recent_feedback(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recent expert feedback audit rows for agent context."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    id, paper_id, field_name, old_value, new_value, title, abstract,
                    timestamp, confidence_before_review, classifier_version
                FROM feedback_audit
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_feedback_loop_metrics(self) -> Dict[str, Any]:
        """Returns feedback audit counters and eval-threshold progress for the learning dashboard."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total FROM feedback_audit")
            total_row = cursor.fetchone()
            total_corrections = int(list(total_row.values())[0] if hasattr(total_row, "keys") else total_row[0])

            cursor.execute("SELECT COUNT(DISTINCT paper_id) AS papers FROM feedback_audit")
            papers_row = cursor.fetchone()
            unique_papers = int(list(papers_row.values())[0] if hasattr(papers_row, "keys") else papers_row[0])

            cursor.execute(
                """
                SELECT field_name, COUNT(*) AS count
                FROM feedback_audit
                GROUP BY field_name
                ORDER BY count DESC
                LIMIT 8
                """
            )
            by_field = {
                row["field_name"] if hasattr(row, "keys") else row[0]: int(
                    row["count"] if hasattr(row, "keys") else row[1]
                )
                for row in cursor.fetchall()
            }

            fts_ready = self.table_exists("feedback_audit_fts", conn)
            if self.is_postgres and not fts_ready:
                fts_ready = self.table_exists("feedback_audit", conn)

            return {
                "total_corrections": total_corrections,
                "unique_papers_corrected": unique_papers,
                "corrections_by_field": by_field,
                "corrections_since_eval": int(self.get_metadata("feedback_corrections_since_eval", "0") or 0),
                "last_feedback_timestamp": self.get_metadata("last_feedback_audit_timestamp"),
                "last_reliability_eval_timestamp": self.get_metadata("last_reliability_eval_timestamp"),
                "fts_index_ready": fts_ready,
            }
        except Exception:
            return {
                "total_corrections": 0,
                "unique_papers_corrected": 0,
                "corrections_by_field": {},
                "corrections_since_eval": int(self.get_metadata("feedback_corrections_since_eval", "0") or 0),
                "last_feedback_timestamp": self.get_metadata("last_feedback_audit_timestamp"),
                "last_reliability_eval_timestamp": self.get_metadata("last_reliability_eval_timestamp"),
                "fts_index_ready": False,
            }
        finally:
            conn.close()

    def get_optimization_log_metrics(self, limit: int = 25) -> Dict[str, Any]:
        """Returns optimization_log summary including Hamming scores and escalation status."""
        conn = self.get_connection()
        try:
            if not self.table_exists("optimization_log", conn):
                return {"total_runs": 0, "by_status": {}, "needs_human_review_count": 0, "recent_runs": []}

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total FROM optimization_log")
            total_row = cursor.fetchone()
            total_runs = int(list(total_row.values())[0] if hasattr(total_row, "keys") else total_row[0])

            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM optimization_log
                GROUP BY status
                """
            )
            by_status = {}
            for row in cursor.fetchall():
                if hasattr(row, "keys"):
                    by_status[str(row["status"])] = int(row["count"])
                else:
                    by_status[str(row[0])] = int(row[1])

            cursor.execute(
                """
                SELECT
                    id, run_id, timestamp, field_group_scores, reward, gate_passed,
                    failed_attempts, status, rules_version_before, rules_version_after
                FROM optimization_log
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            recent_runs = []
            for row in cursor.fetchall():
                record = dict(row)
                raw_scores = record.get("field_group_scores")
                if isinstance(raw_scores, str):
                    try:
                        record["field_group_scores"] = json.loads(raw_scores)
                    except Exception:
                        record["field_group_scores"] = {}
                recent_runs.append(record)

            return {
                "total_runs": total_runs,
                "by_status": by_status,
                "needs_human_review_count": by_status.get("needs_human_review", 0),
                "recent_runs": recent_runs,
            }
        except Exception:
            return {"total_runs": 0, "by_status": {}, "needs_human_review_count": 0, "recent_runs": []}
        finally:
            conn.close()

    def get_bm25_propagation_timeline(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns BM25 few-shot retrieval usage aggregated by calibration batch."""
        conn = self.get_connection()
        try:
            if not self.table_exists("llm_calls_log", conn):
                return []
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    batch_id,
                    MIN(timestamp) AS first_call,
                    COUNT(*) AS call_count,
                    SUM(CASE WHEN bm25_retrieval_used = 1 THEN 1 ELSE 0 END) AS bm25_used_count,
                    AVG(few_shot_similarity) AS avg_few_shot_similarity,
                    AVG(classification_confidence) AS avg_confidence
                FROM llm_calls_log
                WHERE batch_id IS NOT NULL AND batch_id != ''
                GROUP BY batch_id
                ORDER BY first_call ASC
                LIMIT ?
                """,
                (limit,),
            )
            timeline = []
            for row in cursor.fetchall():
                record = dict(row)
                call_count = int(record.get("call_count") or 0)
                bm25_used = int(record.get("bm25_used_count") or 0)
                record["bm25_usage_rate"] = round(bm25_used / call_count, 3) if call_count else 0.0
                timeline.append(record)
            return timeline
        except Exception:
            return []
        finally:
            conn.close()

    def insert_paper(self, paper: Dict[str, Any], *, force_id: Optional[int] = None) -> int:
        """Insert or update a paper via the backend-neutral repository layer."""
        return self._papers_repo().insert_paper(paper, force_id=force_id)

    def find_paper_id_by_title(self, title: str) -> Optional[int]:
        """Return the paper id for an exact title match (case-insensitive), if any."""
        return self._papers_repo().find_paper_id_by_title(title)

    def find_fuzzy_paper_by_title(
        self,
        title: str,
        *,
        min_ratio: float = 0.82,
    ) -> Tuple[Optional[int], float]:
        """Return (paper_id, similarity) for the best title match at or above min_ratio."""
        return self._papers_repo().find_fuzzy_paper_by_title(title, min_ratio=min_ratio)

    def find_top_title_matches(
        self,
        title: str,
        *,
        limit: int = 5,
        min_ratio: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """Return up to `limit` candidate papers ranked by title similarity."""
        return self._papers_repo().find_top_title_matches(title, limit=limit, min_ratio=min_ratio)

    def search_papers_minimal_for_section_stats(
        self,
        filters: Dict[str, Any],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return lightweight rows for section-stats (optional hard cap)."""
        return self._papers_repo().search_papers_minimal_for_section_stats(filters, limit=limit)

    def search_papers_by_ids(self, paper_ids: List[Any]) -> List[Dict[str, Any]]:
        """Return list-column rows for the given paper ids, preserving id order."""
        return self._papers_repo().search_papers_by_ids(paper_ids)

    def log_llm_call(self, paper_id: Optional[int], metrics: Dict[str, Any], batch_id: Optional[str] = None, cursor = None):
        """Logs an LLM API call's token usage, model, and cost to the database."""
        timestamp = datetime.now().isoformat()
        model = metrics.get("model", "unknown")
        input_tokens = metrics.get("input_tokens", 0)
        cache_read = metrics.get("cache_read_tokens", 0)
        cache_write = metrics.get("cache_write_tokens", 0)
        output_tokens = metrics.get("output_tokens", 0)
        cost = metrics.get("cost", 0.0)
        few_shot_similarity = metrics.get("few_shot_similarity", 0.0)
        few_shot_count = metrics.get("few_shot_count", 0)
        bm25_retrieval_used = int(metrics.get("bm25_retrieval_used", 0) or 0)
        classification_confidence = metrics.get("classification_confidence", 0.0)
        classifier_version = metrics.get("classifier_version", "1.0.0")

        sql = """
            INSERT INTO llm_calls_log (
                paper_id, timestamp, model, input_tokens, cache_read_tokens, cache_write_tokens,
                output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence,
                classifier_version, batch_id, bm25_retrieval_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            paper_id, timestamp, model, input_tokens, cache_read, cache_write,
            output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence,
            classifier_version, batch_id, bm25_retrieval_used,
        )

        if cursor:
            cursor.execute(sql, params)
        else:
            conn = self.get_connection()
            try:
                conn.execute(sql, params)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"[DB ERROR] Failed to log LLM call: {e}")
            finally:
                conn.close()

    def get_paper(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a single paper by its database ID."""
        return self._papers_repo().get_paper(paper_id)

    def delete_paper(self, paper_id: int) -> bool:
        """Deletes a paper by database ID."""
        return self._papers_repo().delete_paper(paper_id)

    @staticmethod
    def clean_fts_query(query: str) -> str:
        """Cleans and sanitizes a query string for SQLite FTS5 / Postgres websearch."""
        from repository.filters import clean_fts_query
        return clean_fts_query(query)

    def _build_filter_clauses(self, filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
        """Build SQL WHERE clauses via the dialect-aware repository filter layer."""
        return self._papers_repo().build_filter_clauses(filters)

    def search_papers(
        self,
        filters: Dict[str, Any],
        include_total: bool = False,
    ):
        """Query the catalog using backend-neutral repository SQL."""
        return self._papers_repo().search_papers(filters, include_total=include_total)

    def search_papers_for_analysis(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Returns all papers matching analysis filter settings."""
        return self._papers_repo().search_papers_for_analysis(filters)

    def count_papers(self, filters: Dict[str, Any]) -> int:
        """Counts total papers matching the filters."""
        return self._papers_repo().count_papers(filters)

    def get_tab_counts(self) -> Dict[str, int]:
        """Return paper counts for each primary dashboard tab using indexed tab SQL."""
        return self._papers_repo().get_tab_counts()

    def get_all_pmids(self) -> set:
        """Returns a set of all PMIDs currently stored in the database for skip checks."""
        return self._papers_repo().get_all_pmids()

    def hash_password(self, password: str) -> str:
        import hashlib
        import os
        salt = os.urandom(16).hex()
        pwd_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
        return f"{salt}:{pwd_hash}"

    def check_password(self, password: str, stored_hash: str) -> bool:
        import hashlib
        if not stored_hash or ":" not in stored_hash:
            return False
        salt, pwd_hash = stored_hash.split(":", 1)
        test_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
        return test_hash == pwd_hash

    def record_user_login(self, user_id: int) -> None:
        """Stamp the user's last successful login time."""
        conn = self.get_connection()
        try:
            self._ensure_user_profile_columns(conn)
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?;",
                (datetime.now().isoformat(timespec="seconds"), int(user_id)),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to record last login for user %s: %s", user_id, exc)
        finally:
            conn.close()

    def update_user_password(self, user_id: int, new_password_hash: str) -> bool:
        """Update password hash for a manual (non-Google-only) account."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET password_hash = ? WHERE id = ? AND password_hash IS NOT NULL;",
                (new_password_hash, int(user_id)),
            )
            conn.commit()
            return (cursor.rowcount or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def update_username(self, user_id: int, new_username: str) -> bool:
        """Rename a user when the username is still available."""
        username = (new_username or "").strip()
        if not username:
            return False
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET username = ? WHERE id = ?;",
                (username, int(user_id)),
            )
            conn.commit()
            return (cursor.rowcount or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def get_user_notification_preferences(self, user_id: int) -> Dict[str, Any]:
        """Return parsed notification preferences for a user."""
        user = self.get_user_by_id(int(user_id))
        if not user:
            return {}
        raw = user.get("notification_preferences")
        if raw in (None, ""):
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def set_user_notification_preferences(self, user_id: int, preferences: Dict[str, Any]) -> bool:
        """Persist notification preferences JSON for a user."""
        conn = self.get_connection()
        try:
            self._ensure_user_profile_columns(conn)
            payload = json.dumps(preferences or {})
            conn.execute(
                "UPDATE users SET notification_preferences = ? WHERE id = ?;",
                (payload, int(user_id)),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.error("Failed to save notification preferences for user %s: %s", user_id, exc)
            return False
        finally:
            conn.close()

    def list_verified_users_for_notifications(self) -> List[Dict[str, Any]]:
        """Return verified users that may receive notification digests."""
        conn = self.get_connection()
        try:
            self._ensure_user_profile_columns(conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, username, email, notification_preferences, is_verified "
                "FROM users WHERE is_verified = 1;"
            )
            rows = cursor.fetchall() or []
            out = []
            for row in rows:
                out.append(dict(row) if not isinstance(row, dict) else row)
            return out
        except Exception as exc:
            logger.error("Failed to list users for notifications: %s", exc)
            return []
        finally:
            conn.close()

    def delete_user_account(self, user_id: int) -> bool:
        """Permanently delete a user and their saved analyses."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            if self.table_exists("analyses", conn):
                cursor.execute("DELETE FROM analyses WHERE user_id = ?;", (int(user_id),))
            cursor.execute("DELETE FROM users WHERE id = ?;", (int(user_id),))
            conn.commit()
            return (cursor.rowcount or 0) > 0
        except Exception as exc:
            logger.error("Failed to delete user %s: %s", user_id, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

    def count_user_analyses(self, user_id: int) -> int:
        """Return how many saved analyses belong to a user."""
        conn = self.get_connection()
        try:
            if not self.table_exists("analyses", conn):
                return 0
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total FROM analyses WHERE user_id = ?;", (int(user_id),))
            row = cursor.fetchone()
            if row is None:
                return 0
            if isinstance(row, dict):
                return int(row.get("total") or 0)
            return int(row[0] or 0)
        except Exception:
            return 0
        finally:
            conn.close()

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetches a user by primary key id."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_dashboard_preferences(self, user_id: int) -> Dict[str, Any]:
        """Return parsed dashboard UI preferences for a user, or an empty dict."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            if not self.column_exists("users", "dashboard_preferences", conn):
                return {}
            cursor = conn.cursor()
            cursor.execute("SELECT dashboard_preferences FROM users WHERE id = ?;", (user_id,))
            row = cursor.fetchone()
            if not row:
                return {}
            raw = row["dashboard_preferences"]
            if raw in (None, ""):
                return {}
            if isinstance(raw, dict):
                return raw
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        finally:
            conn.close()

    def set_user_dashboard_preferences(self, user_id: int, preferences: Dict[str, Any]) -> bool:
        """Persist dashboard UI preferences JSON for a user."""
        conn = self.get_connection()
        try:
            payload = json.dumps(preferences or {})
            conn.execute(
                "UPDATE users SET dashboard_preferences = ? WHERE id = ?;",
                (payload, user_id),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save dashboard preferences for user {user_id}: {e}")
            return False
        finally:
            conn.close()

    def get_user_by_username_or_email(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Fetches a user by username or email."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?;", (identifier, identifier))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Fetch a user by exact email (case-insensitive)."""
        normalized = (email or "").strip()
        if not normalized:
            return None
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE LOWER(email) = LOWER(?) LIMIT 1;",
                (normalized,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch a user by exact username (case-insensitive)."""
        normalized = (username or "").strip()
        if not normalized:
            return None
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM users WHERE LOWER(username) = LOWER(?) LIMIT 1;",
                (normalized,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a user by google_id."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE google_id = ?;", (google_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create_user(self, username: str, email: str, password_hash: Optional[str] = None, google_id: Optional[str] = None, is_verified: int = 0, verification_code: Optional[str] = None) -> bool:
        """Creates a new user in the database."""
        conn = self.get_connection()
        try:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, google_id, is_verified, verification_code) VALUES (?, ?, ?, ?, ?, ?);",
                (username, email, password_hash, google_id, is_verified, verification_code)
            )
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def refresh_unverified_signup(
        self,
        user_id: int,
        *,
        username: str,
        password_hash: str,
        verification_code: str,
    ) -> bool:
        """Replace credentials on an unverified, non-Google pending signup."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET username = ?, password_hash = ?, verification_code = ?, "
                "is_verified = 0, google_id = NULL WHERE id = ? AND is_verified = 0 "
                "AND (google_id IS NULL OR google_id = '');",
                (username, password_hash, verification_code, int(user_id)),
            )
            conn.commit()
            return (cursor.rowcount or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    def verify_user(self, username: str) -> bool:
        """Marks a user as verified."""
        conn = self.get_connection()
        try:
            conn.execute("UPDATE users SET is_verified = 1, verification_code = NULL WHERE username = ?;", (username,))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def set_verification_code(self, username: str, code: str) -> bool:
        """Store a new email verification code for an unverified user."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET verification_code = ? WHERE username = ? AND is_verified = 0;",
                (code, username),
            )
            conn.commit()
            return (cursor.rowcount or 0) > 0
        except Exception:
            return False
        finally:
            conn.close()

    # ─── Analyses Table ──────────────────────────────────────────

    def init_analyses_table(self):
        """Creates the analyses table if it does not exist, migrates if needed."""
        conn = self.get_connection()
        try:
            # Check if table exists with the right columns
            required_cols = {'id', 'user_id', 'name', 'filter_settings', 'paper_count', 'chart_data', 'created_at'}
            
            table_exists_val = self.table_exists("analyses", conn)
            if table_exists_val:
                has_all = True
                for col in required_cols:
                    if not self.column_exists("analyses", col, conn):
                        has_all = False
                        break
                if not has_all:
                    conn.execute("DROP TABLE IF EXISTS analyses;")
                    conn.commit()
                    
            analyses_sql = """
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT DEFAULT 'Analysis',
                    filter_settings TEXT,
                    paper_count INTEGER DEFAULT 0,
                    chart_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """
            if self.is_postgres:
                analyses_sql = analyses_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                analyses_sql = analyses_sql.replace("DEFAULT CURRENT_TIMESTAMP", "DEFAULT NOW()")
            conn.execute(analyses_sql)
            conn.commit()
        finally:
            conn.close()

    def create_analysis(self, name: str, filter_settings: str, paper_count: int, chart_data: str, user_id: Optional[int] = None) -> int:
        """Creates a new analysis record and returns its id."""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO analyses (name, filter_settings, paper_count, chart_data, user_id) VALUES (?, ?, ?, ?, ?);",
                (name, filter_settings, paper_count, chart_data, user_id)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        """Fetches a single analysis by id."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM analyses WHERE id = ?;", (analysis_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_analyses(self, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns analyses filtered by user_id if provided, ordered by most recent first."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            if user_id is not None:
                cursor = conn.execute(
                    "SELECT id, user_id, name, filter_settings, paper_count, created_at FROM analyses WHERE user_id = ? ORDER BY created_at DESC;",
                    (user_id,)
                )
            else:
                cursor = conn.execute(
                    "SELECT id, user_id, name, filter_settings, paper_count, created_at FROM analyses ORDER BY created_at DESC;"
                )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def update_analysis(self, analysis_id: int, name: str = None, filter_settings: str = None, chart_data: str = None) -> bool:
        """Updates an analysis record. Only updates fields that are not None."""
        conn = self.get_connection()
        try:
            sets = []
            params = []
            if name is not None:
                sets.append("name = ?")
                params.append(name)
            if filter_settings is not None:
                sets.append("filter_settings = ?")
                params.append(filter_settings)
            if chart_data is not None:
                sets.append("chart_data = ?")
                params.append(chart_data)
            if not sets:
                return False
            params.append(analysis_id)
            cursor = conn.execute(
                f"UPDATE analyses SET {', '.join(sets)} WHERE id = ?;",
                params
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def delete_analysis(self, analysis_id: int) -> bool:
        """Deletes an analysis by id."""
        conn = self.get_connection()
        try:
            cursor = conn.execute("DELETE FROM analyses WHERE id = ?;", (analysis_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _resolve_tab_sql(self, tab: Optional[str]) -> str:
        """Return tab WHERE SQL, preferring indexed tab_* columns when available.

        Never uses legacy tab SQL on the request hot path when tab_* columns exist —
        legacy expressions scan the full table (~20s on production) and cause search timeouts.
        """
        if not tab:
            return ""
        from paper_tab_flags import dashboard_tab_sql, legacy_tab_sql_for

        if self._tab_flag_columns_exist():
            indexed_sql = dashboard_tab_sql(tab)
            if indexed_sql:
                return indexed_sql
        legacy_sql = legacy_tab_sql_for(tab)
        if legacy_sql:
            return legacy_sql
        return _TAB_SQL.get(tab, "")

    def sync_tab_flags_for_paper(
        self,
        paper_id: int,
        conn=None,
        publication_type: Optional[str] = None,
        study_type: Any = None,
        ingestion_status: Optional[str] = None,
    ) -> None:
        """Updates denormalized tab_* columns for one paper when those columns exist."""
        from paper_tab_flags import TAB_FLAG_FIELDS, compute_tab_flags

        own_conn = conn is None
        if own_conn:
            conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if publication_type is None and study_type is None and ingestion_status is None:
                cursor.execute(
                    "SELECT publication_type, study_type, ingestion_status FROM papers WHERE id = ?",
                    (paper_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return
                if hasattr(row, "keys"):
                    publication_type = row["publication_type"]
                    study_type = row["study_type"]
                    ingestion_status = row["ingestion_status"]
                else:
                    publication_type, study_type, ingestion_status = row[0], row[1], row[2]

            flags = compute_tab_flags(
                publication_type=publication_type,
                study_type=study_type,
                ingestion_status=ingestion_status,
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
            cursor.execute(
                f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?",
                params,
            )
            if own_conn:
                conn.commit()
        except Exception as exc:
            if "no such column" in str(exc).lower() or "does not exist" in str(exc).lower():
                logger.debug("Tab flag columns unavailable; skipping sync for paper %s", paper_id)
                return
            raise
        finally:
            if own_conn:
                conn.close()

    def sync_orphan_tab_flags_since(self, since_date: str) -> int:
        """Assign dashboard tab flags for recently harvested papers that have none.

        Harvest used to insert rows with tab_* defaulting to 0, which hid them from
        every dashboard tab even though date_harvested was set.
        """
        from paper_tab_flags import TAB_FLAG_FIELDS

        if not self._tab_flag_columns_exist():
            return 0
        start = str(since_date).strip()
        if start and "T" not in start:
            start = f"{start}T00:00:00"
        zero_clause = " AND ".join(
            f"COALESCE({column}, 0) = 0" for column in TAB_FLAG_FIELDS.values()
        )
        conn = self.get_connection()
        cursor = conn.cursor()
        updated = 0
        try:
            cursor.execute(
                f"""
                SELECT id, publication_type, study_type, ingestion_status
                FROM papers
                WHERE date_harvested >= ?
                  AND ({zero_clause})
                """,
                (start,),
            )
            rows = cursor.fetchall()
            for row in rows:
                if hasattr(row, "keys"):
                    paper_id = row["id"]
                    publication_type = row["publication_type"]
                    study_type = row["study_type"]
                    ingestion_status = row["ingestion_status"]
                else:
                    paper_id, publication_type, study_type, ingestion_status = row
                self.sync_tab_flags_for_paper(
                    int(paper_id),
                    conn=conn,
                    publication_type=publication_type,
                    study_type=study_type,
                    ingestion_status=ingestion_status,
                )
                updated += 1
            conn.commit()
        finally:
            conn.close()
        if updated:
            try:
                self.set_metadata("dashboard_tab_counts_json", "")
                self.set_metadata("dashboard_tab_counts_cached_at", "0")
            except Exception:
                logger.debug("Could not clear tab count cache after orphan repair")
        return updated

    def _tab_flag_columns_exist(self, conn=None) -> bool:
        """Return True when indexed tab membership columns are present."""
        if conn is None and DatabaseManager._tab_columns_exist_cache is not None:
            return DatabaseManager._tab_columns_exist_cache

        own_conn = conn is None
        if own_conn:
            conn = self.get_connection()
        try:
            exists = self.column_exists("papers", "tab_preclinical", conn)
            if own_conn:
                DatabaseManager._tab_columns_exist_cache = exists
            return exists
        finally:
            if own_conn:
                conn.close()

    def _tab_flags_backfill_is_complete(self) -> bool:
        """Return True when indexed tab counts match legacy routing SQL."""
        from paper_tab_flags import dashboard_tab_sql, legacy_tab_sql_for

        indexed_sql = dashboard_tab_sql("all_original")
        legacy_sql = legacy_tab_sql_for("all_original")
        if not indexed_sql or not legacy_sql:
            return False

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) as total FROM papers WHERE {indexed_sql}")
            row = cursor.fetchone()
            indexed_count = row["total"] if hasattr(row, "keys") else row[0]

            cursor.execute(f"SELECT COUNT(*) as total FROM papers WHERE {legacy_sql}")
            row = cursor.fetchone()
            legacy_count = row["total"] if hasattr(row, "keys") else row[0]

            if legacy_count == 0:
                return True
            tolerance = max(5, int(legacy_count * 0.01))
            return indexed_count >= (legacy_count - tolerance)
        except Exception as exc:
            logger.debug("Tab flag completeness check failed: %s", exc)
            return False
        finally:
            conn.close()

    def _backfill_tab_flags_python(self, conn) -> None:
        """Populate tab_* columns row-by-row using compute_tab_flags (Postgres-safe)."""
        from paper_tab_flags import TAB_FLAG_FIELDS, compute_tab_flags

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, publication_type, study_type, ingestion_status FROM papers"
        )
        rows = cursor.fetchall()
        tab_columns = list(TAB_FLAG_FIELDS.values())
        set_clause = ", ".join(f"{column} = ?" for column in tab_columns)
        batch: List[List[Any]] = []

        for row in rows:
            if hasattr(row, "keys"):
                paper_id = row["id"]
                publication_type = row["publication_type"]
                study_type = row["study_type"]
                ingestion_status = row["ingestion_status"]
            else:
                paper_id, publication_type, study_type, ingestion_status = row

            flags = compute_tab_flags(
                publication_type=publication_type,
                study_type=study_type,
                ingestion_status=ingestion_status,
            )
            params = [int(flags.get(column, 0)) for column in tab_columns]
            params.append(paper_id)
            batch.append(params)
            if len(batch) >= 500:
                cursor.executemany(
                    f"UPDATE papers SET {set_clause} WHERE id = ?",
                    batch,
                )
                conn.commit()
                batch = []

        if batch:
            cursor.executemany(
                f"UPDATE papers SET {set_clause} WHERE id = ?",
                batch,
            )
            conn.commit()

    def _mark_tab_flags_ready(self, ready: bool) -> None:
        """Persist and cache indexed tab-flag readiness for request hot paths."""
        self.set_metadata(TAB_FLAGS_READY_METADATA_KEY, "true" if ready else "false")
        DatabaseManager._tab_flags_ready_cache = ready

    def _tab_flags_are_ready(self) -> bool:
        """Returns True when indexed tab columns exist and backfill is marked complete."""
        if DatabaseManager._tab_flags_ready_cache is not None:
            return DatabaseManager._tab_flags_ready_cache

        if not self._tab_flag_columns_exist():
            DatabaseManager._tab_flags_ready_cache = False
            return False

        meta = self.get_metadata(TAB_FLAGS_READY_METADATA_KEY)
        if meta == "true":
            DatabaseManager._tab_flags_ready_cache = True
            return True
        if meta == "false":
            DatabaseManager._tab_flags_ready_cache = False
            return False

        DatabaseManager._tab_flags_ready_cache = False
        return False

    def _refresh_tab_flags_ready_cache(self) -> None:
        """Clear cached tab-flag readiness so the next query re-evaluates metadata."""
        DatabaseManager._tab_flags_ready_cache = None

    def _backfill_tab_flags(self, conn) -> None:
        """Backfills tab_* columns using SQL on SQLite or Python on Postgres."""
        from paper_tab_flags import BACKFILL_TAB_FLAGS_SQL

        if not self._tab_flag_columns_exist(conn):
            logger.warning("Tab flag columns unavailable; skipping backfill.")
            return

        if self.is_postgres:
            self._backfill_tab_flags_python(conn)
            ready = self._tab_flags_backfill_is_complete()
            self._mark_tab_flags_ready(ready)
            self._refresh_tab_flags_ready_cache()
            return

        cursor = conn.cursor()
        try:
            cursor.execute(BACKFILL_TAB_FLAGS_SQL)
            conn.commit()
        except Exception as exc:
            logger.warning("SQL tab flag backfill failed; falling back to Python: %s", exc)
            self._backfill_tab_flags_python(conn)
        ready = self._tab_flags_backfill_is_complete()
        self._mark_tab_flags_ready(ready)
        self._refresh_tab_flags_ready_cache()
