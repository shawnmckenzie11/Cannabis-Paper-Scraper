import os
import sys
import json
import sqlite3

sys.path.append("/Users/shawnscomputer/Documents/Cannabis Paper Scraper")
os.environ["DATABASE_URL"] = "postgres://postgres:AXLq1wmqeKLTsF2@localhost:15432/cannabis_paper_scraper"
from db_manager import DatabaseManager

def main():
    pg = DatabaseManager()
    pg_conn = pg.get_connection()
    pg_cursor = pg_conn.cursor()
    
    pids = [11712, 12519, 13015, 7100, 7230, 7642, 4280, 4668, 4685, 4823]
    
    pg_cursor.execute("""
        SELECT id, title, study_type, exposure_method, cannabis_type, publication_type,
               thc_pct, cbd_pct, dose_mg, strain_reported, duration_days,
               treatment_duration, thc_uM, cbd_uM, thc_mg_kg, cbd_mg_kg,
               inhaled_exposure_duration, sample_size, classification_confidence, classifier_version
        FROM papers
        WHERE id IN %s
        ORDER BY id ASC
    """, (tuple(pids),))
    rows = pg_cursor.fetchall()
    
    print(f"Loaded {len(rows)} reclassified papers under v2.0.0.")
    print("=" * 120)
    for r in rows:
        print(f"Paper ID: {r['id']} | Version: {r['classifier_version']}")
        print(f"Title:    {r['title'][:90]}...")
        print(f"Study:    {r['study_type']}")
        print(f"Exposure: {r['exposure_method']}")
        print(f"Product:  {r['cannabis_type']}")
        print(f"Pub Type: {r['publication_type']}")
        print(f"Strain:   {r['strain_reported']}")
        print(f"Sample N: {r['sample_size']}")
        print(f"Conf:     {r['classification_confidence']:.2f}")
        
        # Dosages
        doses = []
        if r.get('dose_mg') is not None: doses.append(f"dose_mg: {r['dose_mg']} mg")
        if r.get('thc_mg_kg') is not None: doses.append(f"thc_mg_kg: {r['thc_mg_kg']} mg/kg")
        if r.get('cbd_mg_kg') is not None: doses.append(f"cbd_mg_kg: {r['cbd_mg_kg']} mg/kg")
        if r.get('thc_uM') is not None: doses.append(f"thc_uM: {r['thc_uM']} µM")
        if r.get('cbd_uM') is not None: doses.append(f"cbd_uM: {r['cbd_uM']} µM")
        if doses:
            print(f"Doses:    {', '.join(doses)}")
            
        # Durations
        durs = []
        if r.get('duration_days') is not None: durs.append(f"study_duration: {r['duration_days']} days")
        if r.get('treatment_duration') is not None: durs.append(f"treatment_duration: {r['treatment_duration']}")
        if r.get('inhaled_exposure_duration') is not None: durs.append(f"inhaled_session: {r['inhaled_exposure_duration']}")
        if durs:
            print(f"Durations:{', '.join(durs)}")
            
        print("-" * 120)
        
    pg_conn.close()

if __name__ == "__main__":
    main()
