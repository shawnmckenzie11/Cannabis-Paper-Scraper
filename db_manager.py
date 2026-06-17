# db_manager.py
import sqlite3
import os
import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")
SCHEMA_FILE = "schema.sql"

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

class DatabaseManager:
    """Manages SQLite and PostgreSQL operations, indexing, and dynamic querying for cannabis papers."""
    
    _initialized = False
    
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
                conn_check = self.get_connection()
                # Unwrapped connection check
                unwrapped_conn = conn_check.conn
                cursor = unwrapped_conn.cursor()
                # Ensure compatibility functions exist
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
                cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'papers');")
                row = cursor.fetchone()
                db_exists = list(row.values())[0] if isinstance(row, dict) else row[0]
                
                if db_exists:
                    try:
                        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'system_metadata');")
                        meta_table_exists_row = cursor.fetchone()
                        meta_table_exists = list(meta_table_exists_row.values())[0] if meta_table_exists_row else False
                        if meta_table_exists:
                            cursor.execute("SELECT value FROM system_metadata WHERE key = 'fts_index_updated_authors';")
                            meta_row = cursor.fetchone()
                            meta_val = list(meta_row.values())[0] if meta_row else None
                            if meta_val != 'true':
                                cursor.execute("SET maintenance_work_mem = '16MB';")
                                cursor.execute("DROP INDEX IF EXISTS idx_papers_fts;")
                                cursor.execute("CREATE INDEX IF NOT EXISTS idx_papers_fts ON papers USING GIN (to_tsvector('english', title || ' ' || coalesce(abstract, '') || ' ' || coalesce(authors, '')));")
                                cursor.execute("INSERT INTO system_metadata (key, value) VALUES ('fts_index_updated_authors', 'true') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;")
                                unwrapped_conn.commit()
                    except Exception as idx_err:
                        logger.error(f"Failed to update FTS GIN index: {idx_err}")
                        pass
                        
                conn_check.close()
            except Exception as e:
                logger.error(f"Postgres connection check/compat functions failed: {e}")
                pass
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
            self.init_db()
            DatabaseManager._initialized = True
        elif not DatabaseManager._initialized:
            if not self.is_postgres:
                self.init_db()
            DatabaseManager._initialized = True

    def get_connection(self):
        """Returns a connection wrapper supporting standard operations."""
        if self.is_postgres:
            if psycopg2 is None:
                raise ImportError("PostgreSQL connection requested but psycopg2 is not installed.")
            # Handle URL scheme adjustment if needed (Fly.io might pass postgres://)
            url = self.database_url
            conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
            return PostgresConnectionWrapper(conn)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn

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
                ("treatment_duration", "TEXT")
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
                "CREATE INDEX IF NOT EXISTS idx_papers_thc ON papers(thc_pct);"
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

    def insert_paper(self, paper: Dict[str, Any]) -> int:
        """Inserts a paper into the database. If conflicts on DOI/PMID/Semantic Scholar ID, handles updates gracefully.
        
        Args:
            paper: Dictionary containing all field values to store.
            
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
            "summary", "publication_type", "expert_locked_fields", "classification_confidence", 
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

        # Check if the paper already exists in DB to prevent unique constraint failures and instead update
        existing_id = None
        
        if paper_copy.get("pmid"):
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
            
            
        if filters.get("open_access") is not None:
            val = filters["open_access"]
            if isinstance(val, str):
                val = 1 if val.lower() in ("true", "1", "yes") else 0
            else:
                val = 1 if val else 0
            where_clauses.append("papers.open_access = ?")
            params.append(val)
            
        # Tab-based filtering
        tab = filters.get("tab")
        if tab == "original":
            where_clauses.append(
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
        elif tab == "review":
            where_clauses.append(
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
        elif tab == "recent" or filters.get("recent"):
            recent_range = filters.get("recent_range")
            from datetime import datetime as dt, timedelta as td
            now = dt.now()
            if recent_range == "today":
                start_date = now.strftime("%Y-%m-%d") + "T00:00:00"
                where_clauses.append("papers.date_harvested >= ?")
                params.append(start_date)
            elif recent_range == "week":
                start_date = (now - td(days=7)).strftime("%Y-%m-%d") + "T00:00:00"
                where_clauses.append("papers.date_harvested >= ?")
                params.append(start_date)
            elif recent_range == "month":
                start_date = (now - td(days=30)).strftime("%Y-%m-%d") + "T00:00:00"
                where_clauses.append("papers.date_harvested >= ?")
                params.append(start_date)
            else:
                recent_date = (now - td(days=180)).strftime("%Y-%m-%d")
                current_year = now.year
                where_clauses.append("(papers.publication_date >= ? OR papers.year >= ?)")
                params.extend([recent_date, current_year])
            
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
            
        # Classification level filter
        class_level = filters.get("classification_level")
        if class_level and class_level != "ALL":
            if class_level == "native":
                where_clauses.append("(papers.classifier_version IS NULL OR papers.classifier_version NOT LIKE 'llm-%')")
            elif class_level == "claude_abstract":
                where_clauses.append("papers.classifier_version LIKE 'llm-reclassify-%'")
            elif class_level == "claude_pdf":
                where_clauses.append("papers.classifier_version LIKE 'llm-pdf-reclassify-%'")
            elif class_level == "manual":
                where_clauses.append("(papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != '')")
            elif class_level == "optimal":
                where_clauses.append("(papers.classifier_version LIKE 'llm-pdf-reclassify-%' OR (papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != ''))")
                    
        return where_clauses, params

    def search_papers(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
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
        
        # 1. Base Select
        if query_val:
            # Join with FTS table
            select_sql = "SELECT papers.*, papers_fts.rank FROM papers JOIN papers_fts ON papers.id = papers_fts.rowid"
        else:
            select_sql = "SELECT papers.* FROM papers"
            
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
                
            return results
        finally:
            conn.close()

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
