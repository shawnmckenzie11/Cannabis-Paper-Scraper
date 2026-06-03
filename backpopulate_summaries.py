# backpopulate_summaries.py
import sqlite3
import json
import logging
from db_manager import DatabaseManager
import extractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def backpopulate_summaries():
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # Fetch papers that don't have a summary
        cursor.execute(
            """
            SELECT id, title, abstract, study_type, exposure_method, population,
                   thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
                   duration_days, sample_size, journal, cannabis_type
            FROM papers
            WHERE summary IS NULL OR summary = ''
            """
        )
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No papers found needing summary backpopulation.")
            return
            
        logger.info(f"Loaded {len(papers)} papers needing summary backpopulation.")
        
        update_count = 0
        for p in papers:
            # Generate fallback summary using extractor
            # extractor.generate_heuristic_summary expects a dictionary with keys:
            # study_type, cannabis_type, exposure_method, population, strain_reported
            summary = extractor.generate_heuristic_summary(p)
            
            cursor.execute(
                "UPDATE papers SET summary = ? WHERE id = ?",
                (summary, p["id"])
            )
            update_count += 1
            if update_count % 2000 == 0:
                conn.commit()
                logger.info(f"Updated {update_count} summaries...")
                
        conn.commit()
        logger.info(f"Success! Backpopulated {update_count} papers with heuristic summaries.")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error backpopulating summaries: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    backpopulate_summaries()
