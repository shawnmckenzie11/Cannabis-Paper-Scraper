# harvest.py
import os
import argparse
import logging
import requests
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, Any, List, Set, Optional
from Bio import Entrez
from dotenv import load_dotenv

# Import database manager, extractor, and classifier
from db_manager import DatabaseManager
import classifier
from extractor import is_cannabis_related
from pubmed_metadata import build_publication_type_prefix

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="\033[94m%(asctime)s\033[0m - \033[92m%(levelname)s\033[0m - %(message)s"
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Setup Entrez
ENTREZ_EMAIL = os.getenv("ENTREZ_EMAIL", "miladn1@mcmaster.ca")
Entrez.email = ENTREZ_EMAIL
Entrez.tool = "CannabisResearchScraper"

def get_pubmed_count(query: str) -> int:
    """Checks the total count of papers matching a search query on PubMed without fetching the IDs."""
    try:
        handle = Entrez.esearch(db="pubmed", term=query, retmax=1)
        record = Entrez.read(handle)
        handle.close()
        return int(record.get("Count", 0))
    except Exception as e:
        logger.error(f"Failed to check PubMed count: {e}")
        return 0

def search_pubmed(query: str, max_results: int) -> List[str]:
    """Queries PubMed directly and returns a list of PMIDs matching the search query (up to 9999)."""
    logger.info(f"Searching PubMed directly for query: '{query}' (limit: {max_results})")
    try:
        limit = min(max_results, 9999)
        handle = Entrez.esearch(db="pubmed", term=query, retmax=limit)
        record = Entrez.read(handle)
        handle.close()
        pmids = record.get("IdList", [])
        logger.info(f"PubMed search returned {len(pmids)} matching PMIDs.")
        return pmids
    except Exception as e:
        logger.error(f"PubMed search failed: {e}")
        return []

def fetch_pubmed_papers_via_history(
    query: str, 
    max_results: int, 
    existing_pmids: Set[str], 
    progress_callback=None
) -> tuple[List[Dict[str, Any]], int]:
    """Queries PubMed and fetches full paper details directly via history batching, bypassing all 9999 limits.
    
    Returns:
        tuple: (list of parsed papers, count of skipped pre-existing papers)
    """
    logger.info(f"Searching PubMed using history server for query: '{query}' (max-results: {max_results})")
    try:
        handle = Entrez.esearch(db="pubmed", term=query, usehistory="y")
        record = Entrez.read(handle)
        handle.close()
        
        count = int(record.get("Count", 0))
        webenv = record.get("WebEnv")
        query_key = record.get("QueryKey")
        
        if not webenv or not query_key:
            logger.warning("Entrez did not return WebEnv or QueryKey. Falling back to direct search.")
            return [], 0
            
        total_to_fetch = min(count, max_results)
        msg = f"PubMed search matched {count} total papers. Fetching details for up to {total_to_fetch} papers in batches..."
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)
            
        papers = []
        batch_size = 500  # Safe batch size for fetching XML
        skipped_existing = 0
        
        for retstart in range(0, total_to_fetch, batch_size):
            retmax = min(batch_size, total_to_fetch - retstart)
            batch_msg = f"Fetching PubMed records batch start={retstart}, limit={retmax}..."
            logger.info(batch_msg)
            if progress_callback:
                progress_callback(batch_msg)
                
            try:
                handle = Entrez.efetch(
                    db="pubmed",
                    WebEnv=webenv,
                    query_key=query_key,
                    retstart=retstart,
                    retmax=retmax,
                    retmode="xml"
                )
                xml_data = handle.read()
                handle.close()
                
                if isinstance(xml_data, bytes):
                    xml_data = xml_data.decode("utf-8")
                    
                batch_papers = parse_pubmed_xml(xml_data)
                
                # Filter out existing papers
                for p in batch_papers:
                    pmid = p.get("pmid")
                    if pmid in existing_pmids:
                        skipped_existing += 1
                    else:
                        papers.append(p)
            except Exception as batch_err:
                logger.error(f"Failed to fetch batch starting at {retstart}: {batch_err}")
                
        if skipped_existing > 0:
            logger.info(f"History fetch complete. Skipped {skipped_existing} already-cataloged papers. Found {len(papers)} new papers.")
        return papers, skipped_existing
        
    except Exception as e:
        logger.error(f"PubMed history-based fetch failed: {e}")
        return [], 0



def parse_pubmed_xml(xml_data: str) -> List[Dict[str, Any]]:
    """Parses Medline XML fetched from PubMed and extracts structured details."""
    papers = []
    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        logger.error(f"Failed to parse PubMed XML data: {e}")
        return papers
        
    for article in root.findall(".//PubmedArticle"):
        paper = {}
        
        # 1. PMID
        pmid_node = article.find(".//MedlineCitation/PMID")
        if pmid_node is not None:
            paper["pmid"] = pmid_node.text
        else:
            continue
            
        # 2. Title
        title_node = article.find(".//ArticleTitle")
        if title_node is not None:
            # Handle mixed content in XML title
            title_text = "".join(title_node.itertext()).strip()
            # Remove trailing brackets if any
            if title_text.endswith("."):
                title_text = title_text[:-1]
            paper["title"] = title_text
        else:
            paper["title"] = "Untitled Article"
            
        # 3. Abstract
        abstract_paragraphs = []
        for abstract_text in article.findall(".//AbstractText"):
            label = abstract_text.get("Label")
            text = "".join(abstract_text.itertext()).strip()
            if label and text:
                abstract_paragraphs.append(f"{label}: {text}")
            elif text:
                abstract_paragraphs.append(text)
        paper["abstract"] = "\n\n".join(abstract_paragraphs)
        
        # Prepend publication type to the abstract if it is a Review or Meta-Analysis
        pub_types = []
        for pub_type in article.findall(".//PublicationTypeList/PublicationType"):
            if pub_type.text:
                pub_types.append(pub_type.text.strip())

        prefix = build_publication_type_prefix(pub_types)
        if prefix:
            paper["abstract"] = prefix + paper["abstract"]
        
        # 4. Journal
        journal_node = article.find(".//Journal/Title")
        if journal_node is not None:
            paper["journal"] = journal_node.text
        else:
            journal_node = article.find(".//Journal/ISOAbbreviation")
            paper["journal"] = journal_node.text if journal_node is not None else "Unknown Journal"
            
        # 5. Year & Full Publication Date
        year_node = article.find(".//JournalIssue/PubDate/Year")
        pub_year = None
        pub_month = "01"
        pub_day = "01"
        
        if year_node is not None:
            pub_year = year_node.text.strip()
            try:
                paper["year"] = int(pub_year)
            except ValueError:
                paper["year"] = None
        else:
            # Fallback to MedlineDate
            medline_date = article.find(".//JournalIssue/PubDate/MedlineDate")
            if medline_date is not None:
                # Try to extract a 4 digit year
                match = re.search(r'\b(19|20)\d{2}\b', medline_date.text)
                if match:
                    pub_year = match.group(0)
                    paper["year"] = int(pub_year)
            if "year" not in paper:
                paper["year"] = None
                
        # Parse Month
        month_node = article.find(".//JournalIssue/PubDate/Month")
        if month_node is not None:
            month_text = month_node.text.strip()
            month_map = {
                "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
                "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"
            }
            mapped_month = month_map.get(month_text[:3].lower())
            if mapped_month:
                pub_month = mapped_month
            elif month_text.isdigit():
                pub_month = f"{int(month_text):02d}"
                
        # Parse Day
        day_node = article.find(".//JournalIssue/PubDate/Day")
        if day_node is not None:
            day_text = day_node.text.strip()
            if day_text.isdigit():
                pub_day = f"{int(day_text):02d}"
                
        if pub_year:
            paper["publication_date"] = f"{pub_year}-{pub_month}-{pub_day}"
        else:
            paper["publication_date"] = None
                
        # 6. Authors
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            last_name = author.find("LastName")
            fore_name = author.find("ForeName")
            if last_name is not None and fore_name is not None:
                authors.append(f"{fore_name.text} {last_name.text}")
            elif last_name is not None:
                authors.append(last_name.text)
        paper["authors"] = authors
        
        # 7. DOI
        doi = None
        for el in article.findall(".//ArticleIdList/ArticleId"):
            if el.get("IdType") == "doi":
                doi = el.text
                break
        paper["doi"] = doi
        
        # 8. Full Text Link (Default to PubMed URL)
        paper["full_text_link"] = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
        
        papers.append(paper)
        
    return papers

def fetch_pubmed_details(pmids: List[str]) -> List[Dict[str, Any]]:
    """Fetches full paper metadata in batches from PubMed API."""
    if not pmids:
        return []
        
    logger.info(f"Fetching XML details from PubMed for {len(pmids)} PMIDs...")
    papers = []
    
    # Process in batches of 100 to avoid API limits
    batch_size = 100
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        try:
            handle = Entrez.efetch(db="pubmed", id=",".join(batch), retmode="xml")
            xml_data = handle.read()
            handle.close()
            
            # If the response is bytes, decode it
            if isinstance(xml_data, bytes):
                xml_data = xml_data.decode("utf-8")
                
            batch_papers = parse_pubmed_xml(xml_data)
            papers.extend(batch_papers)
            logger.info(f"Successfully fetched and parsed {len(batch_papers)} papers in this batch.")
        except Exception as e:
            logger.error(f"Error fetching batch starting at {i}: {e}")
            
    return papers

def search_semantic_scholar(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Searches Semantic Scholar for cannabis research papers using the public API."""
    logger.info(f"Searching Semantic Scholar for query: '{query}' (limit: {max_results})")
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": min(max_results, 100), # S2 restricts limit to 100 per page
        "fields": "title,authors,venue,year,abstract,externalIds,citationCount,isOpenAccess,openAccessPdf,publicationDate"
    }
    
    papers = []
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            results = data.get("data", [])
            logger.info(f"Semantic Scholar returned {len(results)} matching papers.")
            
            for item in results:
                paper = {}
                ext_ids = item.get("externalIds", {})
                
                paper["title"] = item.get("title")
                paper["abstract"] = item.get("abstract") or ""
                paper["journal"] = item.get("venue") or "Semantic Scholar"
                paper["year"] = item.get("year")
                paper["citation_count"] = item.get("citationCount", 0)
                paper["semantic_scholar_id"] = item.get("paperId")
                
                # Normalize publicationDate
                pub_date = item.get("publicationDate")
                if pub_date:
                    parts = pub_date.split("-")
                    if len(parts) == 1:
                        pub_date = f"{parts[0]}-01-01"
                    elif len(parts) == 2:
                        pub_date = f"{parts[0]}-{parts[1]}-01"
                elif paper.get("year"):
                    pub_date = f"{paper['year']}-01-01"
                paper["publication_date"] = pub_date
                
                # External IDs
                paper["pmid"] = ext_ids.get("PubMed")
                paper["doi"] = ext_ids.get("DOI")
                
                # Authors
                authors = [a.get("name") for a in item.get("authors", []) if a.get("name")]
                paper["authors"] = authors
                
                # Open Access
                oa_info = item.get("isOpenAccess", False)
                paper["open_access"] = 1 if oa_info else 0
                
                # Full Text URL
                pdf_info = item.get("openAccessPdf")
                if pdf_info and pdf_info.get("url"):
                    paper["full_text_link"] = pdf_info.get("url")
                elif paper.get("doi"):
                    paper["full_text_link"] = f"https://doi.org/{paper['doi']}"
                elif paper.get("pmid"):
                    paper["full_text_link"] = f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"
                else:
                    paper["full_text_link"] = f"https://www.semanticscholar.org/paper/{paper['semantic_scholar_id']}"
                    
                papers.append(paper)
        else:
            logger.warning(f"Semantic Scholar API returned status code {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Semantic Scholar search failed: {e}")
        
    return papers

def enrich_papers_batch_semantic_scholar(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enriches a batch of papers with citations and open-access PDF links from Semantic Scholar using POST batch lookup."""
    if not papers:
        return papers
        
    url = "https://api.semanticscholar.org/graph/v1/paper/batch?fields=paperId,citationCount,isOpenAccess,openAccessPdf,abstract"
    
    # Prepare identifiers
    ids = []
    paper_map = {}  # Maps identifier -> paper index in list
    
    for idx, paper in enumerate(papers):
        identifier = None
        if paper.get("doi"):
            identifier = f"DOI:{paper['doi']}"
        elif paper.get("pmid"):
            identifier = f"PMID:{paper['pmid']}"
            
        if identifier:
            ids.append(identifier)
            paper_map[identifier] = idx
        else:
            ids.append(None)
            
    # Process in chunks of 100 to avoid rate limits
    import time
    batch_size = 100
    payload_ids = [id_val for id_val in ids if id_val is not None]
    if not payload_ids:
        return papers
        
    logger.info(f"Sending batch queries to Semantic Scholar for {len(payload_ids)} papers...")
    
    for i in range(0, len(payload_ids), batch_size):
        chunk_ids = payload_ids[i:i + batch_size]
        try:
            response = requests.post(url, json={"ids": chunk_ids}, timeout=15)
            if response.status_code == 429:
                logger.warning("Semantic Scholar rate limit reached. Sleeping 5s before retry...")
                time.sleep(5)
                response = requests.post(url, json={"ids": chunk_ids}, timeout=15)
                
            if response.status_code == 200:
                results = response.json()
                for id_val, result in zip(chunk_ids, results):
                    if result and id_val in paper_map:
                        paper_idx = paper_map[id_val]
                        p = papers[paper_idx]
                        p["semantic_scholar_id"] = result.get("paperId")
                        p["citation_count"] = result.get("citationCount", 0)
                        
                        # Update abstract if missing or placeholder in records
                        s2_abstract = result.get("abstract")
                        if s2_abstract and (not p.get("abstract") or p.get("abstract").strip() == "" or "no abstract" in p.get("abstract").lower()):
                            p["abstract"] = s2_abstract
                        
                        # Update open access status and link
                        if result.get("isOpenAccess"):
                            p["open_access"] = 1
                            pdf_info = result.get("openAccessPdf")
                            if pdf_info and pdf_info.get("url"):
                                p["full_text_link"] = pdf_info.get("url")
            else:
                logger.warning(f"Semantic Scholar batch lookup returned status code {response.status_code}")
                
            # Rate limit politeness delay
            time.sleep(2)
        except Exception as e:
            logger.error(f"Semantic Scholar batch lookup error in chunk starting at {i}: {e}")
            
    return papers

def merge_papers(pubmed_list: List[Dict[str, Any]], s2_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merges papers harvested from both sources and deduplicates by DOI/PMID."""
    merged_map = {}
    
    # Process PubMed first as they contain deep abstract paragraphs
    for paper in pubmed_list:
        pmid = paper.get("pmid")
        doi = paper.get("doi")
        
        key = pmid if pmid else (f"doi:{doi}" if doi else paper.get("title").lower())
        merged_map[key] = paper
        
    # Process Semantic Scholar and merge fields
    for paper in s2_list:
        pmid = paper.get("pmid")
        doi = paper.get("doi")
        
        # Check matching key
        matched_key = None
        if pmid and pmid in merged_map:
            matched_key = pmid
        elif doi and f"doi:{doi}" in merged_map:
            matched_key = f"doi:{doi}"
        else:
            # Fallback title match
            title_lower = paper.get("title").lower()
            for key, val in merged_map.items():
                if val.get("title").lower() == title_lower:
                    matched_key = key
                    break
                    
        if matched_key:
            # Merge
            existing = merged_map[matched_key]
            # Keep PubMed abstract if available, else Semantic Scholar abstract
            if not existing.get("abstract") and paper.get("abstract"):
                existing["abstract"] = paper["abstract"]
            # Fill other missing fields
            if not existing.get("doi") and paper.get("doi"):
                existing["doi"] = paper["doi"]
            if not existing.get("semantic_scholar_id") and paper.get("semantic_scholar_id"):
                existing["semantic_scholar_id"] = paper["semantic_scholar_id"]
            if paper.get("citation_count", 0) > existing.get("citation_count", 0):
                existing["citation_count"] = paper["citation_count"]
            if paper.get("open_access") == 1:
                existing["open_access"] = 1
                existing["full_text_link"] = paper["full_text_link"]
        else:
            # New unique Semantic Scholar paper
            key = pmid if pmid else (f"doi:{doi}" if doi else paper.get("semantic_scholar_id"))
            merged_map[key] = paper
            
    return list(merged_map.values())

def run_harvest_pipeline(
    query: str, 
    max_results: int, 
    update: bool, 
    classify: bool, 
    progress_callback=None
) -> tuple[int, int, int]:
    """Runs the full harvesting pipeline (PubMed + Semantic Scholar),
    performs acronym relevance pre-filtering, Maude classification (or optional LLM pass),
    and stores records in the SQLite database.

    Args:
        query: Search query
        max_results: Max papers to harvest
        update: Skip existing cataloged PMIDs
        classify: True to run Claude LLM pass instead of Maude; False uses Maude (default)
        progress_callback: Optional callable for live progress text updates
        
    Returns:
        tuple: (success_count, skipped_count_pubmed, filter_skipped)
    """
    db = DatabaseManager()
    harvest_batch_id = f"harvest_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # 1. Skip Check setup
    existing_pmids = set()
    if update:
        if progress_callback:
            progress_callback("Checking database for already-cataloged papers...")
        existing_pmids = db.get_all_pmids()
        logger.info(f"Loaded {len(existing_pmids)} existing PMIDs from database.")
        
    # 2. Search PubMed using history fetch
    if progress_callback:
        progress_callback(f"Searching PubMed for query '{query}'...")
    pubmed_papers, skipped_count_pubmed = fetch_pubmed_papers_via_history(
        query, 
        max_results, 
        existing_pmids if update else set(),
        progress_callback=progress_callback
    )
    
    if not pubmed_papers and skipped_count_pubmed == 0:
        logger.warning("History-based fetching empty or failed. Falling back to direct PubMed query...")
        if progress_callback:
            progress_callback("History fetch empty. Falling back to direct PubMed search...")
        pmids = search_pubmed(query, max_results)
        new_pmids = [pmid for pmid in pmids if pmid not in existing_pmids] if update else pmids
        skipped_count_pubmed = len(pmids) - len(new_pmids)
        pubmed_papers = fetch_pubmed_details(new_pmids)
        
    # Enrich PubMed papers with citation counts via batch API
    if progress_callback:
        progress_callback(f"Enriching {len(pubmed_papers)} PubMed papers with Semantic Scholar metrics...")
    logger.info("Enriching PubMed papers with citation counts and direct PDF links via batch API...")
    enriched_pubmed_papers = enrich_papers_batch_semantic_scholar(pubmed_papers)
        
    # 3. Search Semantic Scholar directly for query to broaden corpus
    if progress_callback:
        progress_callback("Searching Semantic Scholar to expand corpus...")
    s2_papers = search_semantic_scholar(query, max_results)
    
    # Filter Semantic Scholar papers by PMID check
    new_s2_papers = []
    for paper in s2_papers:
        pmid = paper.get("pmid")
        if update and pmid and pmid in existing_pmids:
            continue
        new_s2_papers.append(paper)
        
    # 4. Merge results
    if progress_callback:
        progress_callback(f"Deduplicating and merging PubMed ({len(enriched_pubmed_papers)}) & Semantic Scholar ({len(new_s2_papers)})...")
    merged_papers = merge_papers(enriched_pubmed_papers, new_s2_papers)
    logger.info(f"Consolidated. Unique papers: {len(merged_papers)}.")
    
    # Apply biology-driven Intelligent Harvest pre-filtering
    if progress_callback:
        progress_callback("Intelligent Harvest: Scanning and filtering unrelated acronym-collision papers...")
    relevant_papers = []
    filter_skipped = 0
    for p in merged_papers:
        title = p.get("title") or "Untitled"
        abstract = p.get("abstract") or ""
        is_relevant, reason = is_cannabis_related(title, abstract)
        if is_relevant:
            relevant_papers.append(p)
        else:
            logger.info(f"  -> Skipping unrelated acronym-collision paper: '{title[:50]}...' ({reason})")
            filter_skipped += 1
            
    if filter_skipped > 0:
        logger.info(f"Intelligent Harvest: Removed {filter_skipped} unrelated papers. Proceeding with {len(relevant_papers)} papers.")
        if progress_callback:
            progress_callback(f"Intelligent Harvest: Removed {filter_skipped} unrelated papers. Ingesting {len(relevant_papers)} papers...")
    merged_papers = relevant_papers

    # 5. Process metadata extraction & classification
    success_count = 0
    date_str = datetime.now().isoformat()
    total_to_process = len(merged_papers)
    
    for idx, paper in enumerate(merged_papers):
        title = paper.get("title")
        abstract = paper.get("abstract") or ""
        logger.info(f"[{idx+1}/{total_to_process}] Extracting fields for: '{title[:60]}...'")
        if progress_callback:
            progress_callback(f"Ingesting ({idx+1}/{total_to_process}): '{title[:45]}...' [Maude Classification]")
        
        try:
            # Classify via Maude (default) or Claude when --classify / AUTO_HARVEST_CLASSIFY is set
            extracted = classifier.process_paper_metadata(
                title=title,
                abstract=abstract,
                run_llm=classify,
                full_text_link=paper.get("full_text_link"),
                pmid=paper.get("pmid"),
                doi=paper.get("doi"),
            )
            
            # Merge extracted data back into our main paper record
            paper.update(extracted)
            paper["date_harvested"] = date_str
            paper["_harvest_batch_id"] = harvest_batch_id
            
            # Write to Database
            row_id = db.insert_paper(paper)
            success_count += 1
            logger.info(f"  -> Saved paper successfully (DB ID: {row_id})")
            
        except Exception as e:
            logger.error(f"  -> Failed to process paper '{title[:40]}...': {e}")
            
    return success_count, skipped_count_pubmed, filter_skipped


def main():
    parser = argparse.ArgumentParser(description="Harvest and catalog research papers from PubMed & Semantic Scholar.")
    parser.add_argument("--query", type=str, required=True, help="Search string (e.g. 'cannabis vaporized RCT')")
    parser.add_argument("--max-results", type=int, default=500, help="Maximum number of papers to harvest")
    parser.add_argument("--update", action="store_true", help="Skip papers already cataloged in the database")
    parser.add_argument("--classify", action="store_true", help="Perform LLM classification pass using Anthropic API")
    
    args = parser.parse_args()
    
    success_count, skipped_count, filter_skipped = run_harvest_pipeline(
        query=args.query,
        max_results=args.max_results,
        update=args.update,
        classify=args.classify
    )
    
    logger.info(f"\nHarvesting complete! Processed and saved {success_count} papers. "
                f"(Skipped {skipped_count} pre-existing, Intelligent Harvest filtered {filter_skipped} unrelated).")

if __name__ == "__main__":
    main()
