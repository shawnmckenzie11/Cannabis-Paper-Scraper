# summary_report.py
import argparse
import sys
import os
import json
from datetime import datetime
from typing import List, Dict, Any
from db_manager import DatabaseManager

# ANSI color codes
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

def calculate_median(values: List[float]) -> float:
    """Helper to calculate median of a list of floats."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return sorted_vals[mid]
    else:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0

def generate_landscape_report(papers: List[Dict[str, Any]], output_filepath: str, filter_summary: str):
    """Aggregates paper data and writes a gorgeous Markdown landscape report."""
    n_papers = len(papers)
    
    # 1. Study Type x Exposure Method Matrix Setup
    study_types = ["RCT", "observational", "animal", "in vitro", "review", "meta-analysis"]
    exposure_methods = ["smoked", "vaporized", "oral/edible", "tincture", "injection", "forced inhalation", "in vitro", "unknown"]
    
    matrix = {st: {em: 0 for em in exposure_methods} for st in study_types}
    
    for p in papers:
        st = p.get("study_type")
        em = p.get("exposure_method")
        
        # Guard against unmapped values
        if st not in matrix:
            st = "observational"  # fallback
        if em not in matrix[st]:
            em = "unknown"  # fallback
            
        matrix[st][em] += 1
        
    # 2. THC and CBD Stats
    thc_vals = [p["thc_pct"] for p in papers if p.get("thc_pct") is not None]
    cbd_vals = [p["cbd_pct"] for p in papers if p.get("cbd_pct") is not None]
    
    thc_stats = {}
    if thc_vals:
        thc_stats = {
            "count": len(thc_vals),
            "min": min(thc_vals),
            "max": max(thc_vals),
            "avg": sum(thc_vals) / len(thc_vals),
            "median": calculate_median(thc_vals)
        }
        
    cbd_stats = {}
    if cbd_vals:
        cbd_stats = {
            "count": len(cbd_vals),
            "min": min(cbd_vals),
            "max": max(cbd_vals),
            "avg": sum(cbd_vals) / len(cbd_vals),
            "median": calculate_median(cbd_vals)
        }
        
    # 3. Outcome Domain Prevalence
    outcome_counts = {}
    for p in papers:
        outcomes = p.get("outcome_domain") or []
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []
        for o in outcomes:
            outcome_counts[o] = outcome_counts.get(o, 0) + 1
            
    # 4. Methodological Flags Prevalence
    flag_counts = {}
    for p in papers:
        flags = p.get("methodological_quality_flags") or []
        if isinstance(flags, str):
            try:
                flags = json.loads(flags)
            except Exception:
                flags = []
        for f in flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
            
    # 5. Top 5 Highest-Quality Papers
    # Sort by quality score descending, then by citation count descending
    top_papers = sorted(
        papers,
        key=lambda x: (x.get("methodological_quality_score") or 0, x.get("citation_count") or 0),
        reverse=True
    )[:5]
    
    # 6. Generate Markdown Report Content
    with open(output_filepath, "w", encoding="utf-8") as f:
        f.write("# Cannabis Research Landscape Report\n\n")
        f.write(f"**Date Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Search Query & Filters:** {filter_summary}\n")
        f.write(f"**Total Papers Matched:** {n_papers}\n\n")
        
        f.write("## 1. Study Design × Exposure Method Distribution Matrix\n\n")
        f.write("This table shows the joint distribution of study designs and cannabis/cannabinoid exposure pathways across matching studies:\n\n")
        
        # Build Table Headers
        headers = ["Study Design"] + [em.replace("/", "/<br>").capitalize() for em in exposure_methods] + ["**Total**"]
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("|" + "---|"*len(headers) + "\n")
        
        # Build Table Rows
        total_by_method = {em: 0 for em in exposure_methods}
        for st in study_types:
            row_vals = []
            st_total = 0
            for em in exposure_methods:
                count = matrix[st][em]
                row_vals.append(str(count) if count > 0 else "-")
                st_total += count
                total_by_method[em] += count
            f.write(f"| **{st.capitalize()}** | " + " | ".join(row_vals) + f" | **{st_total}** |\n")
            
        # Total Row
        total_row = ["**Total**"] + [str(total_by_method[em]) for em in exposure_methods] + [f"**{n_papers}**"]
        f.write("| " + " | ".join(total_row) + " |\n\n")
        
        f.write("## 2. Cannabinoid Content Distributions\n\n")
        f.write("Summary statistics for studies that explicitly quantified and reported THC or CBD percentages in the raw material or extract:\n\n")
        
        f.write("| Cannabinoid | N Reporting | Min % | Median % | Average % | Max % |\n")
        f.write("|---|---|---|---|---|---|\n")
        
        if thc_stats:
            f.write(f"| **THC** | {thc_stats['count']} | {thc_stats['min']:.2f}% | {thc_stats['median']:.2f}% | {thc_stats['avg']:.2f}% | {thc_stats['max']:.2f}% |\n")
        else:
            f.write("| **THC** | 0 | - | - | - | - |\n")
            
        if cbd_stats:
            f.write(f"| **CBD** | {cbd_stats['count']} | {cbd_stats['min']:.2f}% | {cbd_stats['median']:.2f}% | {cbd_stats['avg']:.2f}% | {cbd_stats['max']:.2f}% |\n")
        else:
            f.write("| **CBD** | 0 | - | - | - | - |\n")
            
        f.write("\n")
        
        # Outcome Domains Chart/Table
        f.write("## 3. Outcome Domain Prevalence\n\n")
        f.write("Counts of studies investigating specific physiological or psychological outcome domains (studies can target multiple outcome domains):\n\n")
        f.write("| Outcome Domain | Study Count | Prevalence (%) |\n")
        f.write("|---|---|---|\n")
        
        sorted_outcomes = sorted(outcome_counts.items(), key=lambda x: x[1], reverse=True)
        for outcome, count in sorted_outcomes:
            pct = (count / n_papers * 100) if n_papers > 0 else 0
            f.write(f"| {outcome.capitalize()} | {count} | {pct:.1f}% |\n")
        if not sorted_outcomes:
            f.write("| No outcome domains identified | 0 | 0.0% |\n")
            
        f.write("\n")
        
        # Methodological Quality Flags Chart/Table
        f.write("## 4. Methodological Quality Flag Prevalence\n\n")
        f.write("Percentage of matched papers triggering specific methodological quality risk indicators. Higher prevalence flags represent areas with consistent research reporting gaps:\n\n")
        f.write("| Quality Risk Indicator / Flag | Study Count | Prevalence (%) | Risk Level |\n")
        f.write("|---|---|---|---|\n")
        
        sorted_flags = sorted(flag_counts.items(), key=lambda x: x[1], reverse=True)
        for flag, count in sorted_flags:
            pct = (count / n_papers * 100) if n_papers > 0 else 0
            # Label risk levels dynamically based on prevalence
            risk_level = "High" if pct >= 60 else ("Medium" if pct >= 25 else "Low")
            f.write(f"| `{flag}` | {count} | {pct:.1f}% | {risk_level} |\n")
        if not sorted_flags:
            f.write("| No quality risk flags triggered | 0 | 0.0% | None |\n")
            
        f.write("\n")
        
        # Top-Tier Papers
        f.write("## 5. Top 5 Highest-Quality Papers\n\n")
        f.write("Selected papers in the matched corpus that score highest according to the rigorous methodological quality rubric:\n\n")
        
        for idx, p in enumerate(top_papers):
            title = p.get("title")
            journal = p.get("journal") or "Unknown Journal"
            year = p.get("year") or "N/A"
            score = p.get("methodological_quality_score", 0)
            cites = p.get("citation_count", 0)
            st_type = p.get("study_type") or "unknown"
            pop = p.get("population") or "unknown"
            link = p.get("full_text_link") or "#"
            
            f.write(f"### {idx+1}. [{title}]({link})\n")
            f.write(f"- **Journal:** *{journal}* ({year})\n")
            f.write(f"- **Study Type:** {st_type.capitalize()} | **Population:** {pop.capitalize()}\n")
            f.write(f"- **Methodological Quality Score:** **{score}/20** | **Citation Count:** {cites}\n")
            
            # Format and list flags beautifully
            p_flags = p.get("methodological_quality_flags") or []
            if isinstance(p_flags, str):
                try: p_flags = json.loads(p_flags)
                except Exception: p_flags = []
            flag_str = ", ".join([f"`{fl}`" for fl in p_flags]) if p_flags else "None"
            f.write(f"- **Quality Flags Triggered:** {flag_str}\n")
            
            abstract_text = p.get("abstract") or "No abstract available."
            if len(abstract_text) > 350:
                abstract_text = abstract_text[:347] + "..."
            f.write(f"- **Abstract Snippet:** *{abstract_text}*\n\n")
            
    print(f"\n{GREEN}Successfully generated research landscape report file!{RESET}")
    print(f"  -> Path: {BOLD}'{output_filepath}'{RESET}\n")

def main():
    parser = argparse.ArgumentParser(description="Generate a rich, aggregated scientific landscape report.")
    
    # Query parameters
    parser.add_argument("--query", type=str, help="Free-text search against paper titles and abstracts")
    parser.add_argument("--year-min", type=int, help="Minimum publication year")
    parser.add_argument("--year-max", type=int, help="Maximum publication year")
    parser.add_argument("--study-type", type=str, choices=["RCT", "observational", "animal", "in vitro", "review", "meta-analysis"],
                        help="Filter by study design type")
    parser.add_argument("--method", type=str, help="Filter by exposure method")
    parser.add_argument("--thc-min", type=float, help="Minimum THC percentage")
    parser.add_argument("--thc-max", type=float, help="Maximum THC percentage")
    parser.add_argument("--population", type=str, choices=["human", "mouse", "rat", "cell_line", "other"],
                        help="Filter by population")
    parser.add_argument("--outcome", type=str, help="Outcome domains filter (comma-separated)")
    parser.add_argument("--flags", type=str, help="Quality flags filter (e.g. '+large_sample_size')")
    parser.add_argument("--open-access", type=str, choices=["true", "false", "yes", "no", "1", "0"], help="Filter by open access")
    parser.add_argument("--citations-min", type=int, help="Minimum citation count")
    parser.add_argument("--quality-min", type=int, help="Minimum methodological quality score")
    
    # Report parameters
    parser.add_argument("--output", type=str, default="landscape_report.md", help="Filename of the generated landscape report (markdown)")
    
    args = parser.parse_args()
    
    db = DatabaseManager()
    
    oa_filter = None
    if args.open_access:
        oa_filter = args.open_access.lower() in ("true", "yes", "1")
        
    filters = {
        "query": args.query,
        "year_min": args.year_min,
        "year_max": args.year_max,
        "study_type": args.study_type,
        "exposure_method": args.method,
        "thc_min": args.thc_min,
        "thc_max": args.thc_max,
        "population": args.population,
        "outcome": args.outcome,
        "flags": args.flags,
        "open_access": oa_filter,
        "citations_min": args.citations_min,
        "quality_min": args.quality_min
    }
    
    # Build a clean user-facing filter summary string
    sum_parts = []
    for k, v in filters.items():
        if v is not None:
            sum_parts.append(f"{k}={v}")
    filter_summary = ", ".join(sum_parts) if sum_parts else "None (Entire Database)"
    
    try:
        papers = db.search_papers(filters)
    except Exception as e:
        print(f"{RED}Report generation failed due to database query error: {e}{RESET}", file=sys.stderr)
        sys.exit(1)
        
    if not papers:
        print(f"\n{RED}Error: No papers matched filters. Cannot generate landscape report.{RESET}\n", file=sys.stderr)
        sys.exit(1)
        
    generate_landscape_report(papers, args.output, filter_summary)

if __name__ == "__main__":
    main()
