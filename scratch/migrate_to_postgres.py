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

def connect_pg():
    print(f"Connecting to remote PostgreSQL database...")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def migrate_table(table, columns, json_fields, sqlite_conn, pg_conn_ref):
    sqlite_cur = sqlite_conn.cursor()
    
    # 1. Disable triggers and drop indexes (once per table)
    pg_conn = pg_conn_ref[0]
    pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Check what indexes exist on target
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

    triggers_disabled = False
    try:
        pg_cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL;")
        pg_conn.commit()
        print(f"  Disabled triggers for {table}")
        triggers_disabled = True
    except Exception as e:
        print(f"  Could not disable triggers for {table}: {e}")
        pg_conn.rollback()

    # 2. Get current progress (max ID)
    has_id = (table != "system_metadata")
    last_id = 0
    if has_id:
        try:
            pg_cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table};")
            row = pg_cur.fetchone()
            last_id = list(row.values())[0] if isinstance(row, dict) else row[0]
            print(f"  Resuming {table} from id > {last_id}")
        except Exception as e:
            print(f"  Could not fetch max ID for {table}, starting from 0: {e}")
            pg_conn.rollback()

    # Get total count remaining in SQLite
    cols_str = ",".join(columns)
    if has_id:
        sqlite_cur.execute(f"SELECT COUNT(*) FROM {table} WHERE id > ?", (last_id,))
    else:
        sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
    total_remaining = sqlite_cur.fetchone()[0]
    print(f"  Total rows remaining to migrate for {table}: {total_remaining}")

    if total_remaining == 0:
        print(f"  All rows for {table} are already migrated.")
        # Enable triggers & recreate indexes
        enable_triggers_and_indexes(table, pg_conn_ref, indexes_dropped, triggers_disabled)
        return

    chunk_size = 10000
    total_migrated = 0
    
    col_indices = {col: i for i, col in enumerate(columns)}
    json_cols_indices = [col_indices[col] for col in json_fields.get(table, []) if col in col_indices]
    open_access_idx = col_indices.get("open_access") if table == "papers" else None
    
    while True:
        # Fetch chunk from SQLite
        if has_id:
            sqlite_cur.execute(f"SELECT {cols_str} FROM {table} WHERE id > ? ORDER BY id ASC LIMIT ?", (last_id, chunk_size))
        else:
            # For system_metadata, we just select all (and truncate target first)
            try:
                pg_cur.execute(f"TRUNCATE TABLE {table} CASCADE;")
                pg_conn.commit()
            except Exception:
                pg_conn.rollback()
            sqlite_cur.execute(f"SELECT {cols_str} FROM {table}")
            
        rows = sqlite_cur.fetchall()
        if not rows:
            break
            
        # Format rows to CSV
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer, delimiter=',', quotechar='"', doublequote=True, lineterminator='\n', quoting=csv.QUOTE_MINIMAL)
        
        chunk_max_id = last_id
        for row in rows:
            val_list = list(row)
            if has_id:
                chunk_max_id = max(chunk_max_id, val_list[col_indices["id"]])
                
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
        
        # Write to Postgres with retry safety
        retries = 5
        while retries > 0:
            try:
                pg_conn = pg_conn_ref[0]
                pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                columns_escaped = [f'"{col}"' for col in columns]
                copy_sql = f"COPY {table} ({','.join(columns_escaped)}) FROM STDIN WITH (FORMAT CSV, DELIMITER ',', NULL '', QUOTE '\"', ESCAPE '\"')"
                pg_cur.copy_expert(copy_sql, csv_buffer)
                pg_conn.commit()
                
                # Update progress
                last_id = chunk_max_id
                total_migrated += len(rows)
                print(f"  Migrated {total_migrated}/{total_remaining} rows for {table} via COPY...", flush=True)
                
                # Backpressure sleep (500ms)
                time.sleep(0.5)
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                retries -= 1
                print(f"  [ERROR] Database connection lost during COPY: {e}. Retries remaining: {retries}")
                try:
                    pg_conn.close()
                except Exception:
                    pass
                time.sleep(30)
                try:
                    pg_conn_ref[0] = connect_pg()
                except Exception as ex:
                    print(f"  [ERROR] Reconnection failed: {ex}")
                if retries == 0:
                    raise e
        
        if not has_id:
            # system_metadata has no auto-increment ID, we copied all
            break

    # 3. Enable triggers & recreate indexes
    enable_triggers_and_indexes(table, pg_conn_ref, indexes_dropped, triggers_disabled)

def enable_triggers_and_indexes(table, pg_conn_ref, indexes_dropped, triggers_disabled):
    # Enable triggers
    retries = 5
    while retries > 0:
        try:
            pg_conn = pg_conn_ref[0]
            pg_cur = pg_conn.cursor()
            if triggers_disabled:
                pg_cur.execute(f"ALTER TABLE {table} ENABLE TRIGGER ALL;")
                pg_conn.commit()
                print(f"  Re-enabled triggers for {table}")
            break
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            retries -= 1
            print(f"  [ERROR] Database connection lost while enabling triggers for {table}: {e}. Retries remaining: {retries}")
            try:
                pg_conn_ref[0].close()
            except Exception:
                pass
            time.sleep(30)
            try:
                pg_conn_ref[0] = connect_pg()
            except Exception as ex:
                print(f"  [ERROR] Reconnection failed: {ex}")
            if retries == 0:
                raise e
        except Exception as e:
            print(f"  Failed to re-enable triggers for {table}: {e}")
            try:
                pg_conn_ref[0].rollback()
            except Exception:
                pass
            break

    # Recreate indexes
    if table == "citation_edges" and indexes_dropped:
        indexes = [
            ("idx_ce_source", "CREATE INDEX IF NOT EXISTS idx_ce_source ON citation_edges(source_paper_id);"),
            ("idx_ce_target", "CREATE INDEX IF NOT EXISTS idx_ce_target ON citation_edges(target_paper_id);"),
            ("idx_ce_rel", "CREATE INDEX IF NOT EXISTS idx_ce_rel ON citation_edges(relationship);"),
            ("idx_ce_ext", "CREATE INDEX IF NOT EXISTS idx_ce_ext ON citation_edges(target_external_id);")
        ]
        
        for idx_name, idx_sql in indexes:
            retries = 5
            while retries > 0:
                try:
                    pg_conn = pg_conn_ref[0]
                    pg_cur = pg_conn.cursor()
                    print(f"  Recreating index {idx_name} for citation_edges...")
                    pg_cur.execute("SET maintenance_work_mem = '16MB';")
                    pg_cur.execute(idx_sql)
                    pg_conn.commit()
                    print(f"  Successfully recreated index {idx_name} for citation_edges.")
                    break
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    retries -= 1
                    print(f"  [ERROR] Database connection lost while recreating index {idx_name}: {e}. Retries remaining: {retries}")
                    try:
                        pg_conn_ref[0].close()
                    except Exception:
                        pass
                    time.sleep(30)
                    try:
                        pg_conn_ref[0] = connect_pg()
                    except Exception as ex:
                        print(f"  [ERROR] Reconnection failed: {ex}")
                    if retries == 0:
                        raise e
                except Exception as e:
                    print(f"  Failed to recreate index {idx_name} for citation_edges: {e}")
                    try:
                        pg_conn_ref[0].rollback()
                    except Exception:
                        pass
                    break
                    
    elif table == "papers" and indexes_dropped:
        retries = 5
        while retries > 0:
            try:
                pg_conn = pg_conn_ref[0]
                pg_cur = pg_conn.cursor()
                print("  Recreating GIN index for papers...")
                pg_cur.execute("SET maintenance_work_mem = '16MB';")
                pg_cur.execute("CREATE INDEX IF NOT EXISTS idx_papers_fts ON papers USING GIN (to_tsvector('english', title || ' ' || coalesce(abstract, '')));")
                pg_conn.commit()
                print("  Successfully recreated GIN index for papers.")
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                retries -= 1
                print(f"  [ERROR] Database connection lost while recreating GIN index for papers: {e}. Retries remaining: {retries}")
                try:
                    pg_conn_ref[0].close()
                except Exception:
                    pass
                time.sleep(30)
                try:
                    pg_conn_ref[0] = connect_pg()
                except Exception as ex:
                    print(f"  [ERROR] Reconnection failed: {ex}")
                if retries == 0:
                    raise e
            except Exception as e:
                print(f"  Failed to recreate GIN index for papers: {e}")
                try:
                    pg_conn_ref[0].rollback()
                except Exception:
                    pass
                break

def migrate():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("Please export DATABASE_URL before running this script.")
        sys.exit(1)

    print("Checking database schema status...")
    # Skip init_db if schema already exists to prevent OOM/timeouts on memory-constrained Postgres
    pg_conn = connect_pg()
    pg_cur = pg_conn.cursor()
    pg_cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'papers');")
    schema_exists = pg_cur.fetchone()[0]
    pg_cur.close()
    pg_conn.close()

    if not schema_exists:
        print("Initializing DatabaseManager to ensure PostgreSQL tables and indexes exist...")
        from db_manager import DatabaseManager
        db = DatabaseManager()
        db.init_db()
    else:
        print("PostgreSQL schema already exists. Skipping init_db() to avoid timeout/lock issues.")

    print(f"Connecting to local SQLite database: {SQLITE_DB}")
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row

    # Remote PG connection ref so it can be updated in-place during reconnects
    pg_conn = connect_pg()
    pg_conn_ref = [pg_conn]

    tables = ["papers", "system_metadata", "feedback_audit", "llm_calls_log", "citation_edges"]

    json_fields = {
        "papers": ["authors", "outcome_domain", "expert_locked_fields"],
        "feedback_audit": [],
        "llm_calls_log": [],
        "citation_edges": ["metadata"]
    }

    try:
        for table in tables:
            print(f"\nMigrating table: {table}...")
            
            # Fetch target table columns
            pg_cur = pg_conn_ref[0].cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            pg_cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table.lower(),))
            pg_cols = {r["column_name"].lower() for r in pg_cur.fetchall()}
            
            sqlite_cur = sqlite_conn.cursor()
            sqlite_cur.execute(f"PRAGMA table_info({table})")
            sqlite_cols = [r["name"] for r in sqlite_cur.fetchall()]
            
            columns = [col for col in sqlite_cols if col.lower() in pg_cols]
            if not columns:
                print(f"  No matching columns to migrate for {table}.")
                continue
                
            migrate_table(table, columns, json_fields, sqlite_conn, pg_conn_ref)
            
        print("\nAll tables migrated successfully!")

        # Update sequences
        print("Updating auto-increment sequences...")
        pg_cur = pg_conn_ref[0].cursor()
        for table in ["papers", "feedback_audit", "llm_calls_log", "citation_edges"]:
            try:
                pg_cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table};")
                pg_conn_ref[0].commit()
                print(f"  Updated sequence for {table}")
            except Exception as e:
                print(f"  Failed to update sequence for {table}: {e}")
                pg_conn_ref[0].rollback()

        print("Migration completed successfully!")
        
    finally:
        sqlite_conn.close()
        try:
            pg_conn_ref[0].close()
        except Exception:
            pass

if __name__ == "__main__":
    migrate()
