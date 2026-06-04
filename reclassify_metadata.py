# reclassify_metadata.py
import sqlite3
import json
import logging
import os
from db_manager import DatabaseManager
import extractor

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def parse_old_list(val):
    if not val:
        return []
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                return json.loads(val)
            except Exception:
                pass
        return [val]
    return val

def reclassify_all_papers():
    """Iterates through all papers in the SQLite database and re-classifies:
    study_type, exposure_method, population, cannabis_type, and publication_type.
    """
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Fetch all papers
        cursor.execute(
            """
            SELECT id, title, abstract, study_type, exposure_method, population,
                   thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
                   duration_days, sample_size, journal, cannabis_type, publication_type
            FROM papers
            """
        )
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No papers found in the database to reclassify.")
            return
            
        logger.info(f"Loaded {len(papers)} papers from the catalog for reclassification.")
        
        update_count = 0
        study_type_changes = 0
        exposure_changes = 0
        population_changes = 0
        cannabis_type_changes = 0
        
        for p in papers:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            
            # 1. Infer active classifications focusing on the Methods section if present
            new_publication_type = extractor.infer_publication_type(title, abstract)
            new_study_type = extractor.infer_study_type(title, abstract)
            new_population = extractor.infer_population(title, abstract, new_study_type)
            new_exposure_method = extractor.infer_exposure_method(title, abstract, new_study_type, new_population)
            new_cannabis_type = extractor.infer_cannabis_type(title, abstract, new_study_type, new_exposure_method)
            
            # 2. Check for changes
            old_pub_type = p.get("publication_type")
            old_study_type = parse_old_list(p.get("study_type"))
            old_exposure = parse_old_list(p.get("exposure_method"))
            old_population = parse_old_list(p.get("population"))
            old_cannabis_type = parse_old_list(p.get("cannabis_type"))
            
            has_changes = (
                new_publication_type != old_pub_type or
                sorted(new_study_type) != sorted(old_study_type) or
                sorted(new_exposure_method) != sorted(old_exposure) or
                sorted(new_population) != sorted(old_population) or
                sorted(new_cannabis_type) != sorted(old_cannabis_type)
            )
            
            if has_changes:
                if sorted(new_study_type) != sorted(old_study_type):
                    study_type_changes += 1
                if sorted(new_exposure_method) != sorted(old_exposure):
                    exposure_changes += 1
                if sorted(new_population) != sorted(old_population):
                    population_changes += 1
                if sorted(new_cannabis_type) != sorted(old_cannabis_type):
                    cannabis_type_changes += 1
                
                # Update database
                cursor.execute(
                    """
                    UPDATE papers
                    SET study_type = ?,
                        exposure_method = ?,
                        population = ?,
                        cannabis_type = ?,
                        publication_type = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(new_study_type),
                        json.dumps(new_exposure_method),
                        json.dumps(new_population),
                        json.dumps(new_cannabis_type),
                        new_publication_type,
                        p["id"]
                    )
                )
                update_count += 1
                
                if p["id"] == 6895 or "Mitochondrial DNA" in title:
                    logger.info(f"Updated specific paper ID {p['id']}: {title[:60]}...")
                    logger.info(f"  - Publication Type: {old_pub_type} -> {new_publication_type}")
                    logger.info(f"  - Study Type: {old_study_type} -> {new_study_type}")
                    logger.info(f"  - Exposure: {old_exposure} -> {new_exposure_method}")
                    logger.info(f"  - Population: {old_population} -> {new_population}")
                    logger.info(f"  - Cannabis Type: {old_cannabis_type} -> {new_cannabis_type}")
        
        conn.commit()
        logger.info(f"Finished reclassification.")
        logger.info(f"Total papers updated: {update_count}")
        logger.info(f"  - Study type changed for {study_type_changes} papers")
        logger.info(f"  - Exposure method changed for {exposure_changes} papers")
        logger.info(f"  - Population changed for {population_changes} papers")
        logger.info(f"  - Cannabis type changed/populated for {cannabis_type_changes} papers")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error during reclassification: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    reclassify_all_papers()
