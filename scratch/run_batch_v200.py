import os
import sys
import logging
import sqlite3
import psycopg2
import json

sys.path.append("/Users/shawnscomputer/Documents/Cannabis Paper Scraper")
from reclassify_with_llm import reclassify_papers_llm

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

pids = [11712, 12519, 13015, 7100, 7230, 7642, 4280, 4668, 4685, 4823]

def sync_to_postgres():
    logger.info("Initializing sync of reclassified papers from SQLite to PostgreSQL...")
    
    sqlite_conn = sqlite3.connect("/Users/shawnscomputer/Documents/Cannabis Paper Scraper/cannabis_papers.db")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()
    
    pg_conn = psycopg2.connect("postgres://postgres:AXLq1wmqeKLTsF2@localhost:15432/cannabis_paper_scraper")
    pg_cur = pg_conn.cursor()
    
    fields = [
        "study_type", "exposure_method", "cannabis_type", "publication_type",
        "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
        "strain_reported", "strain_normalized", "duration_days",
        "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
        "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
        "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM",
        "classifier_version", "classification_timestamp", "classification_confidence"
    ]
    
    for pid in pids:
        # Fetch from SQLite
        sqlite_cur.execute(f"SELECT {', '.join(fields)} FROM papers WHERE id = ?", (pid,))
        row = sqlite_cur.fetchone()
        if not row:
            logger.warning(f"Paper {pid} not found in SQLite.")
            continue
            
        row_dict = dict(row)
        
        # Prepare PostgreSQL UPDATE
        set_clauses = []
        params = []
        for field in fields:
            val = row_dict[field]
            set_clauses.append(f"{field} = %s")
            params.append(val)
            
        params.append(pid)
        sql = f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = %s"
        
        pg_cur.execute(sql, params)
        logger.info(f"Synced paper ID {pid} to PostgreSQL.")
        
    pg_conn.commit()
    sqlite_conn.close()
    pg_conn.close()
    logger.info("Sync complete!")

def main():
    # Make sure DATABASE_URL is NOT set in the environment so reclassification runs on SQLite locally
    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]
        
    logger.info(f"Starting batch classification of 10 papers (v2.0.0) on SQLite: {pids}")
    
    for pid in pids:
        try:
            logger.info(f"\n=========================================")
            logger.info(f"RECLASSIFYING PAPER ID {pid}")
            logger.info(f"=========================================")
            reclassify_papers_llm(paper_id=pid)
        except Exception as e:
            logger.error(f"Failed to reclassify paper {pid}: {e}")
            
    # Sync to Postgres
    try:
        sync_to_postgres()
    except Exception as e:
        logger.error(f"Failed to sync reclassifications to Postgres: {e}")

if __name__ == "__main__":
    main()
