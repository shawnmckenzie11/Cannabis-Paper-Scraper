# enrich_reviews.py
import sqlite3
import logging
import time
from Bio import Entrez
import xml.etree.ElementTree as ET
from db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

Entrez.email = "miladn1@mcmaster.ca"
Entrez.tool = "CannabisResearchScraper"

def enrich_reviews_in_catalog():
    """Fetches publication types for all cataloged papers from PubMed, 
    updates abstract prefixes for reviews and meta-analyses, and commits changes.
    """
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # Fetch papers that have a PMID and do not already have a "Publication Type:" prefix in their abstract
        cursor.execute(
            """
            SELECT id, pmid, abstract 
            FROM papers 
            WHERE pmid IS NOT NULL AND pmid != ''
              AND (abstract NOT LIKE 'Publication Type: %' OR abstract IS NULL)
            """
        )
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No papers found in the database that need publication type enrichment.")
            return
            
        logger.info(f"Loaded {len(papers)} papers from the catalog for publication type enrichment.")
        
        # Build mapping of PMID -> paper info
        paper_map = {p["pmid"]: p for p in papers}
        pmids = list(paper_map.keys())
        
        batch_size = 100
        update_count = 0
        
        logger.info(f"Splitting into {((len(pmids) - 1) // batch_size) + 1} batches of {batch_size}...")
        
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]
            logger.info(f"Processing batch {(i // batch_size) + 1}: querying {len(batch)} papers from PubMed...")
            
            try:
                handle = Entrez.efetch(db="pubmed", id=",".join(batch), retmode="xml")
                xml_data = handle.read()
                handle.close()
                
                root = ET.fromstring(xml_data)
                for article in root.findall(".//PubmedArticle"):
                    pmid_node = article.find(".//MedlineCitation/PMID")
                    if pmid_node is None or pmid_node.text not in paper_map:
                        continue
                        
                    pmid = pmid_node.text
                    paper = paper_map[pmid]
                    
                    # Extract publication types
                    pub_types = []
                    for pub_type in article.findall(".//PublicationTypeList/PublicationType"):
                        if pub_type.text:
                            pub_types.append(pub_type.text.strip().lower())
                            
                    is_review = any("review" in pt for pt in pub_types)
                    is_meta = any("meta-analysis" in pt for pt in pub_types)
                    
                    prefix = ""
                    if is_meta:
                        prefix += "Publication Type: Meta-Analysis. "
                    if is_review:
                        if "Meta-Analysis" not in prefix:
                            prefix += "Publication Type: Review. "
                            
                    if prefix:
                        old_abstract = paper["abstract"] or ""
                        # Avoid double-prepending if it somehow has it
                        if not old_abstract.startswith("Publication Type:"):
                            new_abstract = prefix + old_abstract
                            cursor.execute(
                                "UPDATE papers SET abstract = ? WHERE id = ?",
                                (new_abstract, paper["id"])
                            )
                            update_count += 1
                
                conn.commit()
                logger.info(f"  -> Successfully updated {update_count} papers so far.")
                
                # Sleep to respect rate limits (3 requests per second limit)
                time.sleep(0.5)
                
            except Exception as batch_err:
                logger.error(f"Error processing batch starting at {i}: {batch_err}")
                time.sleep(2) # Backoff if error
                
        logger.info(f"\nCompleted! Successfully enriched and updated {update_count} papers with Publication Type prefixes!")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to enrich reviews: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    enrich_reviews_in_catalog()
