# calculate_durations.py
import sqlite3
import logging
from db_manager import DatabaseManager
import extractor

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def migrate_study_durations():
    """Calculates study durations for all original articles in the SQLite database

    using updated, context-aware and age-rejecting heuristics, then commits them in a transaction.
    """
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # Fetch all original articles (exclude review and meta-analysis)
        cursor.execute(
            """
            SELECT id, title, abstract, study_type, duration_days
            FROM papers
            WHERE study_type NOT IN ('review', 'meta-analysis')
            """
        )
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No original articles found in the database to update.")
            return
            
        logger.info(f"Loaded {len(papers)} original articles from the database for duration recalculation.")
        
        updated_count = 0
        changed_count = 0
        newly_populated_count = 0
        cleared_count = 0
        
        duration_categories = {
            "years": 0,
            "months": 0,
            "days": 0,
            "N/A": 0
        }
        
        for p in papers:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            old_val = p.get("duration_days")
            
            # Extract duration using refined context-aware logic
            new_val = extractor.extract_duration_days(abstract)
            if new_val is None:
                new_val = extractor.extract_duration_days(title)
                
            # Track categories
            if new_val is not None:
                if new_val >= 365:
                    duration_categories["years"] += 1
                elif new_val >= 30:
                    duration_categories["months"] += 1
                else:
                    duration_categories["days"] += 1
            else:
                duration_categories["N/A"] += 1
                
            # Check if value has changed
            # (using a small delta for floats, though exact equality or None is fine)
            has_changed = False
            if old_val is None and new_val is not None:
                has_changed = True
                newly_populated_count += 1
            elif old_val is not None and new_val is None:
                has_changed = True
                cleared_count += 1
            elif old_val is not None and new_val is not None:
                if abs(old_val - new_val) > 0.001:
                    has_changed = True
                    changed_count += 1
                    
            if has_changed:
                cursor.execute(
                    """
                    UPDATE papers
                    SET duration_days = ?
                    WHERE id = ?
                    """,
                    (new_val, p["id"])
                )
                updated_count += 1
                
        conn.commit()
        
        logger.info("Finished study duration migration.")
        logger.info(f"Total papers updated: {updated_count}")
        logger.info(f"  - Newly populated: {newly_populated_count} papers")
        logger.info(f"  - Changed duration: {changed_count} papers")
        logger.info(f"  - Cleared (false positive age/dates): {cleared_count} papers")
        logger.info("New duration distribution across original articles:")
        logger.info(f"  - Years (>= 365 days): {duration_categories['years']} papers")
        logger.info(f"  - Months (>= 30 days): {duration_categories['months']} papers")
        logger.info(f"  - Days (< 30 days): {duration_categories['days']} papers")
        logger.info(f"  - N/A: {duration_categories['N/A']} papers")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error during study duration migration: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_study_durations()
