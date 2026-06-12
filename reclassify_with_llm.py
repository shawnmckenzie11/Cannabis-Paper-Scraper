# reclassify_with_llm.py
import sqlite3
import json
import logging
import os
import sys
import argparse
import io
import requests
import pypdf
from datetime import datetime
from db_manager import DatabaseManager
import classifier

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

def download_and_extract_pdf_text(url: str):
    """Downloads a PDF from the given URL and extracts its text content.
    Returns the extracted text or None if download/extraction fails.
    """
    if not url or not url.startswith("http"):
        return None
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    try:
        logger.info(f"Downloading PDF from {url}...")
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            logger.warning(f"Failed to download PDF. HTTP status code: {response.status_code}")
            return None
            
        content_type = response.headers.get("Content-Type", "").lower()
        is_pdf = content_type.startswith("application/pdf") or response.content.startswith(b"%PDF")
        if not is_pdf:
            logger.warning(f"URL did not return a valid PDF. Content-Type: {content_type}")
            return None
            
        pdf_file = io.BytesIO(response.content)
        reader = pypdf.PdfReader(pdf_file)
        
        text_parts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
                
        full_text = "\n".join(text_parts).strip()
        if not full_text:
            logger.warning("PDF was downloaded but no text could be extracted.")
            return None
            
        logger.info(f"Successfully extracted {len(full_text)} characters of text from PDF ({len(reader.pages)} pages).")
        return full_text
        
    except Exception as e:
        logger.warning(f"Error downloading or parsing PDF: {e}")
        return None

def reclassify_papers_llm(limit=None, offset=0, prioritize=True, paper_id=None):
    """Queries papers from the database and runs the Anthropic Claude LLM classifier on them,
    respecting expert locked fields.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable is not configured. Cannot run LLM reclassification.")
        sys.exit(1)

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

        # Query papers
        query = """
            SELECT id, title, abstract, study_type, exposure_method, cannabis_type, publication_type,
                   outcome_domain, thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
                   duration_days, inhaled_exposure_duration, administration_frequency, treatment_duration,
                   sample_size, puff_count, thc_mg_ml, thc_mg_g, thc_mg_kg, cbd_mg_ml, cbd_mg_g, cbd_mg_kg,
                   thc_uM, cbd_uM, expert_locked_fields, full_text_link
            FROM papers
        """
        params = []
        if paper_id is not None:
            query += " WHERE id = ?"
            params.append(paper_id)
        else:
            if prioritize:
                query += """
                    ORDER BY 
                        -- 1. Papers not yet classified by LLM first
                        (CASE WHEN classifier_version LIKE 'llm-%' THEN 1 ELSE 0 END) ASC,
                        -- 2. Papers with likely heuristic fallback conflicts prioritized first
                        (CASE WHEN 
                            (exposure_method IN ('["inhaled"]', '["injection cannabinoids"]', '["cannabinoids dissolved in media"]', '["unknown"]') OR exposure_method IS NULL) AND
                            (title LIKE '%oil%' OR title LIKE '%tincture%' OR title LIKE '%gummy%' OR title LIKE '%edible%' OR title LIKE '%capsule%' OR title LIKE '%sublingual%' OR title LIKE '%oral%' OR title LIKE '%ingest%' OR title LIKE '%spray%' OR title LIKE '%sativex%' OR title LIKE '%epidiolex%' OR
                             abstract LIKE '%oil%' OR abstract LIKE '%tincture%' OR abstract LIKE '%gummy%' OR abstract LIKE '%edible%' OR abstract LIKE '%capsule%' OR abstract LIKE '%sublingual%' OR abstract LIKE '%oral%' OR abstract LIKE '%ingest%' OR abstract LIKE '%spray%' OR abstract LIKE '%sativex%' OR abstract LIKE '%epidiolex%')
                        THEN 0 ELSE 1 END) ASC,
                        -- 3. Original research articles before reviews/others
                        (CASE WHEN publication_type = 'original research' THEN 0 ELSE 1 END) ASC,
                        -- 4. Low confidence classifications first
                        COALESCE(classification_confidence, 0) ASC,
                        -- 5. Highly cited papers first
                        citation_count DESC,
                        -- 6. Most recently harvested first
                        date_harvested DESC
                """
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])

        cursor.execute(query, params)
        papers = [dict(row) for row in cursor.fetchall()]

        if not papers:
            logger.info("No papers found matching the query criteria.")
            return

        batch_id = f"reclassify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"Loaded {len(papers)} papers from the database. Starting LLM reclassification (Batch ID: {batch_id})...")

        update_count = 0
        
        # Valid database columns to filter LLM output fields
        valid_columns = {
            "study_type", "exposure_method", "cannabis_type", "publication_type",
            "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
            "strain_reported", "strain_normalized", "duration_days",
            "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
            "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
            "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM"
        }

        for idx, p in enumerate(papers):
            paper_id = p["id"]
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""

            logger.info(f"[{idx+1}/{len(papers)}] Processing Paper ID {paper_id}: '{title[:50]}...'")

            # Respect locked fields
            locked_fields = p.get("expert_locked_fields") or []
            if isinstance(locked_fields, str):
                try:
                    locked_fields = json.loads(locked_fields)
                except Exception:
                    locked_fields = []
            if not isinstance(locked_fields, list):
                locked_fields = []

            # Check for PDF full text link
            full_text_link = p.get("full_text_link") or ""
            full_text = None
            if full_text_link:
                full_text = download_and_extract_pdf_text(full_text_link)

            # Call LLM classification
            try:
                extracted = classifier.process_paper_metadata(title, abstract, run_llm=True, full_text=full_text)
            except Exception as e:
                logger.error(f"Error calling classifier for paper ID {paper_id}: {e}")
                continue

            if not extracted:
                logger.warning(f"No metadata extracted for paper ID {paper_id}. Skipping.")
                continue

            # Update fields in paper record except those locked or not present in DB
            update_data = {}
            for k, v in extracted.items():
                if k in valid_columns and k not in locked_fields:
                    update_data[k] = v

            if not update_data:
                logger.info(f"No fields to update for paper ID {paper_id} (either locked or invalid). Skipping.")
                continue

            # Update the paper record
            set_clauses = []
            update_params = []
            for k, v in update_data.items():
                set_clauses.append(f"{k} = ?")
                if isinstance(v, list) or isinstance(v, dict):
                    update_params.append(json.dumps(v))
                else:
                    update_params.append(v)
            
            # Add metadata metadata
            set_clauses.append("classifier_version = ?")
            version_prefix = "llm-pdf-reclassify" if full_text else "llm-reclassify"
            update_params.append(f"{version_prefix}-{rules_version}")
            
            set_clauses.append("classification_timestamp = ?")
            update_params.append(datetime.now().isoformat())

            # Add confidence score manual update
            if "classification_confidence" in extracted:
                set_clauses.append("classification_confidence = ?")
                update_params.append(extracted["classification_confidence"])
            
            update_params.append(paper_id)

            sql = f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?"
            cursor.execute(sql, update_params)
            
            # Log LLM call details
            if "_llm_call_metrics" in extracted:
                db.log_llm_call(
                    paper_id=paper_id,
                    metrics=extracted["_llm_call_metrics"],
                    batch_id=batch_id,
                    cursor=cursor
                )

            update_count += 1

            # Commit periodically
            if update_count % 10 == 0:
                conn.commit()

        conn.commit()
        logger.info(f"LLM Reclassification complete. Updated {update_count} papers.")

    except Exception as e:
        conn.rollback()
        logger.error(f"Critical error during LLM reclassification: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reclassify database papers using Claude LLM.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of papers to reclassify")
    parser.add_argument("--offset", type=int, default=0, help="Offset to start reclassifying from")
    parser.add_argument("--no-prioritize", action="store_true", help="Disable smart prioritization of potentially misclassified/updated papers")
    parser.add_argument("--paper-id", type=int, default=None, help="Specific paper ID to reclassify")
    args = parser.parse_args()

    reclassify_papers_llm(limit=args.limit, offset=args.offset, prioritize=not args.no_prioritize, paper_id=args.paper_id)
