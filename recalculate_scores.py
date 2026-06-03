# recalculate_scores.py
import sqlite3
import json
import logging
from db_manager import DatabaseManager
import extractor
import classifier

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def recalculate_all_scores():
    """Recalculates quality scores for all papers in the SQLite database based on the updated 0-20 rubric."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # Fetch all papers with necessary fields for heuristics and classification
        cursor.execute(
            """
            SELECT id, title, abstract, study_type, dose_mg, strain_reported, 
                   strain_normalized, sample_size, journal, methodological_quality_flags,
                   methodological_quality_score
            FROM papers
            """
        )
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No papers found in the database to re-score.")
            return
            
        logger.info(f"Loaded {len(papers)} papers from the catalog for score recalculation.")
        
        update_count = 0
        score_changes = 0
        
        for p in papers:
            # Parse JSON fields safely
            flags = p.get("methodological_quality_flags") or []
            if isinstance(flags, str):
                try:
                    flags = json.loads(flags)
                except Exception:
                    flags = []
            
            # Formulate the payload representing the paper
            paper_payload = {
                "study_type": p.get("study_type"),
                "dose_mg": p.get("dose_mg"),
                "strain_reported": p.get("strain_reported"),
                "strain_normalized": p.get("strain_normalized"),
                "sample_size": p.get("sample_size"),
                "journal": p.get("journal"),
                "methodological_quality_flags": flags
            }
            
            # Detect new heuristics additions
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            multiple_doses = extractor.detect_multiple_doses(title, abstract)
            multiple_time_intervals = extractor.detect_multiple_time_intervals(title, abstract)
            
            paper_payload["multiple_doses"] = multiple_doses
            paper_payload["multiple_time_intervals"] = multiple_time_intervals
            
            # Calculate updated score using updated rubric
            new_score = classifier.calculate_quality_score(paper_payload)
            old_score = p.get("methodological_quality_score")
            
            if new_score != old_score:
                score_changes += 1
                
            # Update the paper record in the database
            cursor.execute(
                """
                UPDATE papers 
                SET methodological_quality_score = ?
                WHERE id = ?
                """,
                (new_score, p["id"])
            )
            update_count += 1
            
        conn.commit()
        logger.info(f"Successfully re-scored {update_count} papers in the database.")
        logger.info(f"Scores changed for {score_changes} out of {update_count} papers.")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to recalculate quality scores: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    recalculate_all_scores()
