# anthropic_batch_helper.py
import sqlite3
import json
import logging
import os
import sys
import argparse
from datetime import datetime
from typing import Dict, Any, List, Optional

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db_manager import DatabaseManager
import classifier
import extractor
from reclassify_with_llm import download_and_extract_pdf_text

# Set up logging to stderr (to not corrupt stdout if used in piping)
logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

def create_batch_requests(limit: Optional[int] = None, output_file: str = "requests.jsonl", paper_ids: Optional[List[int]] = None):
    """Queries candidate papers, downloads/extracts PDF text, and creates a JSONL request file
    for Anthropic's Message Batches API.
    """
    logger.info("Initializing database query for batch candidates...")
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Load static rules system prompt
    config = classifier.load_rules_config()
    static_prompt = config.get("system_prompt")
    rules_version = config.get("version", "1.0.0")

    try:
        if paper_ids:
            logger.info(f"Targeting specific paper IDs: {paper_ids}")
            placeholders = ",".join(["?"] * len(paper_ids))
            query = f"""
                SELECT id, title, abstract, study_type, exposure_method, full_text_link
                FROM papers
                WHERE id IN ({placeholders})
                ORDER BY id ASC
            """
            cursor.execute(query, tuple(paper_ids))
        else:
            query = """
                SELECT id, title, abstract, study_type, exposure_method, full_text_link
                FROM papers
                WHERE full_text_link IS NOT NULL AND full_text_link != ''
                  AND full_text_link NOT LIKE '%pubmed.ncbi.nlm.nih.gov%'
                  AND (
                    full_text_link LIKE '%.pdf%' 
                    OR full_text_link LIKE '%/pdf/%' 
                    OR full_text_link LIKE '%springer.com%' 
                    OR full_text_link LIKE '%nature.com%' 
                    OR full_text_link LIKE '%counter/pdf%'
                  )
                  AND (classifier_version IS NULL OR (classifier_version NOT LIKE 'llm-reclassify%' AND classifier_version NOT LIKE 'llm-pdf-reclassify%'))
                ORDER BY 
                  -- Prioritize preclinical
                  (CASE WHEN study_type LIKE '%Animal%' OR study_type LIKE '%Cell%' OR exposure_method LIKE '%media%' THEN 0 ELSE 1 END) ASC,
                  id ASC
            """
            # If limit is specified, fetch more candidates than limit to allow for download failures,
            # but stop generating requests once we hit the limit of successfully written requests.
            db_limit = limit * 3 if limit is not None else None
            if db_limit is not None:
                query += " LIMIT ?"
                cursor.execute(query, (db_limit,))
            else:
                cursor.execute(query)
            
        papers = [dict(row) for row in cursor.fetchall()]
        
        if not papers:
            logger.info("No candidate papers found for batch creation.")
            return

        logger.info(f"Loaded {len(papers)} candidate papers. Starting PDF downloading and JSONL generation...")
        
        request_count = 0
        with open(output_file, "w", encoding="utf-8") as f:
            for idx, p in enumerate(papers):
                if limit is not None and request_count >= limit:
                    logger.info(f"Reached limit of {limit} successfully generated request items. Stopping.")
                    break

                paper_id = p["id"]
                title = p.get("title") or ""
                abstract = p.get("abstract") or ""
                pdf_url = p.get("full_text_link") or ""

                logger.info(f"[{idx+1}/{len(papers)}] Processing Paper ID {paper_id}: '{title[:50]}...'")

                # Try to download and parse PDF text
                full_text = download_and_extract_pdf_text(pdf_url)
                if not full_text:
                    logger.warning(f"Skipping paper ID {paper_id} because PDF download or text extraction failed.")
                    continue

                # Truncate text to fit context bounds
                truncated_text = full_text[:100000]
                user_content = f"Title: {title}\n\nAbstract: {abstract}\n\nFull Paper Text (PDF):\n{truncated_text}"

                # Construct Anthropic Message Batch line
                batch_item = {
                    "custom_id": f"paper_{paper_id}",
                    "params": {
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 1000,
                        "temperature": 0.0,
                        "system": [
                            {
                                "type": "text",
                                "text": static_prompt,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ],
                        "messages": [
                            {"role": "user", "content": user_content}
                        ]
                    }
                }
                f.write(json.dumps(batch_item) + "\n")
                request_count += 1

        logger.info(f"Successfully generated {request_count} request batch items in: {output_file}")

    except Exception as e:
        logger.error(f"Error during batch requests creation: {e}")
    finally:
        conn.close()

def process_batch_results(results_file: str, dry_run: bool = False):
    """Processes Anthropic's Message Batches API result JSONL file and updates the database,
    respecting expert locked fields.
    """
    if not os.path.exists(results_file):
        logger.error(f"Results file does not exist: {results_file}")
        return

    logger.info(f"Processing results from {results_file} (Dry-Run: {dry_run})...")
    
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        rules_version = "1.0.0"
        if os.path.exists("rules_config.json"):
            try:
                with open("rules_config.json", "r") as f:
                    rules_version = json.load(f).get("version", "1.0.0")
            except Exception:
                pass

        valid_columns = {
            "study_type", "exposure_method", "cannabis_type", "publication_type",
            "outcome_domain", "thc_pct", "cbd_pct", "dose_mg",
            "strain_reported", "strain_normalized", "duration_days",
            "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
            "sample_size", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
            "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM"
        }

        success_count = 0
        failure_count = 0
        batch_id = f"batch_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        with open(results_file, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except Exception as e:
                    logger.warning(f"Skipping line {line_idx+1}: Invalid JSON. Error: {e}")
                    continue

                custom_id = data.get("custom_id") or ""
                if not custom_id.startswith("paper_"):
                    logger.warning(f"Line {line_idx+1}: Skipping unknown custom_id format '{custom_id}'")
                    continue

                paper_id_str = custom_id.split("_")[1]
                try:
                    paper_id = int(paper_id_str)
                except ValueError:
                    logger.warning(f"Line {line_idx+1}: Could not parse paper ID '{paper_id_str}'")
                    continue

                result_envelope = data.get("result") or {}
                result_type = result_envelope.get("type")

                if result_type != "succeeded":
                    error_info = result_envelope.get("error") or {}
                    logger.error(f"Paper ID {paper_id} failed in batch API. Type: {result_type}, Error: {error_info}")
                    failure_count += 1
                    continue

                # Parse successful response
                message_data = result_envelope.get("message") or {}
                content_list = message_data.get("content") or []
                if not content_list:
                    logger.warning(f"Paper ID {paper_id} succeeded but returned empty content.")
                    failure_count += 1
                    continue

                response_text = content_list[0].get("text", "").strip()

                # Clean markdown block wrappers if present
                if response_text.startswith("```"):
                    lines = response_text.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines[-1].startswith("```"):
                        lines = lines[:-1]
                    response_text = "\n".join(lines).strip()

                try:
                    extracted = json.loads(response_text)
                except Exception as e:
                    logger.error(f"Paper ID {paper_id} response content could not be parsed as JSON: {e}")
                    failure_count += 1
                    continue

                # Retrieve current paper record to respect expert locked fields and compute confidence
                cursor.execute(
                    "SELECT title, abstract, expert_locked_fields, study_type, exposure_method, cannabis_type, publication_type FROM papers WHERE id = ?",
                    (paper_id,)
                )
                paper_row = cursor.fetchone()
                if not paper_row:
                    logger.warning(f"Paper ID {paper_id} not found in database. Skipping.")
                    continue
                paper_row = dict(paper_row)

                locked_fields = paper_row.get("expert_locked_fields") or []
                if isinstance(locked_fields, str):
                    try:
                        locked_fields = json.loads(locked_fields)
                    except Exception:
                        locked_fields = []

                # Compute confidence score dynamically (using classifier logic)
                heuristic_metadata = extractor.extract_all_heuristics(paper_row["title"], paper_row["abstract"] or "")
                agreements = []
                check_fields = ["study_type", "exposure_method", "cannabis_type", "publication_type"]
                for field in check_fields:
                    llm_val = extracted.get(field)
                    h_val = heuristic_metadata.get(field)
                    agreements.append(classifier.jaccard_similarity(llm_val, h_val))
                
                model_agreement_score = sum(agreements) / len(agreements) if agreements else 1.0
                
                # Batch API has self-consistency=1 and similarity=1.0 (neutral baseline)
                final_confidence = 0.5 * 1.0 + 0.3 * 1.0 + 0.2 * model_agreement_score
                final_confidence = max(0.0, min(1.0, final_confidence))

                # Prepare updates
                update_data = {}
                for k, v in extracted.items():
                    if k in valid_columns and k not in locked_fields:
                        update_data[k] = v

                if not update_data:
                    logger.info(f"Paper ID {paper_id}: No fields to update (either locked or invalid).")
                    continue

                # Log dry-run details or execute database updates
                if dry_run:
                    logger.info(f"[DRY-RUN] Paper ID {paper_id} updates:")
                    for k, v in update_data.items():
                        logger.info(f"  {k} -> {v}")
                    logger.info(f"  Confidence -> {final_confidence:.2f}")
                else:
                    set_clauses = []
                    update_params = []
                    for k, v in update_data.items():
                        set_clauses.append(f"{k} = ?")
                        if isinstance(v, list) or isinstance(v, dict):
                            update_params.append(json.dumps(v))
                        else:
                            update_params.append(v)

                    set_clauses.append("classifier_version = ?")
                    update_params.append(f"llm-pdf-reclassify-{rules_version}")

                    set_clauses.append("classification_timestamp = ?")
                    update_params.append(datetime.now().isoformat())

                    set_clauses.append("classification_confidence = ?")
                    update_params.append(final_confidence)

                    update_params.append(paper_id)

                    sql = f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?"
                    cursor.execute(sql, update_params)

                    # Compute and log metrics
                    actual_model = message_data.get("model", "claude-sonnet-4-6")
                    usage = message_data.get("usage") or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_write = usage.get("cache_creation_input_tokens", 0)

                    # Standard cost calculation (50% Batch API discount applied)
                    full_cost = classifier.calculate_token_cost(actual_model, input_tokens, cache_read, cache_write, output_tokens)
                    batch_cost = full_cost * 0.5

                    metrics = {
                        "model": actual_model,
                        "input_tokens": input_tokens,
                        "cache_read_tokens": cache_read,
                        "cache_write_tokens": cache_write,
                        "output_tokens": output_tokens,
                        "cost": batch_cost,
                        "few_shot_similarity": 1.0,
                        "few_shot_count": 0,
                        "classification_confidence": final_confidence,
                        "classifier_version": f"llm-pdf-reclassify-{rules_version}"
                    }

                    db.log_llm_call(
                        paper_id=paper_id,
                        metrics=metrics,
                        batch_id=batch_id,
                        cursor=cursor
                    )

                success_count += 1

        if not dry_run:
            conn.commit()
            logger.info(f"Database commit successful. Batch ingestion completed.")
        
        logger.info(f"Batch Processing Summary: {success_count} papers successfully processed, {failure_count} failures.")

    except Exception as e:
        if not dry_run:
            conn.rollback()
        logger.error(f"Critical error during batch results ingestion: {e}")
    finally:
        conn.close()

def submit_batch(requests_file: str):
    """Uploads the requests JSONL file to Anthropic and submits the Message Batch."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("Error: ANTHROPIC_API_KEY environment variable is not set.")
        return
        
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    
    logger.info(f"Reading requests from {requests_file}...")
    try:
        requests = []
        with open(requests_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                requests.append(json.loads(line))
        
        logger.info(f"Submitting {len(requests)} requests to Anthropic Message Batch API...")
        batch = client.beta.messages.batches.create(
            requests=requests
        )
        logger.info(f"Batch submitted successfully! Batch ID: {batch.id}")
        logger.info(f"Processing Status: {batch.processing_status}")
        
        # Save batch info to a local json file to track it
        os.makedirs("scratch", exist_ok=True)
        batch_info = {
            "batch_id": batch.id,
            "status": batch.processing_status,
            "created_at": datetime.now().isoformat()
        }
        with open("scratch/last_batch.json", "w") as bf:
            json.dump(batch_info, bf, indent=2)
            
    except Exception as e:
        logger.error(f"Failed to submit batch: {e}")

def check_batch_status():
    """Queries the status of the last submitted batch from scratch/last_batch.json."""
    if not os.path.exists("scratch/last_batch.json"):
        logger.error("No batch info found in scratch/last_batch.json")
        return
        
    with open("scratch/last_batch.json", "r") as f:
        batch_info = json.load(f)
        
    batch_id = batch_info.get("batch_id")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("Error: ANTHROPIC_API_KEY environment variable is not set.")
        return
        
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    
    logger.info(f"Checking status for Batch ID: {batch_id}...")
    try:
        batch = client.beta.messages.batches.retrieve(batch_id)
        logger.info(f"Batch ID: {batch.id}")
        logger.info(f"Processing Status: {batch.processing_status}")
        logger.info(f"Request Counts: Succeeded: {batch.request_counts.succeeded}, Errored: {batch.request_counts.errored}, Processing: {batch.request_counts.processing}")
        
        # Update status in last_batch.json
        batch_info["status"] = batch.processing_status
        if batch.processing_status == "ended":
            logger.info(f"Batch has completed! Output Batch ID: {batch.id}")
            logger.info(f"To download results, run:")
            logger.info(f"  python anthropic_batch_helper.py --download-results --results-file-id {batch.id}")
            
        with open("scratch/last_batch.json", "w") as bf:
            json.dump(batch_info, bf, indent=2)
            
    except Exception as e:
        logger.error(f"Failed to retrieve batch status: {e}")

def download_results(batch_id: str, output_file: str = "scratch/batch_results.jsonl"):
    """Downloads the results JSONL from Anthropic for a completed batch."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("Error: ANTHROPIC_API_KEY environment variable is not set.")
        return
        
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    
    logger.info(f"Downloading results for Batch ID {batch_id} from Anthropic...")
    try:
        results_iterator = client.beta.messages.batches.results(batch_id)
        
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for result in results_iterator:
                f.write(result.model_dump_json() + "\n")
            
        logger.info(f"Successfully downloaded results to {output_file}")
        logger.info(f"To process and ingest these results into the database, run:")
        logger.info(f"  python anthropic_batch_helper.py --process-results --results {output_file}")
    except Exception as e:
        logger.error(f"Failed to download results: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Anthropic Message Batches API pipelines.")
    parser.add_argument("--create-requests", action="store_true", help="Generate a JSONL request file for Anthropic Batch API")
    parser.add_argument("--process-results", action="store_true", help="Ingest a JSONL results file from Anthropic and update DB")
    parser.add_argument("--submit-batch", action="store_true", help="Upload requests.jsonl and submit to Anthropic Batch API")
    parser.add_argument("--check-batch", action="store_true", help="Check status of the last submitted batch")
    parser.add_argument("--download-results", action="store_true", help="Download results from Anthropic for a completed batch")
    parser.add_argument("--results-file-id", type=str, default=None, help="The results file ID to download")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of papers to select for request generation")
    parser.add_argument("--output", type=str, default="requests.jsonl", help="Output JSONL request file path (default: requests.jsonl)")
    parser.add_argument("--results", type=str, default=None, help="Input JSONL results file path for processing")
    parser.add_argument("--dry-run", action="store_true", help="Do not write updates to SQLite database, print output only")
    parser.add_argument("--paper-ids", type=str, default=None, help="Comma-separated list of target paper IDs to generate requests for")
    args = parser.parse_args()

    if args.create_requests:
        paper_ids = None
        if args.paper_ids:
            try:
                paper_ids = [int(pid.strip()) for pid in args.paper_ids.split(",") if pid.strip()]
            except ValueError:
                logger.error("Error: --paper-ids must be a comma-separated list of integers.")
                sys.exit(1)
        create_batch_requests(limit=args.limit, output_file=args.output, paper_ids=paper_ids)
    elif args.submit_batch:
        submit_batch(args.output)
    elif args.check_batch:
        check_batch_status()
    elif args.download_results:
        if not args.results_file_id:
            logger.error("Error: --results-file-id is required when running --download-results")
            sys.exit(1)
        download_results(args.results_file_id)
    elif args.process_results:
        if not args.results:
            logger.error("Error: --results file path is required when running --process-results")
            sys.exit(1)
        process_batch_results(args.results, dry_run=args.dry_run)
    else:
        parser.print_help()
