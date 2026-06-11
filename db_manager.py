# db_manager.py
import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")
SCHEMA_FILE = "schema.sql"

class DatabaseManager:
    """Manages SQLite operations, FTS5 indexing, and dynamic querying for cannabis papers."""
    
    _initialized = False
    
    def __init__(self, db_path: str = DATABASE_FILE):
        self.db_path = db_path
        
        # Ensure the parent directory for the database exists
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        # Check if papers table exists in this DB
        db_exists = False
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

        if not db_exists or not DatabaseManager._initialized:
            self.init_db()
            if db_exists:
                DatabaseManager._initialized = True

    def get_connection(self) -> sqlite3.Connection:
        """Returns a sqlite3 connection with dict-like row factory and JSON support verification."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys just in case
        conn.execute("PRAGMA foreign_keys = ON;")
        # Enable WAL mode for high concurrency
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def init_db(self):
        """Initializes the SQLite database with the schema.sql file if not already set up."""
        # Read the schema file
        if not os.path.exists(SCHEMA_FILE):
            # Create a fallback inline schema if the file is missing (should not happen)
            raise FileNotFoundError(f"Schema file '{SCHEMA_FILE}' is required for database initialization.")
            
        with open(SCHEMA_FILE, "r") as f:
            schema_script = f.read()

        # Check if papers_fts needs migration (i.e. does not have 'authors' column)
        fts_needs_migration = False
        if os.path.exists(self.db_path):
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
            if fts_needs_migration:
                # Drop existing triggers and FTS virtual table so they get recreated
                conn.execute("DROP TRIGGER IF EXISTS papers_ai;")
                conn.execute("DROP TRIGGER IF EXISTS papers_ad;")
                conn.execute("DROP TRIGGER IF EXISTS papers_au;")
                conn.execute("DROP TABLE IF EXISTS papers_fts;")
                conn.commit()

            conn.executescript(schema_script)

            if fts_needs_migration:
                # Populate recreated FTS virtual table
                conn.execute("INSERT INTO papers_fts(rowid, title, abstract, authors) SELECT id, title, abstract, authors FROM papers;")
                conn.commit()

            # Ensure publication_date column exists in existing tables
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN publication_date TEXT;")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
            # Ensure cannabis_type column exists in existing tables
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN cannabis_type TEXT;")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
            # Ensure summary column exists in existing tables
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN summary TEXT;")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
            # Ensure publication_type column exists in existing tables
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN publication_type TEXT;")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
            # Ensure expert_locked_fields column exists
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN expert_locked_fields TEXT DEFAULT '[]';")
            except sqlite3.OperationalError:
                pass
            # Ensure classification_confidence column exists
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN classification_confidence REAL;")
            except sqlite3.OperationalError:
                pass
            # Ensure classification_timestamp column exists
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN classification_timestamp TEXT;")
            except sqlite3.OperationalError:
                pass
            # Ensure classifier_version column exists
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN classifier_version TEXT;")
            except sqlite3.OperationalError:
                pass
            # Ensure type-dependent cannabinoid fields exist
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN puff_count INTEGER;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN thc_mg_ml REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN thc_mg_g REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN thc_mg_kg REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN cbd_mg_ml REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN cbd_mg_g REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN cbd_mg_kg REAL;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN inhaled_exposure_duration TEXT;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN administration_frequency TEXT;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE papers ADD COLUMN treatment_duration TEXT;")
            except sqlite3.OperationalError:
                pass
            # Ensure system_metadata table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            # Ensure users table exists
            conn.execute("""
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
            """)
            # Ensure analyses table exists
            self.init_analyses_table()

            # Ensure citation_edges table exists
            conn.execute("""
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
            """)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_source ON citation_edges(source_paper_id);")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_target ON citation_edges(target_paper_id);")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_rel ON citation_edges(relationship);")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_ext ON citation_edges(target_external_id);")
            except sqlite3.OperationalError:
                pass

            # Ensure llm_calls_log table exists
            conn.execute("""
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
                    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE SET NULL
                );
            """)

            # Populate publication_date for existing rows using year
            conn.execute("UPDATE papers SET publication_date = year || '-01-01' WHERE publication_date IS NULL AND year IS NOT NULL;")
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(f"Failed to initialize database: {e}")
        finally:
            conn.close()

    def get_metadata(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Fetches a metadata value from the database."""
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_metadata WHERE key = ?;", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
        except sqlite3.Error:
            return default
        finally:
            conn.close()

    def set_metadata(self, key: str, value: str):
        """Sets a metadata value in the database, overwriting if already exists."""
        conn = self.get_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO system_metadata (key, value) VALUES (?, ?);", (key, value))
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(f"Failed to set metadata key '{key}': {e}")
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
            "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "strain_reported", "strain_normalized", "duration_days",
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
        except sqlite3.Error as e:
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
        classification_confidence = metrics.get("classification_confidence", 0.0)
        classifier_version = metrics.get("classifier_version", "1.0.0")

        sql = """
            INSERT INTO llm_calls_log (
                paper_id, timestamp, model, input_tokens, cache_read_tokens, cache_write_tokens,
                output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence, classifier_version, batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            paper_id, timestamp, model, input_tokens, cache_read, cache_write,
            output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence, classifier_version, batch_id
        )

        if cursor:
            cursor.execute(sql, params)
        else:
            conn = self.get_connection()
            try:
                conn.execute(sql, params)
                conn.commit()
            except sqlite3.Error as e:
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
        except sqlite3.Error as e:
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
                
        # Claude classified filter (classifier_version starts with llm-)
        if filters.get("claude_classified"):
            where_clauses.append("papers.classifier_version LIKE 'llm-%'")
                    
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

        if sort_by == "year":
            sql += f" ORDER BY papers.year {sort_dir}, papers.id DESC"
        elif sort_by == "citations":
            sql += f" ORDER BY papers.citation_count {sort_dir}, papers.year DESC"
        elif sort_by == "title":
            sql += f" ORDER BY papers.title {sort_dir}, papers.year DESC"
        elif sort_by == "duration":
            sql += f" ORDER BY papers.duration_days {sort_dir}, papers.year DESC"
        elif sort_by == "study_type":
            sql += f" ORDER BY papers.study_type {sort_dir}, papers.year DESC"
        elif sort_by == "exposure_method":
            sql += f" ORDER BY papers.exposure_method {sort_dir}, papers.year DESC"
        elif sort_by == "publication_type":
            sql += f" ORDER BY papers.publication_type {sort_dir}, papers.year DESC"
        elif sort_by == "cannabis_type":
            sql += f" ORDER BY papers.cannabis_type {sort_dir}, papers.year DESC"
        elif sort_by == "outcome_domain":
            sql += f" ORDER BY papers.outcome_domain {sort_dir}, papers.year DESC"
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
        except sqlite3.IntegrityError:
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
        except sqlite3.Error:
            return False
        finally:
            conn.close()

    # ─── Analyses Table ──────────────────────────────────────────

    def init_analyses_table(self):
        """Creates the analyses table if it does not exist, migrates if needed."""
        conn = self.get_connection()
        try:
            # Check if table exists with the right columns
            cursor = conn.execute("PRAGMA table_info(analyses);")
            existing_cols = {row[1] for row in cursor.fetchall()}
            required_cols = {'id', 'name', 'filter_settings', 'paper_count', 'chart_data', 'created_at'}

            if existing_cols and not required_cols.issubset(existing_cols):
                # Table exists but is incomplete; drop and recreate
                conn.execute("DROP TABLE IF EXISTS analyses;")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT DEFAULT 'Analysis',
                    filter_settings TEXT,
                    paper_count INTEGER DEFAULT 0,
                    chart_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def create_analysis(self, name: str, filter_settings: str, paper_count: int, chart_data: str) -> int:
        """Creates a new analysis record and returns its id."""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO analyses (name, filter_settings, paper_count, chart_data) VALUES (?, ?, ?, ?);",
                (name, filter_settings, paper_count, chart_data)
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

    def list_analyses(self) -> List[Dict[str, Any]]:
        """Returns all analyses ordered by most recent first."""
        conn = self.get_connection()
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT id, name, filter_settings, paper_count, created_at FROM analyses ORDER BY created_at DESC;")
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
