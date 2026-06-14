import os
import sys
import sqlite3
import json

sys.path.append("/Users/shawnscomputer/Documents/Cannabis Paper Scraper")
os.environ["DATABASE_URL"] = "postgres://postgres:AXLq1wmqeKLTsF2@localhost:15432/cannabis_paper_scraper"
from db_manager import DatabaseManager

def main():
    # 1. Fetch all reclassified paper IDs from Postgres (both llm-reclassify and llm-pdf-reclassify)
    pg = DatabaseManager()
    pg_conn = pg.get_connection()
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute("SELECT id FROM papers WHERE classifier_version LIKE 'llm-%'")
    reclassified_ids = set(row['id'] for row in pg_cursor.fetchall())
    pg_conn.close()
    
    print(f"Total already reclassified paper IDs in Postgres: {len(reclassified_ids)}")
    
    # 2. Fetch candidates from SQLite
    sqlite_conn = sqlite3.connect("/Users/shawnscomputer/Documents/Cannabis Paper Scraper/cannabis_papers.db")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    sqlite_cursor.execute("""
        SELECT id, title, study_type, exposure_method, full_text_link, publication_type
        FROM papers
        WHERE full_text_link IS NOT NULL AND full_text_link != ''
    """)
    rows = sqlite_cursor.fetchall()
    
    cell_candidates = []
    animal_candidates = []
    clinical_candidates = []
    
    for r in rows:
        pid = r['id']
        if pid in reclassified_ids:
            continue
            
        link = r['full_text_link']
        is_pdf_url = ".pdf" in link.lower() or "/pdf/" in link.lower() or "springer.com" in link or "nature.com" in link or "ncbi.nlm.nih.gov/pmc/articles/PMC" in link or "tandfonline.com/doi/pdf" in link
        if not is_pdf_url:
            continue
            
        st_raw = r['study_type'] or ""
        em_raw = r['exposure_method'] or ""
        
        st_lower = st_raw.lower()
        em_lower = em_raw.lower()
        
        # Check cell culture indicators
        is_cell = "cell" in st_lower or "vitro" in st_lower or "vitro" in em_lower
        # Check animal model indicators
        is_animal = "animal" in st_lower or "mouse" in st_lower or "rat" in st_lower or "rodent" in st_lower
        # Check clinical indicators
        is_clinical = "clinical" in st_lower or "rct" in st_lower or "observational" in st_lower or "survey" in st_lower
        
        # Avoid reviews
        pub_type = r['publication_type'] or ""
        if "review" in pub_type.lower() or "meta-analysis" in pub_type.lower():
            continue
            
        if is_cell and not is_animal:
            cell_candidates.append(r)
        elif is_animal and not is_cell:
            animal_candidates.append(r)
        elif is_clinical and not is_animal and not is_cell:
            clinical_candidates.append(r)
            
    print(f"Available Cell candidates: {len(cell_candidates)}")
    print(f"Available Animal candidates: {len(animal_candidates)}")
    print(f"Available Clinical candidates: {len(clinical_candidates)}")
    
    selected = []
    selected.extend(cell_candidates[:3])
    selected.extend(animal_candidates[:3])
    selected.extend(clinical_candidates[:4])
    
    print("\nBalanced Batch of 10 Candidates (Unclassified):")
    selected_ids = []
    for r in selected:
        st = r['study_type']
        print(f"ID: {r['id']} | Study Type: {st} | Title: {r['title'][:70]}... | Link: {r['full_text_link']}")
        selected_ids.append(r['id'])
        
    print(f"\nPID List: {selected_ids}")
    sqlite_conn.close()

if __name__ == "__main__":
    main()
