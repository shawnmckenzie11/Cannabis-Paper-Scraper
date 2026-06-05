# reclassify_metadata.py
import sqlite3
import json
import logging
import os
from datetime import datetime
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
        # Load rules config version
        rules_version = "1.0.0"
        if os.path.exists("rules_config.json"):
            try:
                with open("rules_config.json", "r") as f:
                    rules_version = json.load(f).get("version", "1.0.0")
            except Exception:
                pass

        # Fetch all papers
        cursor.execute(
            """
            SELECT id, title, abstract, study_type, exposure_method, population,
                   thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
                   duration_days, sample_size, journal, cannabis_type, publication_type,
                   expert_locked_fields
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
            new_study_type = extractor.postprocess_study_type(new_study_type, new_population)
            new_exposure_method = extractor.infer_exposure_method(title, abstract, new_study_type, new_population)
            new_cannabis_type = extractor.infer_cannabis_type(title, abstract, new_study_type, new_exposure_method)
            
            # 2. Check for changes
            old_pub_type = p.get("publication_type")
            old_study_type = parse_old_list(p.get("study_type"))
            old_exposure = parse_old_list(p.get("exposure_method"))
            old_population = parse_old_list(p.get("population"))
            old_cannabis_type = parse_old_list(p.get("cannabis_type"))
            old_summary = p.get("summary") or ""
            
            # Respect locked fields
            locked_fields = p.get("expert_locked_fields") or []
            if isinstance(locked_fields, str):
                try:
                    locked_fields = json.loads(locked_fields)
                except Exception:
                    locked_fields = []
            if not isinstance(locked_fields, list):
                locked_fields = []

            # Assign values based on locking
            final_publication_type = old_pub_type if "publication_type" in locked_fields else new_publication_type
            final_study_type = old_study_type if "study_type" in locked_fields else new_study_type
            final_population = old_population if "population" in locked_fields else new_population
            final_exposure_method = old_exposure if "exposure_method" in locked_fields else new_exposure_method
            final_cannabis_type = old_cannabis_type if "cannabis_type" in locked_fields else new_cannabis_type
            
            # Check if summary is heuristic-generated
            is_heuristic_summary = (
                not old_summary or 
                old_summary.startswith("This is a ") or 
                old_summary.startswith("This is an ")
            )
            
            if is_heuristic_summary and "summary" not in locked_fields:
                new_summary = extractor.generate_heuristic_summary({
                    "study_type": final_study_type,
                    "cannabis_type": final_cannabis_type,
                    "exposure_method": final_exposure_method,
                    "population": final_population,
                    "strain_reported": p.get("strain_reported")
                })
            else:
                new_summary = old_summary
            
            has_changes = (
                final_publication_type != old_pub_type or
                sorted(final_study_type) != sorted(old_study_type) or
                sorted(final_exposure_method) != sorted(old_exposure) or
                sorted(final_population) != sorted(old_population) or
                sorted(final_cannabis_type) != sorted(old_cannabis_type) or
                new_summary != old_summary
            )
            
            if has_changes:
                if sorted(final_study_type) != sorted(old_study_type):
                    study_type_changes += 1
                if sorted(final_exposure_method) != sorted(old_exposure):
                    exposure_changes += 1
                if sorted(final_population) != sorted(old_population):
                    population_changes += 1
                if sorted(final_cannabis_type) != sorted(old_cannabis_type):
                    cannabis_type_changes += 1
                
                # Update database
                cursor.execute(
                    """
                    UPDATE papers
                    SET study_type = ?,
                        exposure_method = ?,
                        population = ?,
                        cannabis_type = ?,
                        publication_type = ?,
                        summary = ?,
                        classifier_version = ?,
                        classification_timestamp = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(final_study_type),
                        json.dumps(final_exposure_method),
                        json.dumps(final_population),
                        json.dumps(final_cannabis_type),
                        final_publication_type,
                        new_summary,
                        f"heuristic-reclassify-{rules_version}",
                        datetime.now().isoformat(),
                        p["id"]
                    )
                )
                update_count += 1
                
                if p["id"] == 6895 or "Mitochondrial DNA" in title:
                    logger.info(f"Updated specific paper ID {p['id']}: {title[:60]}...")
                    logger.info(f"  - Publication Type: {old_pub_type} -> {final_publication_type}")
                    logger.info(f"  - Study Type: {old_study_type} -> {final_study_type}")
                    logger.info(f"  - Exposure: {old_exposure} -> {final_exposure_method}")
                    logger.info(f"  - Population: {old_population} -> {final_population}")
                    logger.info(f"  - Cannabis Type: {old_cannabis_type} -> {final_cannabis_type}")
        
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
    from datetime import datetime
    reclassify_all_papers()
