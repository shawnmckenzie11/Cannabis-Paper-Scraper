# purge_unrelated.py
import sqlite3
import json
import re
import sys
from typing import List, Dict, Any
from db_manager import DatabaseManager

# ANSI escape codes for styling
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Import shared cannabis relevance check from extractor
from extractor import is_cannabis_related

def run_purger(dry_run: bool = True):
    """Scans all cataloged papers and purges misfit acronym collisions."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id, title, abstract, pmid, doi FROM papers")
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            print(f"\n{YELLOW}No papers found in the database catalog.{RESET}\n")
            return
            
        misfits = []
        for p in papers:
            title = p["title"]
            abstract = p["abstract"] or ""
            related, reason = is_cannabis_related(title, abstract)
            if not related:
                misfits.append({
                    "id": p["id"],
                    "title": title,
                    "reason": reason,
                    "pmid": p.get("pmid"),
                    "doi": p.get("doi")
                })
                
        if not misfits:
            print(f"\n{GREEN}Database is pristine! 0 unrelated papers detected.{RESET}\n")
            return
            
        print(f"\n{BOLD}{RED}=== Cannabis Catalog Cleanse Scanner ==={RESET}")
        print(f"Scanned {len(papers)} total papers. Detected {len(misfits)} unrelated papers to purge:\n")
        
        # Display first 15 misfits as preview
        preview_count = min(len(misfits), 15)
        for idx, m in enumerate(misfits[:preview_count]):
            print(f"{BOLD}{idx+1}. ID: {m['id']} | {BLUE}{m['title'][:70]}...{RESET}")
            print(f"   -> {YELLOW}Reason:{RESET} {m['reason']}")
            if m['pmid']: print(f"   -> PMID: {m['pmid']}")
            if m['doi']: print(f"   -> DOI: {m['doi']}")
            print()
            
        if len(misfits) > preview_count:
            print(f"... and {len(misfits) - preview_count} more papers.\n")
            
        if dry_run:
            print(f"{BOLD}{YELLOW}Dry run active. No papers were deleted.{RESET}")
            print(f"To execute the actual purge, run: {BOLD}python3 purge_unrelated.py --execute{RESET}\n")
        else:
            # Perform actual deletion
            misfit_ids = [m["id"] for m in misfits]
            
            print(f"Deleting {len(misfits)} papers from database and virtual indexing...")
            # Perform batch deletion inside a single transaction
            chunk_size = 900
            for k in range(0, len(misfit_ids), chunk_size):
                chunk = misfit_ids[k:k + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                cursor.execute(f"DELETE FROM papers WHERE id IN ({placeholders})", chunk)
            conn.commit()
                
            print(f"\n{GREEN}Successfully purged {len(misfits)} unrelated acronym-collision papers!{RESET}\n")
            
    except Exception as e:
        print(f"{RED}Cleanup failed: {e}{RESET}", file=sys.stderr)
    finally:
        conn.close()

if __name__ == "__main__":
    execute_mode = "--execute" in sys.argv
    run_purger(dry_run=not execute_mode)
