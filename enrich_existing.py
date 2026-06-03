# enrich_existing.py
import sqlite3
import requests
import logging
from db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def enrich_existing_catalog():
    """Reads all papers in SQLite, queries Semantic Scholar POST batch API, and updates citation counts."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Fetch all cataloged papers
        cursor.execute("SELECT id, pmid, doi, title, citation_count FROM papers")
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No papers found in the database to enrich.")
            return
            
        logger.info(f"Loaded {len(papers)} papers from the catalog for citation enrichment.")
        
        # 2. Build Semantic Scholar POST batch request payload
        url = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=paperId,citationCount,isOpenAccess,openAccessPdf"
        
        ids = []
        paper_map = {}  # Maps Semantic Scholar ID query string -> SQLite paper ID
        
        for p in papers:
            identifier = None
            if p.get("doi"):
                identifier = f"DOI:{p['doi']}"
            elif p.get("pmid"):
                identifier = f"PMID:{p['pmid']}"
                
            if identifier:
                ids.append(identifier)
                paper_map[identifier] = p["id"]
                
        if not ids:
            logger.info("None of the papers in the database have a PMID or DOI. Cannot query Semantic Scholar.")
            return
            
        # 3. Process in batches of 100 to respect Semantic Scholar limits and rate limits
        import time
        batch_size = 100
        update_count = 0
        
        logger.info(f"Splitting into {((len(ids) - 1) // batch_size) + 1} batches of {batch_size}...")
        
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            
            logger.info(f"Processing batch {(i // batch_size) + 1}: querying {len(batch_ids)} papers...")
            
            try:
                response = requests.post(url, json={"ids": batch_ids}, timeout=15)
                
                if response.status_code == 429:
                    logger.warning("Rate limited (429). Sleeping for 5 seconds...")
                    time.sleep(5)
                    # Retry once
                    response = requests.post(url, json={"ids": batch_ids}, timeout=15)
                    
                if response.status_code != 200:
                    logger.error(f"Semantic Scholar batch API failed with status {response.status_code}")
                    continue
                    
                results = response.json()
                
                # Update SQLite database for this batch
                for identifier, result in zip(batch_ids, results):
                    if result and identifier in paper_map:
                        paper_id = paper_map[identifier]
                        citations = result.get("citationCount", 0)
                        semantic_scholar_id = result.get("paperId")
                        
                        # Fetch open access info if available
                        oa_pdf = None
                        if result.get("isOpenAccess") and result.get("openAccessPdf"):
                            oa_pdf = result["openAccessPdf"].get("url")
                        
                        if oa_pdf:
                            cursor.execute(
                                """
                                UPDATE papers 
                                SET citation_count = ?, semantic_scholar_id = ?, open_access = 1, full_text_link = ?
                                WHERE id = ?
                                """,
                                (citations, semantic_scholar_id, oa_pdf, paper_id)
                            )
                        else:
                            cursor.execute(
                                """
                                UPDATE papers 
                                SET citation_count = ?, semantic_scholar_id = ?
                                WHERE id = ?
                                """,
                                (citations, semantic_scholar_id, paper_id)
                            )
                        update_count += 1
                
                conn.commit()
                logger.info(f"  -> Successfully updated {update_count} papers so far.")
                
                # Sleep between batches to prevent rate limiting
                time.sleep(2)
                
            except Exception as batch_err:
                logger.error(f"Error processing batch starting at {i}: {batch_err}")
                
        logger.info(f"\nCompleted! Successfully enriched and updated {update_count} papers with accurate citation counts!")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to enrich existing papers: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    enrich_existing_catalog()
