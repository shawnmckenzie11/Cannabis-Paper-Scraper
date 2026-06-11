# scratch/migrate_to_postgres.py
import sqlite3
import os
import sys
import json
import io
import csv
from datetime import datetime
import time

# Add parent directory to path so we can import db_manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")
SQLITE_DB = "cannabis_papers.db"

def migrate():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("Please export DATABASE_URL before running this script.")
        sys.exit(1)

    # Early truncation using raw psycopg2 connection to ensure tables are empty
    # before DatabaseManager runs and tries to build index on pre-existing rows.
    try:
        print("Clearing database tables using raw connection to speed up initialization...")
        raw_conn = psycopg2.connect(DATABASE_URL)
        raw_cur = raw_conn.cursor()
        for t in ["citation_edges", "llm_calls_log", "feedback_audit", "system_metadata", "papers"]:
            try:
                raw_cur.execute(f"TRUNCATE TABLE {t} CASCADE;")
            except Exception:
                pass
        raw_conn.commit()
        raw_cur.close()
        raw_conn.close()
    except Exception as e:
        print(f"Warning: Early truncation failed (probably table doesn't exist yet): {e}")

    print("Initializing DatabaseManager to ensure PostgreSQL tables and indexes exist...")
    from db_manager import DatabaseManager
    db = DatabaseManager()
    db.init_db()

    print(f"Connecting to local SQLite database: {SQLITE_DB}")
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    print(f"Connecting to remote PostgreSQL database...")
    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    tables = ["papers", "system_metadata", "feedback_audit", "llm_calls_log", "citation_edges"]

    # Table columns to handle as JSONB
    json_fields = {
        "papers": ["authors", "outcome_domain", "expert_locked_fields"],
        "feedback_audit": [],
        "llm_calls_log": [],
        "citation_edges": ["metadata"]
    }

    try:
        # 1. Clean existing records in target database for fresh migration
        print("Clearing existing data in remote PostgreSQL tables...")
        for table in reversed(tables):
            try:
                pg_cur.execute(f"TRUNCATE TABLE {table} CASCADE;")
                print(f"  Truncated {table}")
            except Exception as e:
                print(f"  Could not truncate {table}: {e}")
                pg_conn.rollback()

        pg_conn.commit()

        # 2. Copy data table by table using COPY FROM STDIN
        for table in tables:
            print(f"Migrating table: {table}...")
            
            # Fetch column intersection to avoid legacy columns like 'population'
            pg_cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table.lower(),))
            pg_cols = {r["column_name"].lower() for r in pg_cur.fetchall()}
            
            sqlite_cur.execute(f"PRAGMA table_info({table})")
            sqlite_cols = [r["name"] for r in sqlite_cur.fetchall()]
            
            columns = [col for col in sqlite_cols if col.lower() in pg_cols]
            if not columns:
                print(f"  No matching columns to migrate for {table}.")
                continue
                
            cols_str = ",".join(columns)
            sqlite_cur.execute(f"SELECT count(*) FROM {table}")
            total_rows = sqlite_cur.fetchone()[0]
            print(f"  Total rows to migrate in SQLite {table}: {total_rows}")
            
            if total_rows == 0:
                print(f"  No rows to migrate in {table}.")
                continue

            # Drop indexes before COPY to speed it up
            indexes_dropped = False
            if table == "citation_edges":
                try:
                    print("  Dropping indexes for citation_edges to speed up load...")
                    pg_cur.execute("DROP INDEX IF EXISTS idx_ce_source;")
                    pg_cur.execute("DROP INDEX IF EXISTS idx_ce_target;")
                    pg_cur.execute("DROP INDEX IF EXISTS idx_ce_rel;")
                    pg_cur.execute("DROP INDEX IF EXISTS idx_ce_ext;")
                    pg_conn.commit()
                    indexes_dropped = True
                except Exception as e:
                    print(f"  Could not drop indexes for citation_edges: {e}")
                    pg_conn.rollback()
            elif table == "papers":
                try:
                    print("  Dropping GIN index for papers to speed up load...")
                    pg_cur.execute("DROP INDEX IF EXISTS idx_papers_fts;")
                    pg_conn.commit()
                    indexes_dropped = True
                except Exception as e:
                    print(f"  Could not drop GIN index for papers: {e}")
                    pg_conn.rollback()

            # Disable triggers for this table to bypass foreign key checks during COPY
            triggers_disabled = False
            try:
                pg_cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL;")
                pg_conn.commit()
                print(f"  Disabled triggers for {table}")
                triggers_disabled = True
            except Exception as e:
                print(f"  Could not disable triggers for {table} (proceeding without disabling): {e}")
                pg_conn.rollback()

            sqlite_cur.execute(f"SELECT {cols_str} FROM {table}")
            
            # Read in chunks and stream using COPY
            # Use a smaller chunk size to prevent connection drops on Fly proxy (20,000)
            chunk_size = 20000
            total_migrated = 0
            # Precompute column indices for O(1) loop lookup
            col_indices = {col: i for i, col in enumerate(columns)}
            json_cols_indices = [col_indices[col] for col in json_fields.get(table, []) if col in col_indices]
            open_access_idx = col_indices.get("open_access") if table == "papers" else None
            
            try:
                while True:
                    rows = sqlite_cur.fetchmany(chunk_size)
                    if not rows:
                        break
                    
                    # Write chunk of rows to memory CSV
                    csv_buffer = io.StringIO()
                    # Standard CSV format with double-quoting of special characters
                    writer = csv.writer(csv_buffer, delimiter=',', quotechar='"', doublequote=True, lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
                    
                    for row in rows:
                        val_list = list(row)
                        for idx in json_cols_indices:
                            val = val_list[idx]
                            if val:
                                try:
                                    parsed_val = json.loads(val)
                                    val_list[idx] = json.dumps(parsed_val)
                                except Exception:
                                    pass
                            else:
                                if columns[idx] == "expert_locked_fields":
                                    val_list[idx] = "[]"
                                elif columns[idx] == "metadata":
                                    val_list[idx] = "{}"
                                else:
                                    val_list[idx] = None
                        
                        if open_access_idx is not None:
                            val_list[open_access_idx] = 1 if val_list[open_access_idx] else 0
                            
                        writer.writerow(val_list)
                    
                    csv_buffer.seek(0)
                    
                    # PostgreSQL COPY statement using standard CSV options
                    columns_escaped = [f'"{col}"' for col in columns]
                    copy_sql = f"COPY {table} ({','.join(columns_escaped)}) FROM STDIN WITH (FORMAT CSV, DELIMITER ',', NULL '', QUOTE '\"', ESCAPE '\"')"
                    pg_cur.copy_expert(copy_sql, csv_buffer)
                    pg_conn.commit()
                    
                    # Backpressure sleep to prevent resource starvation on 256MB Fly VM
                    time.sleep(0.2)
                    
                    total_migrated += len(rows)
                    print(f"  Migrated {total_migrated}/{total_rows} rows into remote {table} via COPY...", flush=True)

                print(f"  Successfully finished migrating {table}.", flush=True)

            finally:
                # Always ensure triggers are re-enabled even if COPY fails
                if triggers_disabled:
                    try:
                        pg_cur.execute(f"ALTER TABLE {table} ENABLE TRIGGER ALL;")
                        pg_conn.commit()
                        print(f"  Re-enabled triggers for {table}")
                    except Exception as e:
                        print(f"  Failed to re-enable triggers for {table}: {e}")
                        pg_conn.rollback()

                # Always recreate indexes for citation_edges
                if table == "citation_edges" and indexes_dropped:
                    try:
                        print("  Recreating indexes for citation_edges...")
                        pg_cur.execute("SET maintenance_work_mem = '16MB';")
                        pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_source ON citation_edges(source_paper_id);")
                        pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_target ON citation_edges(target_paper_id);")
                        pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_rel ON citation_edges(relationship);")
                        pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_ce_ext ON citation_edges(target_external_id);")
                        pg_conn.commit()
                        print("  Successfully recreated indexes for citation_edges.")
                    except Exception as e:
                        print(f"  Failed to recreate indexes for citation_edges: {e}")
                        pg_conn.rollback()
                elif table == "papers" and indexes_dropped:
                    try:
                        print("  Recreating GIN index for papers...")
                        pg_cur.execute("SET maintenance_work_mem = '16MB';")
                        pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_fts ON papers USING GIN (to_tsvector('english', title || ' ' || coalesce(abstract, '')));")
                        pg_conn.commit()
                        print("  Successfully recreated GIN index for papers.")
                    except Exception as e:
                        print(f"  Failed to recreate GIN index for papers: {e}")
                        pg_conn.rollback()

        print("Migration completed successfully!")
        
        # 3. Synchronize auto-incrementing ID sequences in PostgreSQL
        print("Updating auto-increment sequences...")
        for table in ["papers", "feedback_audit", "llm_calls_log", "citation_edges"]:
            try:
                pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table};")
                pg_conn.commit()
                print(f"  Updated sequence for {table}")
            except Exception as e:
                print(f"  Failed to update sequence for {table}: {e}")
                pg_conn.rollback()

    except Exception as e:
        print(f"Migration failed with error: {e}")
        pg_conn.rollback()
        raise e
    finally:
        sqlite_conn.close()
        pg_conn.close()

if __name__ == "__main__":
    migrate()
