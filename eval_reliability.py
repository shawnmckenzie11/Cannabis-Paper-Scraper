import os
import sys
import json
import sqlite3
import logging
from datetime import datetime

# Set up paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
RELIABILITY_MANIFEST_FILE = os.path.join(BASE_DIR, "reliability_manifest.json")
from db_manager import DatabaseManager
import extractor

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def normalize_list(val):
    if val is None:
        return []
    if isinstance(val, str):
        val = val.strip()
        if val.startswith('['):
            try:
                val = json.loads(val)
            except:
                pass
        else:
            val = [val]
    if not isinstance(val, list):
        val = [val]
    return sorted([str(x).lower().strip() for x in val if x and x.lower().strip() != "unknown"])

def get_broad_study_types(lst):
    categories = set()
    for x in lst:
        if "clinical" in x:
            categories.add("clinical")
        elif "animal" in x:
            categories.add("animal")
        elif "cell" in x or "vitro" in x or "co-culture" in x or "pcls" in x:
            categories.add("cell_culture")
        elif "review" in x or "meta-analysis" in x or "editorial" in x:
            categories.add("review")
    return categories

def main():
    # Detect connection type
    db = DatabaseManager()
    conn = db.get_connection()
    is_postgres = db.is_postgres
    
    if is_postgres:
        # PostgreSQL wrapped connection supports cursor()
        cursor = conn.cursor()
    else:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
    logger.info(f"Querying reclassified papers from database (Postgres={is_postgres})...")
    
    # Select all papers reclassified by LLM
    cursor.execute("""
        SELECT id, title, abstract, study_type, exposure_method, cannabis_type, publication_type, classifier_version
        FROM papers
        WHERE classifier_version LIKE 'llm-pdf-reclassify-%'
           OR classifier_version LIKE 'llm-reclassify-%'
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    logger.info(f"Found {len(rows)} reclassified papers.")
    if not rows:
        logger.warning("No reclassified papers found to calculate reliability.")
        return
        
    # Group results by broad study type of the ground truth (after PDF classification)
    groups = {
        "preclinical": [], # cell_culture and animal models
        "clinical": [],
        "review": []
    }
    
    for r in rows:
        pg_study = get_broad_study_types(normalize_list(r.get("study_type")))
        pub_type = r.get("publication_type") or ""
        
        if "review" in pub_type.lower() or "meta-analysis" in pub_type.lower() or "review" in pg_study:
            groups["review"].append(r)
        elif "clinical" in pg_study:
            groups["clinical"].append(r)
        elif "animal" in pg_study or "cell_culture" in pg_study:
            groups["preclinical"].append(r)
        else:
            # Fallback
            groups["preclinical"].append(r)
            
    # Calculate stats per group
    threshold = 0.75
    manifest = {
        "last_updated": datetime.now().isoformat(),
        "threshold": threshold,
        "metrics": {}
    }
    
    for group_name, group_papers in groups.items():
        if group_name == "review":
            continue # We ignore reviews in prompting loops
            
        total = len(group_papers)
        logger.info(f"Calculating stats for '{group_name}' group ({total} papers)...")
        
        if total == 0:
            manifest["metrics"][group_name] = {
                "study_type": {"score": 0.0, "reliable": False},
                "exposure_method": {"score": 0.0, "reliable": False},
                "cannabis_type": {"score": 0.0, "reliable": False}
            }
            continue
            
        study_matches = 0
        exposure_matches = 0
        cannabis_matches = 0
        
        for r in group_papers:
            title = r.get("title") or ""
            abstract = r.get("abstract") or ""
            
            # Heuristics (Tier 1)
            h = extractor.extract_all_heuristics(title, abstract)
            h_study = get_broad_study_types(normalize_list(h.get("study_type")))
            h_exposure = normalize_list(h.get("exposure_method"))
            h_cannabis = normalize_list(h.get("cannabis_type"))
            
            # Ground Truth (Tier 2)
            pg_study = get_broad_study_types(normalize_list(r.get("study_type")))
            pg_exposure = normalize_list(r.get("exposure_method"))
            pg_cannabis = normalize_list(r.get("cannabis_type"))
            
            # 1. Study Type (intersection)
            if h_study.intersection(pg_study):
                study_matches += 1
                
            # 2. Exposure Method (overlap)
            if len(h_exposure) > 0 and len(pg_exposure) > 0:
                if any(x in pg_exposure for x in h_exposure):
                    exposure_matches += 1
            elif len(h_exposure) == 0 and len(pg_exposure) == 0:
                exposure_matches += 1
                
            # 3. Cannabis Type (overlap)
            if len(h_cannabis) > 0 and len(pg_cannabis) > 0:
                if any(x in pg_cannabis for x in h_cannabis):
                    cannabis_matches += 1
            elif len(h_cannabis) == 0 and len(pg_cannabis) == 0:
                cannabis_matches += 1
                
        study_score = study_matches / total
        exposure_score = exposure_matches / total
        cannabis_score = cannabis_matches / total
        
        manifest["metrics"][group_name] = {
            "study_type": {
                "score": round(study_score, 3),
                "reliable": study_score >= threshold
            },
            "exposure_method": {
                "score": round(exposure_score, 3),
                "reliable": exposure_score >= threshold
            },
            "cannabis_type": {
                "score": round(cannabis_score, 3),
                "reliable": cannabis_score >= threshold
            }
        }
        
        logger.info(f"Group '{group_name}' metrics:")
        logger.info(f"  - Study Type:      {study_score*100:.1f}% (reliable={study_score >= threshold})")
        logger.info(f"  - Exposure Method: {exposure_score*100:.1f}% (reliable={exposure_score >= threshold})")
        logger.info(f"  - Cannabis Type:   {cannabis_score*100:.1f}% (reliable={cannabis_score >= threshold})")
        
    # Write manifest file
    with open(RELIABILITY_MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        
    logger.info(f"Successfully generated reliability manifest at: {RELIABILITY_MANIFEST_FILE}")

if __name__ == "__main__":
    main()
