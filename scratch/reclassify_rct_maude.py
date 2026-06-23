#!/usr/bin/env python3
"""Updates all Clinical RCT papers in the database using the Maude classification model,
following the PDF -> Full text -> Abstract hierarchy, without LLM calls.
"""

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Set up project path
PROJECT_ROOT = Path("/Users/shawnscomputer/Documents/Cannabis Paper Scraper")
sys.path.append(str(PROJECT_ROOT))

from db_manager import DatabaseManager
import extractor
import paper_text_cache
import calibration_pdf

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def parse_list_field(val):
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
    if isinstance(val, list):
        return val
    return [val]

def main():
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Fetch all Clinical RCT papers
    cursor.execute("""
        SELECT id, pmid, doi, title, abstract, full_text_link, study_type, exposure_method,
               thc_pct, cbd_pct, dose_mg, strain_reported, strain_normalized,
               duration_days, sample_size, outcome_domain, cannabis_type, publication_type,
               summary, thc_mg_kg, cbd_mg_kg, population_age, population_sex,
               inclusion_criteria, exclusion_criteria, expert_locked_fields
        FROM papers
        WHERE study_type LIKE '%Clinical (RCT)%'
    """)
    papers = [dict(row) for row in cursor.fetchall()]
    total_papers = len(papers)
    logger.info(f"Found {total_papers} Clinical RCT papers in the database to reclassify.")

    if total_papers == 0:
        sys.exit(0)

    # Load rules config version
    rules_version = "2.6.0"
    if os.path.exists(PROJECT_ROOT / "rules_config.json"):
        try:
            with open(PROJECT_ROOT / "rules_config.json", "r") as f:
                rules_version = json.load(f).get("version", "2.6.0")
        except Exception:
            pass

    # Fields to check and potentially update
    fields_to_update = [
        "exposure_method", "cannabis_type", "publication_type", "summary",
        "thc_pct", "cbd_pct", "dose_mg", "strain_reported", "strain_normalized",
        "duration_days", "sample_size", "outcome_domain", "thc_mg_kg", "cbd_mg_kg",
        "population_age", "population_sex", "inclusion_criteria", "exclusion_criteria"
    ]

    # Initialize statistics
    stats = {f: {"checked": 0, "changed": 0} for f in fields_to_update}
    papers_changed = 0
    total_fields_checked = 0
    total_fields_changed = 0
    sources_used = {"pdf": 0, "fulltext": 0, "abstract": 0}

    logger.info("Starting reclassification processing...")

    for idx, p in enumerate(papers):
        paper_id = p["id"]
        title = p["title"] or ""
        abstract = p["abstract"] or ""
        pmid = p["pmid"]
        doi = p["doi"]
        full_text_link = p["full_text_link"]

        # Parse locked fields
        locked_fields = p.get("expert_locked_fields") or []
        if isinstance(locked_fields, str):
            try:
                locked_fields = json.loads(locked_fields)
            except Exception:
                locked_fields = []
        if not isinstance(locked_fields, list):
            locked_fields = []

        # 1. Resolve text following hierarchy: PDF -> Full text -> Abstract
        full_text = None
        source = "abstract"

        # Check local cache first (PDF/Full text)
        cached = paper_text_cache.read_cached_entry(paper_id, cache_dir=PROJECT_ROOT / "scratch" / "paper_cache")
        if cached and cached.get("text"):
            full_text = cached["text"]
            source = "pdf" if cached.get("has_pdf") else "fulltext"
        else:
            # Try to resolve/download online
            if full_text_link or pmid or doi:
                try:
                    full_text, source = calibration_pdf.resolve_classification_full_text(
                        full_text_link=full_text_link,
                        pmid=pmid,
                        doi=doi
                    )
                    # Cache it if download succeeded
                    if full_text:
                        paper_text_cache.write_cached_entry(
                            paper_id,
                            text=full_text,
                            source=source,
                            full_text_link=full_text_link,
                            pmid=pmid,
                            doi=doi,
                            cache_dir=PROJECT_ROOT / "scratch" / "paper_cache"
                        )
                except Exception:
                    full_text = None
                    source = "abstract"

        sources_used[source] += 1

        # 2. Run Maude extraction on the resolved text
        maude_result = extractor.extract_all_heuristics(title, abstract, full_text=full_text)

        # 3. Identify differences
        updates = {}
        paper_has_change = False

        for f in fields_to_update:
            # Skip if field is locked by an expert
            if f in locked_fields:
                continue

            new_val = maude_result.get(f)
            old_val = p.get(f)

            # Handle JSON fields
            is_json_field = f in ["exposure_method", "cannabis_type", "outcome_domain"]
            if is_json_field:
                old_list = parse_list_field(old_val)
                new_list = parse_list_field(new_val)
                changed = sorted(old_list) != sorted(new_list)
            else:
                # Handle numeric/text comparison
                if old_val is None and new_val == "":
                    changed = False
                elif old_val == "" and new_val is None:
                    changed = False
                elif isinstance(old_val, float) and isinstance(new_val, (int, float)):
                    changed = abs(old_val - float(new_val)) > 1e-6
                else:
                    changed = str(old_val) != str(new_val) and (old_val is not None or new_val is not None)

            stats[f]["checked"] += 1
            total_fields_checked += 1

            if changed:
                stats[f]["changed"] += 1
                total_fields_changed += 1
                paper_has_change = True
                
                # Format value for database write
                if is_json_field:
                    updates[f] = json.dumps(parse_list_field(new_val))
                else:
                    updates[f] = new_val

        # 4. Save updates to database if there are changes
        if paper_has_change:
            papers_changed += 1
            set_clauses = [f"{f} = ?" for f in updates.keys()]
            set_clauses.append("classifier_version = ?")
            set_clauses.append("classification_timestamp = ?")
            
            sql = f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?"
            params = list(updates.values())
            params.append(f"maude-rct-reclassify-{rules_version}")
            params.append(datetime.now().isoformat())
            params.append(paper_id)
            
            cursor.execute(sql, params)

            if papers_changed % 50 == 0:
                conn.commit()
                logger.info(f"Processed {idx+1}/{total_papers} papers... Committed {papers_changed} updates.")

    conn.commit()
    conn.close()

    # Log summary of results to stdout for capture
    print("\n" + "="*80)
    print("MAUDE BATCH RECLASSIFICATION COMPLETE")
    print("="*80)
    print(f"Total Clinical RCT papers processed: {total_papers}")
    print(f"Total papers updated (at least 1 field): {papers_changed} ({papers_changed/total_papers*100:.2f}%)")
    print(f"Total fields checked across all papers: {total_fields_checked}")
    print(f"Total fields updated across all papers: {total_fields_changed} ({total_fields_changed/total_fields_checked*100:.2f}%)")
    
    print("\nSOURCES USED FOR EXTRACTION:")
    print(f"  - PDF (Cached or Downloaded):    {sources_used['pdf']} ({sources_used['pdf']/total_papers*100:.1f}%)")
    print(f"  - Europe PMC / HTML Full Text:  {sources_used['fulltext']} ({sources_used['fulltext']/total_papers*100:.1f}%)")
    print(f"  - Abstract & Title (Fallback):  {sources_used['abstract']} ({sources_used['abstract']/total_papers*100:.1f}%)")

    print("\nBREAKDOWN OF UPDATES BY FIELD:")
    for f in fields_to_update:
        ch = stats[f]["changed"]
        ck = stats[f]["checked"]
        pct = (ch / ck * 100) if ck > 0 else 0
        print(f"  - {f:<25} {ch:>4} / {ck:>4} updates ({pct:.1f}%)")
    print("="*80)

if __name__ == "__main__":
    main()
