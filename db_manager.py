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
    
    def __init__(self, db_path: str = DATABASE_FILE):
        self.db_path = db_path
        
        # Ensure the parent directory for the database exists
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
                    
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a sqlite3 connection with dict-like row factory and JSON support verification."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable foreign keys just in case
        conn.execute("PRAGMA foreign_keys = ON;")
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
                conn_check = sqlite3.connect(self.db_path)
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
            # Ensure system_metadata table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
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
            "cbd_pct", "dose_mg", "strain_reported", "strain_normalized", "duration_days",
            "population", "sample_size", "outcome_domain", "methodological_quality_flags",
            "methodological_quality_score", "open_access", "citation_count", "date_harvested", "publication_date", "cannabis_type", "summary"
        ]
        
        # Ensure array fields are stored as JSON strings
        paper_copy = paper.copy()
        for list_field in ["authors", "outcome_domain", "methodological_quality_flags", "study_type", "exposure_method", "cannabis_type", "population"]:
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
                
            conn.commit()
            return row_id
        except sqlite3.Error as e:
            conn.rollback()
            raise RuntimeError(f"Database error during insert/update: {e}")
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
                for json_field in ["authors", "outcome_domain", "methodological_quality_flags", "study_type", "exposure_method", "cannabis_type", "population"]:
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
            
        if filters.get("study_type"):
            where_clauses.append(
                "((json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
                "SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value = ?"
                ")) OR (papers.study_type = ?))"
            )
            params.append(filters["study_type"])
            params.append(filters["study_type"])
            
        # Filter on minimum methodological quality score
        if filters.get("quality_min") is not None:
            where_clauses.append("papers.methodological_quality_score >= ?")
            params.append(int(filters["quality_min"]))
 
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
            
        # Filter on populations (supports comma-separated list or single value)
        populations = filters.get("population")
        if populations:
            if isinstance(populations, str):
                populations = [p.strip() for p in populations.split(",") if p.strip()]
            if populations:
                if filters.get("population_logic", "or").lower() == "and":
                    for pop in populations:
                        where_clauses.append(
                            "((json_valid(papers.population) AND json_type(papers.population) = 'array' AND EXISTS ("
                            "SELECT 1 FROM json_each(papers.population) WHERE json_each.value = ?"
                            ")) OR (papers.population = ?))"
                        )
                        params.extend([pop, pop])
                else:
                    placeholders = ",".join(["?"] * len(populations))
                    where_clauses.append(
                        f"((json_valid(papers.population) AND json_type(papers.population) = 'array' AND EXISTS ("
                        f"SELECT 1 FROM json_each(papers.population) WHERE json_each.value IN ({placeholders})"
                        f")) OR (papers.population IN ({placeholders})))"
                    )
                    params.extend(populations)
                    params.extend(populations)
            
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
                "  (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND NOT EXISTS ("
                "      SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
                "  ))"
                "  OR"
                "  ((NOT json_valid(papers.study_type) OR json_type(papers.study_type) != 'array') AND (papers.study_type IS NULL OR papers.study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial')))"
                ")"
            )
        elif tab == "review":
            where_clauses.append(
                "("
                "  (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
                "      SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
                "  ))"
                "  OR"
                "  (papers.study_type IN ('review', 'meta-analysis', 'case study', 'editorial'))"
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
                
        # Flags filter: supports +flag_name (must have) and -flag_name (must not have)
        flags = filters.get("flags")
        if flags:
            if isinstance(flags, str):
                flags = [f.strip() for f in flags.split(",") if f.strip()]
            for flag in flags:
                if flag.startswith("-"):
                    flag_clean = flag[1:]
                    where_clauses.append("NOT EXISTS (SELECT 1 FROM json_each(papers.methodological_quality_flags) WHERE value = ?)")
                    params.append(flag_clean)
                else:
                    flag_clean = flag[1:] if flag.startswith("+") else flag
                    where_clauses.append("EXISTS (SELECT 1 FROM json_each(papers.methodological_quality_flags) WHERE value = ?)")
                    params.append(flag_clean)
                    
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
            thc_max: maximum numeric THC%
            population: exact population string
            outcome: outcome domains to search (comma-separated string or list)
            flags: list or comma-separated string of flags, prefixed with +/-
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
        if sort_by == "year":
            sql += " ORDER BY papers.year DESC, papers.id DESC"
        elif sort_by == "citations":
            sql += " ORDER BY papers.citation_count DESC, papers.year DESC"
        elif sort_by == "quality_score":
            sql += " ORDER BY papers.methodological_quality_score DESC, papers.year DESC"
        else:
            # Default sorting: Quality first
            if query_val:
                sql += " ORDER BY papers.methodological_quality_score DESC, rank ASC"
            else:
                sql += " ORDER BY papers.methodological_quality_score DESC, papers.year DESC, papers.id DESC"

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
                for json_field in ["authors", "outcome_domain", "methodological_quality_flags"]:
                    if res.get(json_field):
                        try:
                            res[json_field] = json.loads(res[json_field])
                        except Exception:
                            res[json_field] = []
                    else:
                        res[json_field] = []

                for json_field in ["study_type", "exposure_method", "cannabis_type", "population"]:
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
