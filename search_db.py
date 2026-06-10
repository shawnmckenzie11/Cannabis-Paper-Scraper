# search_db.py
import argparse
import json
import csv
import sys
from typing import List, Dict, Any
from db_manager import DatabaseManager
from extractor import format_study_duration

# ANSI escape codes for premium terminal styling
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

def print_gorgeous_table(papers: List[Dict[str, Any]]):
    """Outputs a beautiful, premium terminal dashboard for paper listings."""
    if not papers:
        print(f"\n{YELLOW}No papers matched your search criteria.{RESET}\n")
        return
        
    print(f"\n{BOLD}{BLUE}=== Cannabis Paper Intelligence Catalog ({len(papers)} papers) ==={RESET}")
    
    # Define column widths
    col_widths = {
        "duration": 12,
        "title": 55,
        "year": 4,
        "type": 13,
        "method": 12,
        "score": 6,
        "citations": 5,
        "oa": 4
    }
    
    # Print Header
    header = (
        f"{BOLD}{UNDERLINE}"
        f"{'DURATION':<{col_widths['duration']}}  "
        f"{'TITLE':<{col_widths['title']}}  "
        f"{'YEAR':<{col_widths['year']}}  "
        f"{'STUDY TYPE':<{col_widths['type']}}  "
        f"{'EXPOSURE':<{col_widths['method']}}  "
        f"{'SCORE':<{col_widths['score']}}  "
        f"{'CITES':<{col_widths['citations']}}  "
        f"{'OA':<{col_widths['oa']}}"
        f"{RESET}"
    )
    print(header)
    
    # Print Rows
    for p in papers:
        # Format Duration
        duration_str = format_study_duration(p.get("duration_days"))
        
        # Format Title (truncate and pad)
        title = p.get("title") or "Untitled"
        if len(title) > col_widths["title"]:
            title = title[:col_widths["title"] - 3] + "..."
            
        # Format Year
        year = str(p.get("year") or "N/A")
        
        # Format Study Type
        st = p.get("study_type") or "unknown"
        
        # Format Exposure Method
        exp = p.get("exposure_method") or "unknown"
        
        # Format Score (Color-coded: green >= 14, yellow >= 8, red < 8)
        score_val = p.get("methodological_quality_score")
        if score_val is None:
            score_str = "N/A"
        else:
            if score_val >= 14:
                score_str = f"{GREEN}{score_val}/20{RESET}"
            elif score_val >= 8:
                score_str = f"{YELLOW}{score_val}/20{RESET}"
            else:
                score_str = f"{RED}{score_val}/20{RESET}"
                
        # Format Citations
        cites = str(p.get("citation_count", 0))
        
        # Format Open Access [OA] status
        oa_val = p.get("open_access", 0)
        oa = f"{GREEN}Yes{RESET}" if oa_val == 1 else "No"
        
        # Assemble Row
        row = (
            f"{duration_str:<{col_widths['duration']}}  "
            f"{title:<{col_widths['title']}}  "
            f"{year:<{col_widths['year']}}  "
            f"{st:<{col_widths['type']}}  "
            f"{exp:<{col_widths['method']}}  "
            f"{score_str:<{col_widths['score'] + 9 if score_val is not None else col_widths['score']}}  " # offset for color codes
            f"{cites:<{col_widths['citations']}}  "
            f"{oa:<{col_widths['oa'] + 9 if oa_val == 1 else col_widths['oa']}}"
        )
        print(row)
        
    print(f"\n{BLUE}========================================================================={RESET}\n")

def export_csv(papers: List[Dict[str, Any]], filepath: str):
    """Exports paper records to CSV format."""
    if not papers:
        return
        
    # Get all fields from first paper
    fields = list(papers[0].keys())
    
    try:
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for p in papers:
                p_copy = p.copy()
                # Serialize list values to JSON string for CSV compatibility
                for key in ["authors", "outcome_domain", "methodological_quality_flags"]:
                    if isinstance(p_copy.get(key), list):
                        p_copy[key] = json.dumps(p_copy[key])
                writer.writerow(p_copy)
        print(f"{GREEN}Successfully exported {len(papers)} records to CSV: '{filepath}'{RESET}")
    except Exception as e:
        print(f"{RED}Failed to export CSV: {e}{RESET}", file=sys.stderr)

def export_json(papers: List[Dict[str, Any]], filepath: str):
    """Exports paper records to JSON format."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)
        print(f"{GREEN}Successfully exported {len(papers)} records to JSON: '{filepath}'{RESET}")
    except Exception as e:
        print(f"{RED}Failed to export JSON: {e}{RESET}", file=sys.stderr)

def export_markdown_table(papers: List[Dict[str, Any]], filepath: str):
    """Exports paper records to a gorgeous Markdown Table file."""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Cannabis Research Search Export\n\n")
            f.write(f"Generated search results containing {len(papers)} matching records.\n\n")
            
            # Header
            f.write("| Duration | Title | Year | Study Type | Exposure | Quality Score | Citations | Open Access |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            
            # Rows
            for p in papers:
                title = p.get("title", "").replace("|", "\\|")
                oa_str = "Yes" if p.get("open_access", 0) == 1 else "No"
                duration_str = format_study_duration(p.get("duration_days"))
                f.write(
                    f"| {duration_str} "
                    f"| {title} "
                    f"| {p.get('year') or 'N/A'} "
                    f"| {p.get('study_type') or 'unknown'} "
                    f"| {p.get('exposure_method') or 'unknown'} "
                    f"| {p.get('methodological_quality_score')}/20 "
                    f"| {p.get('citation_count', 0)} "
                    f"| {oa_str} |\n"
                )
        print(f"{GREEN}Successfully exported {len(papers)} records to Markdown Table: '{filepath}'{RESET}")
    except Exception as e:
        print(f"{RED}Failed to export Markdown Table: {e}{RESET}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Query and filter the local Cannabis Research Catalog.")
    
    # Query parameters
    parser.add_argument("--query", type=str, help="Free-text search against paper titles and abstracts")
    parser.add_argument("--year-min", type=int, help="Minimum publication year")
    parser.add_argument("--year-max", type=int, help="Maximum publication year")
    parser.add_argument("--study-type", type=str, choices=["RCT", "observational", "animal", "in vitro", "review", "meta-analysis"],
                        help="Filter by methodological design type")
    parser.add_argument("--method", type=str, help="Filter by exposure method (smoked, vaporized, oral/edible, etc.)")
    parser.add_argument("--thc-min", type=float, help="Minimum THC percentage")
    parser.add_argument("--thc-max", type=float, help="Maximum THC percentage")
    parser.add_argument("--outcome", type=str, help="Multi-label outcome filter (comma-separated, e.g. 'pain,anxiety')")
    parser.add_argument("--open-access", type=str, choices=["true", "false", "yes", "no", "1", "0"], help="Filter by open-access status")
    parser.add_argument("--citations-min", type=int, help="Minimum citation count")
    parser.add_argument("--quality-min", type=int, help="Minimum methodological quality score")
    
    # Presentation and exports
    parser.add_argument("--sort-by", type=str, choices=["year", "citations", "quality_score"], help="Sort outputs")
    parser.add_argument("--export", type=str, choices=["csv", "json", "markdown_table"], help="File format to export")
    parser.add_argument("--output-file", type=str, help="Export output filepath (required if --export is set)")
    
    args = parser.parse_args()
    
    # Verify file output path if export is enabled
    if args.export and not args.output_file:
        parser.error(f"{RED}Error: --output-file is required when --export is specified.{RESET}")
        
    db = DatabaseManager()
    
    # Clean and parse open-access boolean
    oa_filter = None
    if args.open_access:
        oa_filter = args.open_access.lower() in ("true", "yes", "1")
        
    # Build filter payload
    filters = {
        "query": args.query,
        "year_min": args.year_min,
        "year_max": args.year_max,
        "study_type": args.study_type,
        "exposure_method": args.method,
        "thc_min": args.thc_min,
        "thc_max": args.thc_max,
        "outcome": args.outcome,
        "open_access": oa_filter,
        "citations_min": args.citations_min,
        "quality_min": args.quality_min,
        "sort_by": args.sort_by
    }
    
    # Query database
    try:
        papers = db.search_papers(filters)
    except Exception as e:
        print(f"{RED}Search failed due to database query error: {e}{RESET}", file=sys.stderr)
        sys.exit(1)
        
    # Handle Exports or beautiful Terminal display
    if args.export == "csv":
        export_csv(papers, args.output_file)
    elif args.export == "json":
        export_json(papers, args.output_file)
    elif args.export == "markdown_table":
        export_markdown_table(papers, args.output_file)
    else:
        # Gorgeous table listing printout
        print_gorgeous_table(papers)

if __name__ == "__main__":
    main()
