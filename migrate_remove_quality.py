# migrate_remove_quality.py
import os
import sqlite3
import logging

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

DATABASE_FILE = os.getenv("DATABASE_PATH", "cannabis_papers.db")

def run_migration():
    logger.info(f"Connecting to database: {DATABASE_FILE}")
    if not os.path.exists(DATABASE_FILE):
        logger.warning(f"Database file not found at {DATABASE_FILE}. Skipping migration.")
        return
        
    conn = sqlite3.connect(DATABASE_FILE)
    try:
        cursor = conn.cursor()
        
        # Check current columns in papers table
        cursor.execute("PRAGMA table_info(papers);")
        columns = [row[1] for row in cursor.fetchall()]
        logger.info(f"Current columns in 'papers' table: {columns}")
        
        # Drop methodological_quality_score if exists
        if "methodological_quality_score" in columns:
            logger.info("Dropping column 'methodological_quality_score' from 'papers' table...")
            cursor.execute("ALTER TABLE papers DROP COLUMN methodological_quality_score;")
            logger.info("Column 'methodological_quality_score' dropped successfully.")
        else:
            logger.info("Column 'methodological_quality_score' does not exist in 'papers' table.")
            
        # Drop methodological_quality_flags if exists
        if "methodological_quality_flags" in columns:
            logger.info("Dropping column 'methodological_quality_flags' from 'papers' table...")
            cursor.execute("ALTER TABLE papers DROP COLUMN methodological_quality_flags;")
            logger.info("Column 'methodological_quality_flags' dropped successfully.")
        else:
            logger.info("Column 'methodological_quality_flags' does not exist in 'papers' table.")
            
        conn.commit()
        logger.info("Database migration completed successfully.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to migrate database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
