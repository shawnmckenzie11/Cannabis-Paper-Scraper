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

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")
SCHEMA_FILE = "schema.sql"

_SQL_ORIGINAL_RESEARCH = (
    "("
    "  papers.publication_type = 'original research'"
    "  OR"
    "  (papers.publication_type IS NULL AND ("
    "    (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND NOT EXISTS ("
    "        SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
    "    ))"
    "    OR"
    "    ((NOT json_valid(papers.study_type) OR json_type(papers.study_type) != 'array') AND (papers.study_type IS NULL OR papers.study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial')))"
    "  ))"
    ")"
)

_SQL_REVIEW_PUBLICATION = (
    "("
    "  (papers.publication_type IS NOT NULL AND papers.publication_type != 'original research')"
    "  OR"
    "  (papers.publication_type IS NULL AND ("
    "    (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
    "        SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
    "    ))"
    "    OR"
    "    (papers.study_type IN ('review', 'meta-analysis', 'case study', 'editorial'))"
    "  ))"
    ")"
)

_SQL_INGESTION_NOT_CANNABIS = (
    "LOWER(COALESCE(papers.ingestion_status, '')) IN ('not_cannabis_related', 'not cannabis-related')"
)

_SQL_INGESTION_IRRELEVANT = "LOWER(COALESCE(papers.ingestion_status, '')) = 'irrelevant'"

_SQL_INGESTION_TANGENTIAL = "LOWER(COALESCE(papers.ingestion_status, '')) = 'tangential'"

_SQL_HAS_PDF_LINK = (
    "(papers.full_text_link IS NOT NULL AND TRIM(papers.full_text_link) != ''"
    " AND LOWER(papers.full_text_link) LIKE '%.pdf')"
)

_SQL_HAS_FULL_TEXT_LINK = (
    "(papers.full_text_link IS NOT NULL AND TRIM(papers.full_text_link) != ''"
    " AND LOWER(papers.full_text_link) NOT LIKE '%pubmed.ncbi.nlm.nih.gov/%')"
)

_SQL_INGESTION_ROUTED = (
    "("
    f"  {_SQL_INGESTION_NOT_CANNABIS}"
    "  OR "
    f"  {_SQL_INGESTION_IRRELEVANT}"
    "  OR "
    f"  {_SQL_INGESTION_TANGENTIAL}"
    ")"
)

_SQL_CLINICAL_STUDY = (
    "("
    "  LOWER(COALESCE(papers.study_type, '')) LIKE '%clinical%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rct%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%prospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%retrospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%observational%'"
    ")"
)

_SQL_PRECLINICAL_STUDY = (
    "("
    "  LOWER(COALESCE(papers.study_type, '')) LIKE '%animal%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%mouse%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rat%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rodent%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%in vivo%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%cell culture%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%vitro%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%organoid%'"
    ")"
)

_TAB_SQL = {
    "all_original": (
        f"({_SQL_ORIGINAL_RESEARCH} AND NOT {_SQL_INGESTION_ROUTED})"
    ),
    "preclinical": (
        f"({_SQL_ORIGINAL_RESEARCH} AND NOT {_SQL_INGESTION_ROUTED} AND {_SQL_PRECLINICAL_STUDY})"
    ),
    "clinical": (
        f"({_SQL_ORIGINAL_RESEARCH} AND NOT {_SQL_INGESTION_ROUTED} AND {_SQL_CLINICAL_STUDY})"
    ),
    "unclassified_preclinical": (
        f"({_SQL_ORIGINAL_RESEARCH} AND NOT {_SQL_INGESTION_ROUTED}"
        f" AND NOT {_SQL_CLINICAL_STUDY} AND NOT {_SQL_PRECLINICAL_STUDY})"
    ),
    "unclassified": (
        f"({_SQL_INGESTION_TANGENTIAL}) OR "
        f"({_SQL_ORIGINAL_RESEARCH} AND NOT {_SQL_INGESTION_ROUTED}"
        f" AND NOT {_SQL_CLINICAL_STUDY} AND NOT {_SQL_PRECLINICAL_STUDY})"
    ),
    "tangential": f"({_SQL_INGESTION_TANGENTIAL})",
    "review": f"({_SQL_REVIEW_PUBLICATION} AND NOT {_SQL_INGESTION_ROUTED})",
}

_DASHBOARD_TAB_KEYS = (
    "all_original",
    "preclinical",
    "clinical",
    "review",
    "unclassified",
)

TABLE_LIST_COLUMNS = (
    "papers.id",
    "papers.pmid",
    "papers.doi",
    "papers.title",
    "papers.authors",
    "papers.journal",
    "papers.year",
    "papers.full_text_link",
    "papers.study_type",
    "papers.publication_type",
    "papers.exposure_method",
    "papers.cannabis_type",
    "papers.thc_pct",
    "papers.cbd_pct",
    "papers.dose_mg",
    "papers.puff_count",
    "papers.thc_mg_ml",
    "papers.thc_mg_g",
    "papers.thc_mg_kg",
    "papers.cbd_mg_ml",
    "papers.cbd_mg_g",
    "papers.cbd_mg_kg",
    "papers.thc_uM",
    "papers.cbd_uM",
    "papers.strain_reported",
    "papers.strain_normalized",
    "papers.duration_days",
    "papers.inhaled_exposure_duration",
    "papers.administration_frequency",
    "papers.treatment_duration",
    "papers.repeat_exposure_count",
    "papers.exposure_regimen_bin",
    "papers.sample_size",
    "papers.outcome_domain",
    "papers.open_access",
    "papers.citation_count",
    "papers.date_harvested",
    "papers.expert_locked_fields",
    "papers.classification_confidence",
    "papers.classifier_version",
    "papers.ingestion_status",
    "papers.species",
    "papers.population_age",
    "papers.population_sex",
)


class PostgresCursorWrapper:
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

    def get_connection(self, retries: int = 3):
        """Returns a connection wrapper supporting standard operations."""
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

    def search_feedback_corrections_bm25(
        self,
        query_text: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieves feedback audit rows ranked by BM25/full-text relevance to a query."""
        cleaned_query = self.build_bm25_query(query_text)
        if not cleaned_query:
            return []

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if self.is_postgres:
                pg_query = cleaned_query.replace(" OR ", " | ")
                cursor.execute(
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
                            to_tsquery('english', %s)
                        ) AS bm25_score
                    FROM feedback_audit
                    WHERE to_tsvector(
                        'english',
                        coalesce(title, '') || ' ' || coalesce(abstract, '') || ' ' ||
                        coalesce(field_name, '') || ' ' || coalesce(old_value, '') || ' ' || coalesce(new_value, '')
                    ) @@ to_tsquery('english', %s)
                    ORDER BY bm25_score DESC
                    LIMIT %s
                    """,
                    (pg_query, pg_query, limit),
                )
            else:
                if not self.table_exists("feedback_audit_fts", conn):
                    return []
                cursor.execute(
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
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                score = float(row.get("bm25_score") or 0.0)
                if self.is_postgres:
                    row["retrieval_similarity"] = max(0.0, min(1.0, score))
                else:
                    row["retrieval_similarity"] = max(0.0, min(1.0, 1.0 / (1.0 + abs(score))))
            return rows
        except Exception:
            return []
        finally:
            conn.close()

    def get_feedback_audit_for_paper(self, paper_id: int) -> List[Dict[str, Any]]:
        """Returns all feedback audit field corrections for one paper."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT field_name, old_value, new_value
                FROM feedback_audit
                WHERE paper_id = ?
                ORDER BY id ASC
                """,
                (paper_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

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
        """Inserts a paper into the database. If conflicts on DOI/PMID/Semantic Scholar ID, handles updates gracefully.

        Args:
            paper: Dictionary containing all field values to store.
            force_id: When set, always update this paper id (used by PDF upload review).

        Returns:
            The row ID of the inserted or updated paper.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Build fields dynamically
        fields = [
            "pmid", "doi", "semantic_scholar_id", "title", "authors", "journal", "year",
            "abstract", "full_text_link", "study_type", "exposure_method", "thc_pct",
            "cbd_pct", "dose_mg", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
            "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM", "strain_reported", "strain_normalized", "duration_days",
            "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
            "sample_size", "outcome_domain",
            "open_access", "citation_count", "date_harvested", "publication_date", "cannabis_type",
            "summary", "publication_type", "ingestion_status", "species",
            "population_age", "population_sex",
            "inclusion_criteria", "exclusion_criteria",
            "expert_locked_fields", "classification_confidence",
            "classification_timestamp", "classifier_version"
        ]
        
        # Ensure array fields are stored as JSON strings
        paper_copy = paper.copy()
        llm_metrics = paper_copy.pop("_llm_call_metrics", None)
        harvest_batch_id = paper_copy.pop("_harvest_batch_id", None)
        for list_field in ["authors", "outcome_domain", "study_type", "exposure_method", "cannabis_type", "expert_locked_fields"]:
            if list_field in paper_copy and not isinstance(paper_copy[list_field], str):
                paper_copy[list_field] = json.dumps(paper_copy[list_field])
            
        # Ensure open_access is integer 0 or 1
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
                paper_copy.get("abstract") or ""
            )

        # Prefer an explicit target id (PDF review confirm) over identifier lookup.
        existing_id = int(force_id) if force_id is not None else None

        if not existing_id and paper_copy.get("pmid"):
            cursor.execute("SELECT id FROM papers WHERE pmid = ?", (paper_copy["pmid"],))
            row = cursor.fetchone()
            if row:
                existing_id = row["id"]
                
        if not existing_id and paper_copy.get("doi"):
            cursor.execute("SELECT id FROM papers WHERE doi = ?", (paper_copy["doi"],))
            row = cursor.fetchone()
            if row:
                existing_id = row["id"]
                
        if not existing_id and paper_copy.get("semantic_scholar_id"):
            cursor.execute("SELECT id FROM papers WHERE semantic_scholar_id = ?", (paper_copy["semantic_scholar_id"],))
            row = cursor.fetchone()
            if row:
                existing_id = row["id"]

        if not existing_id and paper_copy.get("title"):
            cursor.execute(
                "SELECT id FROM papers WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) LIMIT 1",
                (paper_copy["title"],),
            )
            row = cursor.fetchone()
            if row:
                existing_id = row["id"]

        try:
            if existing_id:
                # Update existing record
                update_pairs = []
                values = []
                for field in fields:
                    if field in paper_copy:
                        update_pairs.append(f"{field} = ?")
                        values.append(paper_copy[field])
                
                values.append(existing_id)
                query = f"UPDATE papers SET {', '.join(update_pairs)} WHERE id = ?"
                cursor.execute(query, values)
                row_id = existing_id
            else:
                # Insert new record
                present_fields = [f for f in fields if f in paper_copy]
                placeholders = [f"?{i+1}" for i in range(len(present_fields))]
                values = [paper_copy[f] for f in present_fields]
                
                query = f"INSERT INTO papers ({', '.join(present_fields)}) VALUES ({', '.join(placeholders)})"
                cursor.execute(query, values)
                row_id = cursor.lastrowid
                
            if llm_metrics:
                self.log_llm_call(
                    paper_id=row_id,
                    metrics=llm_metrics,
                    batch_id=harvest_batch_id or "harvest",
                    cursor=cursor
                )
            conn.commit()
            return row_id
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Database error during insert/update: {e}")
        finally:
            conn.close()

    def find_paper_id_by_title(self, title: str) -> Optional[int]:
        """Return the paper id for an exact title match (case-insensitive), if any."""
        normalized = (title or "").strip()
        if not normalized:
            return None
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id FROM papers WHERE LOWER(TRIM(title)) = LOWER(TRIM(?)) LIMIT 1",
                (normalized,),
            )
            row = cursor.fetchone()
            return int(row["id"]) if row else None
        finally:
            conn.close()

    def find_fuzzy_paper_by_title(
        self,
        title: str,
        *,
        min_ratio: float = 0.82,
    ) -> Tuple[Optional[int], float]:
        """Return (paper_id, similarity) for the best title match at or above min_ratio."""
        matches = self.find_top_title_matches(title, limit=1, min_ratio=min_ratio)
        if not matches:
            # Still report best ratio below threshold when useful for diagnostics.
            soft = self.find_top_title_matches(title, limit=1, min_ratio=0.0)
            if soft:
                return None, float(soft[0]["similarity"])
            return None, 0.0
        return int(matches[0]["id"]), float(matches[0]["similarity"])

    def find_top_title_matches(
        self,
        title: str,
        *,
        limit: int = 5,
        min_ratio: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """Return up to `limit` candidate papers ranked by title similarity.

        Candidate retrieval uses punctuation-tolerant token AND patterns so
        titles like "COVID-19" still match queries normalized to "covid 19",
        then scores and collapses near-duplicate rows.
        """
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

        select_cols = (
            "id, title, year, journal, publication_type, pmid, doi, full_text_link"
        )
        rows_by_id: Dict[int, Dict[str, Any]] = {}

        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if exact_id is not None:
                cursor.execute(
                    f"SELECT {select_cols} FROM papers WHERE id = ?",
                    (exact_id,),
                )
                row = cursor.fetchone()
                if row:
                    rows_by_id[int(row["id"])] = dict(row)

            # Strongest retrieval: require several content tokens in order-insensitive AND.
            # Using %token% between tokens ignores commas/hyphens in stored titles.
            and_tokens = tokens[:6]
            if len(and_tokens) >= 3:
                clauses = " AND ".join(["LOWER(title) LIKE ?" for _ in and_tokens])
                cursor.execute(
                    f"SELECT {select_cols} FROM papers WHERE {clauses} LIMIT 80",
                    tuple(f"%{tok[:28]}%" for tok in and_tokens),
                )
                for row in cursor.fetchall():
                    rows_by_id[int(row["id"])] = dict(row)

            # Mid-strength: punctuation-tolerant full-token phrase pattern.
            pattern = pdf_upload_merge.title_token_like_pattern(query_for_match, max_tokens=8)
            if pattern and pattern != "%%":
                cursor.execute(
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 80",
                    (pattern,),
                )
                for row in cursor.fetchall():
                    rows_by_id[int(row["id"])] = dict(row)

            # Shorter leading phrase (first 5–6 normalized tokens) for long titles.
            phrase_tokens = pdf_upload_merge.normalize_title(query_for_match).split()[:6]
            if len(phrase_tokens) >= 4:
                short_pattern = "%" + "%".join(phrase_tokens) + "%"
                cursor.execute(
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 80",
                    (short_pattern,),
                )
                for row in cursor.fetchall():
                    rows_by_id[int(row["id"])] = dict(row)

            # Fallback: rarest/longest individual tokens (avoid flooding with "cannabis").
            for token in and_tokens[:4]:
                cursor.execute(
                    f"SELECT {select_cols} FROM papers WHERE LOWER(title) LIKE ? LIMIT 120",
                    (f"%{token[:28]}%",),
                )
                for row in cursor.fetchall():
                    rows_by_id[int(row["id"])] = dict(row)
        finally:
            conn.close()

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

    def search_papers_minimal_for_section_stats(
        self,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return id/title/abstract rows for all papers matching filters (no pagination)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        query_val = filters.get("query")
        if query_val:
            select_sql = (
                "SELECT papers.id, papers.title, papers.abstract, papers.full_text_link, papers.classifier_version "
                "FROM papers JOIN papers_fts ON papers.id = papers_fts.rowid"
            )
        else:
            select_sql = (
                "SELECT papers.id, papers.title, papers.abstract, papers.full_text_link, papers.classifier_version "
                "FROM papers"
            )
        where_clauses, params = self._build_filter_clauses(filters)
        sql = select_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        try:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

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
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
            row = cursor.fetchone()
            if row:
                res = dict(row)
                # Parse JSON fields
                for json_field in ["authors", "outcome_domain", "study_type", "exposure_method", "cannabis_type", "expert_locked_fields"]:
                    if res.get(json_field):
                        try:
                            val = res[json_field].strip()
                            if val.startswith("[") and val.endswith("]"):
                                res[json_field] = json.loads(res[json_field])
                        except Exception:
                            pass
                return res
            return None
        finally:
            conn.close()

    def delete_paper(self, paper_id: int) -> bool:
        """Deletes a paper by database ID."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Failed to delete paper: {e}")
        finally:
            conn.close()

    @staticmethod
    def clean_fts_query(query: str) -> str:
        """Cleans and sanitizes a query string for SQLite FTS5 to prevent syntax and 'no such column' errors."""
        if not query:
            return ""
        # Split by whitespace into individual tokens/terms
        terms = query.split()
        cleaned_terms = []
        for term in terms:
            # If term is already quoted, leave it as is
            if (term.startswith('"') and term.endswith('"')) or (term.startswith("'") and term.endswith("'")):
                cleaned_terms.append(term)
                continue
            
            # Check if the term has FTS5 special characters that need escaping/quoting
            # We allow * at the end for prefix searches, but quote if there are other specials like - or :
            has_wildcard = term.endswith('*')
            clean_term = term[:-1] if has_wildcard else term
            
            # If the term contains special characters like - or : or /
            if any(c in clean_term for c in ('-', ':', '/', '\\', '+', '~')):
                escaped_term = clean_term.replace('"', '""')
                if has_wildcard:
                    cleaned_terms.append(f'"{escaped_term}"*')
                else:
                    cleaned_terms.append(f'"{escaped_term}"')
            else:
                cleaned_terms.append(term)
                
        return " ".join(cleaned_terms)

    def _build_filter_clauses(self, filters: Dict[str, Any]) -> Tuple[List[str], List[Any]]:
        """Common helper to build SQL where clauses and extract bind parameters."""
        where_clauses = []
        params = []
        
        query_val = filters.get("query")
        if query_val:
            where_clauses.append("papers_fts MATCH ?")
            params.append(self.clean_fts_query(query_val))
            
        # 2. Dynamic Filters
        if filters.get("year_min") is not None:
            where_clauses.append("papers.year >= ?")
            params.append(int(filters["year_min"]))
            
        if filters.get("year_max") is not None:
            where_clauses.append("papers.year <= ?")
            params.append(int(filters["year_max"]))
            
        study_types = filters.get("study_type")
        if study_types:
            if isinstance(study_types, str):
                study_types = [s.strip() for s in study_types.split(",") if s.strip()]
            if study_types:
                # Expand any legacy category terms into their constituent Stage 2 types
                expanded_study_types = []
                for s in study_types:
                    if s == "RCT":
                        expanded_study_types.extend(["Clinical (RCT)", "RCT"])
                    elif s == "observational":
                        expanded_study_types.extend(["Clinical (prospective)", "Clinical (observational)", "Clinical (retrospective)", "observational"])
                    elif s == "animal":
                        expanded_study_types.extend(["Animal Models (Mouse)", "Animal Models (Rat)", "Animal Models (Other Rodents)", "Animal Models (Non-Human Primates)", "Animal Models (Other)", "animal"])
                    elif s == "in vitro":
                        expanded_study_types.extend(["Cell Culture (Primary Cells)", "Cell Culture (Cell Lines)", "Cell Culture (Organoids)", "Cell Culture (Co-Culture)", "Cell Culture (PCLS)", "Cell Culture (Other In Vitro)", "in vitro"])
                    else:
                        expanded_study_types.append(s)
                
                if filters.get("study_logic", "or").lower() == "and":
                    for s_type in expanded_study_types:
                        where_clauses.append(
                            "((json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
                            "SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value = ?"
                            ")) OR (papers.study_type = ?))"
                        )
                        params.extend([s_type, s_type])
                else:
                    placeholders = ",".join(["?"] * len(expanded_study_types))
                    where_clauses.append(
                        f"((json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
                        f"SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ({placeholders})"
                        f")) OR (papers.study_type IN ({placeholders})))"
                    )
                    params.extend(expanded_study_types)
                    params.extend(expanded_study_types)
            

 
        # Filter on minimum citation count
        if filters.get("citations_min") is not None:
            where_clauses.append("papers.citation_count >= ?")
            params.append(int(filters["citations_min"]))
 
        # Filter on cannabis types (supports comma-separated list or single value)
        cannabis_types = filters.get("cannabis_type")
        if cannabis_types:
            if isinstance(cannabis_types, str):
                cannabis_types = [c.strip() for c in cannabis_types.split(",") if c.strip()]
            if cannabis_types:
                if filters.get("cannabis_logic", "or").lower() == "and":
                    for c_type in cannabis_types:
                        where_clauses.append(
                            "((json_valid(papers.cannabis_type) AND json_type(papers.cannabis_type) = 'array' AND EXISTS ("
                            "SELECT 1 FROM json_each(papers.cannabis_type) WHERE json_each.value = ?"
                            ")) OR (papers.cannabis_type = ?))"
                        )
                        params.extend([c_type, c_type])
                else:
                    placeholders = ",".join(["?"] * len(cannabis_types))
                    where_clauses.append(
                        f"((json_valid(papers.cannabis_type) AND json_type(papers.cannabis_type) = 'array' AND EXISTS ("
                        f"SELECT 1 FROM json_each(papers.cannabis_type) WHERE json_each.value IN ({placeholders})"
                        f")) OR (papers.cannabis_type IN ({placeholders})))"
                    )
                    params.extend(cannabis_types)
                    params.extend(cannabis_types)
 
        # Filter on exposure methods (supports comma-separated list or single value)
        exposure_methods = filters.get("exposure_method")
        if exposure_methods:
            if isinstance(exposure_methods, str):
                exposure_methods = [m.strip() for m in exposure_methods.split(",") if m.strip()]
            if exposure_methods:
                if filters.get("exposure_logic", "or").lower() == "and":
                    for exp_method in exposure_methods:
                        where_clauses.append(
                            "((json_valid(papers.exposure_method) AND json_type(papers.exposure_method) = 'array' AND EXISTS ("
                            "SELECT 1 FROM json_each(papers.exposure_method) WHERE json_each.value = ?"
                            ")) OR (papers.exposure_method = ?))"
                        )
                        params.extend([exp_method, exp_method])
                else:
                    placeholders = ",".join(["?"] * len(exposure_methods))
                    where_clauses.append(
                        f"((json_valid(papers.exposure_method) AND json_type(papers.exposure_method) = 'array' AND EXISTS ("
                        f"SELECT 1 FROM json_each(papers.exposure_method) WHERE json_each.value IN ({placeholders})"
                        f")) OR (papers.exposure_method IN ({placeholders})))"
                    )
                    params.extend(exposure_methods)
                    params.extend(exposure_methods)
            
        if filters.get("thc_min") is not None:
            where_clauses.append("papers.thc_pct >= ?")
            params.append(float(filters["thc_min"]))
            
        if filters.get("thc_max") is not None:
            where_clauses.append("papers.thc_pct <= ?")
            params.append(float(filters["thc_max"]))

        if filters.get("cbd_min") is not None:
            where_clauses.append("papers.cbd_pct >= ?")
            params.append(float(filters["cbd_min"]))

        if filters.get("cbd_max") is not None:
            where_clauses.append("papers.cbd_pct <= ?")
            params.append(float(filters["cbd_max"]))

        has_pdf = filters.get("has_pdf")
        has_full_text = filters.get("has_full_text")
        if has_pdf is not None or has_full_text is not None:
            if isinstance(has_pdf, str):
                has_pdf = has_pdf.lower() in ("true", "1", "yes")
            if isinstance(has_full_text, str):
                has_full_text = has_full_text.lower() in ("true", "1", "yes")
            pdf_active = bool(has_pdf)
            full_text_active = bool(has_full_text)
            if pdf_active and full_text_active:
                where_clauses.append(f"({_SQL_HAS_PDF_LINK} OR {_SQL_HAS_FULL_TEXT_LINK})")
            elif pdf_active:
                where_clauses.append(_SQL_HAS_PDF_LINK)
            elif full_text_active:
                where_clauses.append(_SQL_HAS_FULL_TEXT_LINK)
            
            
        if filters.get("open_access") is not None:
            val = filters["open_access"]
            if isinstance(val, str):
                val = 1 if val.lower() in ("true", "1", "yes") else 0
            else:
                val = 1 if val else 0
            where_clauses.append("papers.open_access = ?")
            params.append(val)
            
        # Tab-based filtering (clinical/preclinical may overlap; recents stacks via recent_range)
        tab = filters.get("tab")
        if tab == "original":
            tab = "all_original"
        if tab == "recent":
            tab = None
        tab_sql = self._resolve_tab_sql(tab)
        if tab_sql:
            where_clauses.append(tab_sql)

        recent_range = filters.get("recent_range")
        if recent_range or filters.get("recent"):
            from paper_tab_flags import recent_range_sql

            recent_clause, recent_params = recent_range_sql(recent_range or "180d")
            where_clauses.append(recent_clause)
            params.extend(recent_params)

        # Numeric / sub-node scoped filters (read-only on existing columns)
        _NUMERIC_RANGE_FILTERS = (
            ("sample_size_min", "papers.sample_size", ">="),
            ("sample_size_max", "papers.sample_size", "<="),
            ("dose_mg_min", "papers.dose_mg", ">="),
            ("dose_mg_max", "papers.dose_mg", "<="),
            ("duration_days_min", "papers.duration_days", ">="),
            ("duration_days_max", "papers.duration_days", "<="),
            ("thc_mg_kg_min", "papers.thc_mg_kg", ">="),
            ("thc_mg_kg_max", "papers.thc_mg_kg", "<="),
            ("cbd_mg_kg_min", "papers.cbd_mg_kg", ">="),
            ("cbd_mg_kg_max", "papers.cbd_mg_kg", "<="),
            ("thc_mg_ml_min", "papers.thc_mg_ml", ">="),
            ("thc_mg_ml_max", "papers.thc_mg_ml", "<="),
            ("cbd_mg_ml_min", "papers.cbd_mg_ml", ">="),
            ("cbd_mg_ml_max", "papers.cbd_mg_ml", "<="),
            ("thc_uM_min", "papers.thc_uM", ">="),
            ("thc_uM_max", "papers.thc_uM", "<="),
            ("cbd_uM_min", "papers.cbd_uM", ">="),
            ("cbd_uM_max", "papers.cbd_uM", "<="),
            ("puff_count_min", "papers.puff_count", ">="),
        )
        for filter_key, column, operator in _NUMERIC_RANGE_FILTERS:
            raw = filters.get(filter_key)
            if raw is not None and raw != "":
                where_clauses.append(f"{column} {operator} ?")
                params.append(float(raw))

        population_age = filters.get("population_age")
        if population_age:
            if isinstance(population_age, str):
                population_age = [a.strip() for a in population_age.split(",") if a.strip()]
            if population_age:
                placeholders = ",".join(["?"] * len(population_age))
                where_clauses.append(f"LOWER(COALESCE(papers.population_age, '')) IN ({placeholders})")
                params.extend([a.lower() for a in population_age])

        population_sex = filters.get("population_sex")
        if population_sex:
            if isinstance(population_sex, str):
                population_sex = [s.strip() for s in population_sex.split(",") if s.strip()]
            if population_sex:
                placeholders = ",".join(["?"] * len(population_sex))
                where_clauses.append(f"LOWER(COALESCE(papers.population_sex, '')) IN ({placeholders})")
                params.extend([s.lower() for s in population_sex])

        species_values = filters.get("species")
        if species_values:
            if isinstance(species_values, str):
                species_values = [s.strip() for s in species_values.split(",") if s.strip()]
            if species_values:
                species_clauses = []
                for species in species_values:
                    clause, clause_params = _species_ui_match_clause(species)
                    species_clauses.append(clause)
                    params.extend(clause_params)
                where_clauses.append("(" + " OR ".join(species_clauses) + ")")

        regimen_values = filters.get("exposure_regimen_bin")
        if regimen_values:
            if isinstance(regimen_values, str):
                regimen_values = [r.strip() for r in regimen_values.split(",") if r.strip()]
            if regimen_values:
                placeholders = ",".join(["?"] * len(regimen_values))
                where_clauses.append(
                    f"LOWER(COALESCE(papers.exposure_regimen_bin, '')) IN ({placeholders})"
                )
                params.extend([value.lower() for value in regimen_values])

        # Outcome domain filters (JSON list)
        outcomes = filters.get("outcome")
        if outcomes:
            if isinstance(outcomes, str):
                outcomes = [o.strip() for o in outcomes.split(",") if o.strip()]
            if outcomes:
                if filters.get("outcome_logic", "or").lower() == "and":
                    for outcome in outcomes:
                        where_clauses.append("EXISTS (SELECT 1 FROM json_each(papers.outcome_domain) WHERE value = ?)")
                        params.append(outcome)
                else:
                    placeholders = ",".join(["?"] * len(outcomes))
                    where_clauses.append(f"EXISTS (SELECT 1 FROM json_each(papers.outcome_domain) WHERE value IN ({placeholders}))")
                    params.extend(outcomes)
                
        # Claude classified filter (classifier_version starts with llm-) - retained for backwards compatibility
        if filters.get("claude_classified"):
            where_clauses.append("papers.classifier_version LIKE 'llm-%'")
            
        # Filter on publication types (§5.2 — sidebar review tab)
        publication_types = filters.get("publication_type")
        if publication_types:
            if isinstance(publication_types, str):
                publication_types = [p.strip() for p in publication_types.split(",") if p.strip()]
            if publication_types:
                if filters.get("publication_type_logic", "or").lower() == "and":
                    for pub_type in publication_types:
                        where_clauses.append("LOWER(papers.publication_type) = LOWER(?)")
                        params.append(pub_type)
                else:
                    placeholders = ",".join(["?"] * len(publication_types))
                    where_clauses.append(
                        f"LOWER(papers.publication_type) IN ({','.join(['LOWER(?)'] * len(publication_types))})"
                    )
                    params.extend(publication_types)

        # Classification level filter (§5.2 — sidebar Classification Details)
        class_level = filters.get("classification_level")
        if class_level and class_level != "ALL":
            if class_level == "claude_abstract":
                where_clauses.append(
                    "(papers.classifier_version LIKE 'llm-reclassify-%' AND papers.classifier_version NOT LIKE 'llm-pdf-%')"
                )
            elif class_level == "claude_pdf":
                where_clauses.append("papers.classifier_version LIKE 'llm-pdf-reclassify-%'")
            elif class_level == "manual":
                where_clauses.append("(papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != '')")
            elif class_level == "optimal":
                where_clauses.append("(papers.classifier_version LIKE 'llm-pdf-reclassify-%' OR (papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != ''))")
            elif class_level == "maude":
                where_clauses.append("papers.classifier_version LIKE 'maude-%'")

        content_tier = filters.get("content_tier")
        if content_tier and content_tier not in ("any", "all"):
            import content_tiers

            tier_clause, tier_params = content_tiers.content_tier_sql_clause(content_tier)
            if tier_clause:
                where_clauses.append(tier_clause)
                params.extend(tier_params)
                    
        return where_clauses, params

    def search_papers(
        self,
        filters: Dict[str, Any],
        include_total: bool = False,
    ):
        """Queries the database dynamically using filters.
        
        Supported filters:
            query: Free-text match query (against title & abstract FTS5)
            year_min: Minimum publication year
            year_max: Maximum publication year
            study_type: exact study type string
            exposure_method: exact exposure method string
            thc_min: minimum numeric THC%
            outcome: outcome domains to search (comma-separated string or list)
            open_access: boolean or int (0/1) for open access
            sort_by: year, citations, or quality_score
            
        Returns:
            List of dictionaries containing matched papers.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query_val = filters.get("query")
        
        list_columns_sql = ", ".join(TABLE_LIST_COLUMNS)
        # 1. Base Select
        if query_val:
            # Join with FTS table
            select_sql = (
                f"SELECT {list_columns_sql}, papers_fts.rank "
                f"FROM papers JOIN papers_fts ON papers.id = papers_fts.rowid"
            )
        else:
            select_sql = f"SELECT {list_columns_sql} FROM papers"
            
        where_clauses, params = self._build_filter_clauses(filters)

        # 3. Assemble Where Clauses
        sql = select_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
            
        # 4. Sorting & Ordering
        sort_by = filters.get("sort_by")
        sort_dir = filters.get("sort_dir", "DESC").upper()
        if sort_dir not in ("ASC", "DESC"):
            sort_dir = "DESC"

        collate_clause = ' COLLATE "C"' if self.is_postgres else ''
        if sort_by == "year":
            sql += f" ORDER BY papers.year {sort_dir}, papers.id DESC"
        elif sort_by == "citations":
            sql += f" ORDER BY papers.citation_count {sort_dir}, papers.year DESC"
        elif sort_by == "title":
            sql += f" ORDER BY papers.title{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "duration":
            sql += f" ORDER BY papers.duration_days {sort_dir}, papers.year DESC"
        elif sort_by == "study_type":
            sql += f" ORDER BY papers.study_type{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "exposure_method":
            sql += f" ORDER BY papers.exposure_method{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "population_age":
            sql += f" ORDER BY papers.population_age{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "population_sex":
            sql += f" ORDER BY papers.population_sex{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "publication_type":
            sql += f" ORDER BY papers.publication_type{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "cannabis_type":
            sql += f" ORDER BY papers.cannabis_type{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "outcome_domain":
            sql += f" ORDER BY papers.outcome_domain{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "dose_mg":
            sql += f" ORDER BY papers.dose_mg {sort_dir}, papers.year DESC"
        elif sort_by == "puff_count":
            sql += f" ORDER BY papers.puff_count {sort_dir}, papers.year DESC"
        elif sort_by == "administration_frequency":
            sql += f" ORDER BY papers.administration_frequency{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "thc_mg_ml":
            sql += f" ORDER BY papers.thc_mg_ml {sort_dir}, papers.year DESC"
        elif sort_by == "cbd_mg_ml":
            sql += f" ORDER BY papers.cbd_mg_ml {sort_dir}, papers.year DESC"
        elif sort_by == "thc_mg_kg":
            sql += f" ORDER BY papers.thc_mg_kg {sort_dir}, papers.year DESC"
        elif sort_by == "cbd_mg_kg":
            sql += f" ORDER BY papers.cbd_mg_kg {sort_dir}, papers.year DESC"
        elif sort_by == "thc_uM":
            sql += f" ORDER BY papers.thc_uM {sort_dir}, papers.year DESC"
        elif sort_by == "cbd_uM":
            sql += f" ORDER BY papers.cbd_uM {sort_dir}, papers.year DESC"
        elif sort_by == "treatment_duration":
            sql += f" ORDER BY papers.treatment_duration{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "strain_reported":
            sql += f" ORDER BY papers.strain_reported{collate_clause} {sort_dir}, papers.year DESC"
        elif sort_by == "strain_normalized":
            sql += f" ORDER BY papers.strain_normalized{collate_clause} {sort_dir}, papers.year DESC"
        else:
            # Default sorting: Rank (relevance) or Year DESC
            if query_val:
                sql += " ORDER BY rank ASC"
            else:
                sql += " ORDER BY papers.year DESC, papers.id DESC"

        # 5. Limit & Offset (Pagination)
        limit = filters.get("limit")
        offset = filters.get("offset")
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                sql += " OFFSET ?"
                params.append(int(offset))
                
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                res = dict(row)
                # Parse JSON fields
                for json_field in ["authors", "outcome_domain"]:
                    if res.get(json_field):
                        try:
                            res[json_field] = json.loads(res[json_field])
                        except Exception:
                            res[json_field] = []
                    else:
                        res[json_field] = []

                for json_field in ["study_type", "exposure_method", "cannabis_type", "expert_locked_fields"]:
                    if res.get(json_field):
                        try:
                            val = res[json_field].strip()
                            if val.startswith("[") and val.endswith("]"):
                                res[json_field] = json.loads(res[json_field])
                        except Exception:
                            pass
                results.append(res)

            if include_total:
                count_filters = {
                    key: value
                    for key, value in filters.items()
                    if key not in ("limit", "offset")
                }
                return results, self.count_papers(count_filters)

            return results
        finally:
            conn.close()

    def search_papers_for_analysis(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Returns all papers matching analysis filter settings (no pagination cap beyond caller limit)."""
        return self.search_papers(filters)

    def count_papers(self, filters: Dict[str, Any]) -> int:
        """Counts total papers matching the filters by executing the query with COUNT(*)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query_val = filters.get("query")
        
        # 1. Base Select
        if query_val:
            select_sql = "SELECT COUNT(*) as total FROM papers JOIN papers_fts ON papers.id = papers_fts.rowid"
        else:
            select_sql = "SELECT COUNT(*) as total FROM papers"
            
        where_clauses, params = self._build_filter_clauses(filters)
                    
        sql = select_sql
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
            
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return row["total"] if row else 0
        finally:
            conn.close()

    def get_tab_counts(self) -> Dict[str, int]:
        """Return paper counts for each primary dashboard tab using indexed tab SQL."""
        cache_key = "dashboard_tab_counts_json"
        cache_at_key = "dashboard_tab_counts_cached_at"
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
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            for tab_key in _DASHBOARD_TAB_KEYS:
                tab_sql = self._resolve_tab_sql(tab_key)
                if not tab_sql:
                    counts[tab_key] = 0
                    continue
                cursor.execute(f"SELECT COUNT(*) as total FROM papers WHERE {tab_sql}")
                row = cursor.fetchone()
                counts[tab_key] = row["total"] if row else 0
            try:
                self.set_metadata(cache_key, json.dumps(counts))
                self.set_metadata(cache_at_key, str(time.time()))
            except Exception as exc:
                logger.debug("Tab count cache write failed: %s", exc)
            return counts
        finally:
            conn.close()

    def get_all_pmids(self) -> set:
        """Returns a set of all PMIDs currently stored in the database for skip checks."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pmid FROM papers WHERE pmid IS NOT NULL")
            return {row["pmid"] for row in cursor.fetchall()}
        finally:
            conn.close()

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
