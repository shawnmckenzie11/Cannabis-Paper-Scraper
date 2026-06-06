# extractor.py
import re
import json
from typing import Dict, Any, List, Optional

# --- Chemotype Lookup Maps ---
CHEMOTYPE_MAP = {
    # Chemotype I (High THC, low CBD)
    "og kush": "Chemotype I",
    "sour diesel": "Chemotype I",
    "jack herer": "Chemotype I",
    "gorilla glue": "Chemotype I",
    "gg4": "Chemotype I",
    "blue dream": "Chemotype I",
    "bedrocan": "Chemotype I",
    "girl scout cookies": "Chemotype I",
    "gsc": "Chemotype I",
    "gelato": "Chemotype I",
    "granddaddy purple": "Chemotype I",
    "green crack": "Chemotype I",
    "maui wowie": "Chemotype I",
    "durban poison": "Chemotype I",
    "high-thc": "Chemotype I",
    
    # Chemotype II (Balanced THC:CBD ~ 1:1)
    "bediol": "Chemotype II",
    "cannatonic": "Chemotype II",
    "harlequin": "Chemotype II",
    "pennywise": "Chemotype II",
    "dancehall": "Chemotype II",
    "shark shock": "Chemotype II",
    "royal highness": "Chemotype II",
    "balanced strain": "Chemotype II",
    
    # Chemotype III (High CBD, low THC)
    "bedrolite": "Chemotype III",
    "charlotte's web": "Chemotype III",
    "acdc": "Chemotype III",
    "solodiol": "Chemotype III",
    "avidekel": "Chemotype III",
    "ringo's gift": "Chemotype III",
    "harle-tsu": "Chemotype III",
    "valentine x": "Chemotype III",
    "high-cbd": "Chemotype III"
}

# --- Pre-compiled Regular Expressions ---
# THC Percentage Extraction
THC_PATTERNS = [
    re.compile(r'(?i)(?:THC|tetrahydrocannabinol)[^\w]{1,15}(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%'), # THC 5-10%
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%\s*(?:THC|tetrahydrocannabinol)'),      # 5-10% THC
    re.compile(r'(?i)(?:THC|tetrahydrocannabinol)[^\w]{1,15}(\d+(?:\.\d+)?)\s*%'),                   # THC 10%
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*%\s*(?:THC|tetrahydrocannabinol)'),                         # 10% THC
]

# CBD Percentage Extraction
CBD_PATTERNS = [
    re.compile(r'(?i)(?:CBD|cannabidiol)[^\w]{1,15}(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%'),       # CBD 5-10%
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%\s*(?:CBD|cannabidiol)'),             # 5-10% CBD
    re.compile(r'(?i)(?:CBD|cannabidiol)[^\w]{1,15}(\d+(?:\.\d+)?)\s*%'),                          # CBD 10%
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*%\s*(?:CBD|cannabidiol)'),                                # 10% CBD
]

# Dose Extraction (mg) - Avoiding mg/kg/ml but capturing raw mg values
DOSE_MG_PATTERN = re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*mg\b(?!\s*/\s*(?:kg|ml|g|day))')
DOSE_MG_FALLBACK = re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*mg\b')

# Duration in days, weeks, months, years
DURATION_PATTERN = re.compile(
    r'(?i)(?:for|duration\s+of|period\s+of|treated\s+for|studied\s+for)\s+(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b'
)
DURATION_FALLBACK = re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b')

# Sample Size
SAMPLE_SIZE_PATTERNS = [
    re.compile(r'(?i)\bn\s*=\s*(\d+)\b'),
    re.compile(r'(?i)\bN\s*=\s*(\d+)\b'),
    re.compile(r'(?i)\bsample\s+size\s+(?:of|is)\s+(\d+)\b'),
    re.compile(r'(?i)\b(\d+)\s+(?:patients|subjects|participants|healthy volunteers|mice|rats|rats/group|mice/group)\b')
]

# Quoted Strain Name Matcher
QUOTED_STRAIN_PATTERN = re.compile(r'(?i)(?:strain|variety|cultivar)\s+[\'"]([^\'"]+)[\'"]')

def extract_numeric_value(text: str, patterns: list) -> Optional[float]:
    """Helper to extract a single number or calculate range average from text."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) == 2: # Range
                try:
                    return (float(groups[0]) + float(groups[1])) / 2.0
                except ValueError:
                    pass
            elif len(groups) == 1: # Single value
                try:
                    return float(groups[0])
                except ValueError:
                    pass
    return None

def extract_thc_pct(text: str) -> Optional[float]:
    """Extracts THC percentage from text."""
    return extract_numeric_value(text, THC_PATTERNS)

def extract_cbd_pct(text: str) -> Optional[float]:
    """Extracts CBD percentage from text."""
    return extract_numeric_value(text, CBD_PATTERNS)

def extract_dose_mg(text: str) -> Optional[float]:
    """Extracts absolute dose in mg from text."""
    match = DOSE_MG_PATTERN.search(text)
    if match:
        return float(match.group(1))
    # Try fallback if standard pattern misses
    match = DOSE_MG_FALLBACK.search(text)
    if match:
        return float(match.group(1))
    return None

def extract_duration_days(text: str) -> Optional[float]:
    """Extracts study duration and converts it to float days, ignoring age references."""
    if not text:
        return None

    def convert_to_days(val: float, unit: str) -> float:
        unit = unit.lower()
        if "day" in unit:
            return val
        elif "week" in unit:
            return val * 7.0
        elif "month" in unit:
            return val * 30.0
        elif "year" in unit:
            return val * 365.0
        return val

    # 1. First, search for explicit context-aware study duration matches
    match = DURATION_PATTERN.search(text)
    if match:
        days = convert_to_days(float(match.group(1)), match.group(2))
        if days <= 30 * 365.0:
            return days

    # 2. Next, check for hyphenated study duration formats (e.g. "6-week study", "3-month trial")
    hyphen_pattern = re.compile(
        r'(?i)\b(\d+(?:\.\d+)?)-(day|week|month|year)s?\b\s*(?:study|trial|intervention|treatment|regimen|course|follow-up|period)'
    )
    match = hyphen_pattern.search(text)
    if match:
        days = convert_to_days(float(match.group(1)), match.group(2))
        if days <= 30 * 365.0:
            return days

    # 3. Fallback: find general matches of duration, but filter out age-related references and dates
    fallback_pattern = re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b')
    for m in fallback_pattern.finditer(text):
        val_str, unit = m.group(1), m.group(2).lower()
        val = float(val_str)
        start_idx = m.start()
        end_idx = m.end()

        # Check pre-context for age indicators (larger window of 60 characters)
        pre_context = text[max(0, start_idx - 60):start_idx].lower()
        age_words = ["aged", "age", "adults", "patients", "subjects", "participants", "adolescents", "children", "men", "women", "volunteers", "years of age", "old"]
        if any(w in pre_context for w in age_words):
            continue

        # Check if it's preceded by a hyphen and a number indicating an age range (e.g. "18-" before "24 years")
        range_match = re.search(r'\b\d+\s*-\s*$', pre_context)
        if range_match:
            num_before = re.findall(r'\b\d+\b', range_match.group(0))
            if num_before:
                val_before = float(num_before[-1])
                if unit == "year" and (val_before >= 10 or val >= 10):
                    continue

        # Check post-context for age indicators
        post_context = text[end_idx:min(len(text), end_idx + 15)].lower()
        if any(w in post_context for w in ["old", "of age"]):
            continue

        # If it looks like a calendar year, skip
        if unit == "year" and 1900 <= val <= 2030:
            continue

        days = convert_to_days(val, unit)
        if days <= 30 * 365.0:
            return days

    return None

def format_study_duration(days: Any) -> str:
    """Formats study duration in days to a clean string in years, months, or days (no weeks)."""
    if days is None or days == "":
        return "N/A"
    try:
        d = float(days)
    except (ValueError, TypeError):
        return "N/A"

    if d <= 0:
        return "N/A"

    # Express in years if >= 365 days
    if d >= 365:
        yrs = d / 365.0
        if yrs.is_integer():
            yrs_int = int(yrs)
            return f"{yrs_int} year{'s' if yrs_int > 1 else ''}"
        formatted_yrs = f"{yrs:.1f}".rstrip('0').rstrip('.')
        return f"{formatted_yrs} year{'s' if formatted_yrs != '1' else ''}"

    # Express in months if >= 30 days
    if d >= 30:
        mos = d / 30.0
        if mos.is_integer():
            mos_int = int(mos)
            return f"{mos_int} month{'s' if mos_int > 1 else ''}"
        formatted_mos = f"{mos:.1f}".rstrip('0').rstrip('.')
        return f"{formatted_mos} month{'s' if formatted_mos != '1' else ''}"

    # Default to days
    d_int = int(d) if d.is_integer() else d
    return f"{d_int} day{'s' if d_int != 1 else ''}"

def extract_sample_size(text: str) -> Optional[int]:
    """Extracts sample size N from text."""
    for pattern in SAMPLE_SIZE_PATTERNS:
        match = pattern.search(text)
        if match:
            # Avoid matching 1 or 2 as sample size in generic text
            val = int(match.group(1))
            if val > 2:
                return val
    return None

def extract_strain_info(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extracts reported strain and normalizes it to a Chemotype I/II/III.
    
    Returns:
        tuple: (strain_reported, strain_normalized)
    """
    text_lower = text.lower()
    
    # 1. First, search for exact matches from our chemotype list
    for strain, chemotype in CHEMOTYPE_MAP.items():
        if re.search(r'\b' + re.escape(strain) + r'\b', text_lower):
            # Find the original capitalization in the text if possible
            match_orig = re.search(r'\b' + re.escape(strain) + r'\b', text, re.IGNORECASE)
            reported = match_orig.group(0) if match_orig else strain
            return reported, chemotype

    # 2. Search for quoted strain names, e.g., strain "Bedrocan"
    match = QUOTED_STRAIN_PATTERN.search(text)
    if match:
        reported = match.group(1)
        # Check if the extracted quoted strain is normalized
        normalized = CHEMOTYPE_MAP.get(reported.lower())
        return reported, normalized
        
    return None, None

def keyword_match(text: str, keywords: List[str]) -> bool:
    """Helper to check if any keyword is matched as a whole word in the text."""
    for k in keywords:
        # Use regex word boundaries for short terms or single words to avoid false substring matches
        if len(k) <= 4 or " " not in k:
            pattern = r'\b' + re.escape(k) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                return True
        else:
            if k in text.lower():
                return True
    return False

def get_methods_text(title: str, abstract: str) -> str:
    """Extracts Title, Objectives, Methods, or Results sections of the abstract,
    discarding Conclusions, Discussion, Significance, or other concluding sections.
    
    If a clear Methods section is present, we isolate it along with the Title to
    minimize false positives from other sections (such as literature background).
    Otherwise, we extract all other non-concluding sections.
    """
    if not abstract:
        return title
        
    header_pattern = re.compile(
        r'\b(background|introduction|objective|objectives|aim|aims|'
        r'method|methods|methodology|materials and methods|patients and methods|'
        r'result|results|findings|'
        r'conclusion|conclusions|discussion|significance|implications|interpretation|summary|key\s+words?|highlights)\b\s*[:\.]',
        re.IGNORECASE
    )
    
    matches = list(header_pattern.finditer(abstract))
    if not matches:
        # Unstructured abstract: strip conclusion sentence if clearly marked
        conclusion_pattern = re.compile(
            r'\b(in conclusion|we conclude|to conclude|in summary|concluding remarks)\b',
            re.IGNORECASE
        )
        c_match = conclusion_pattern.search(abstract)
        if c_match:
            clean_abstract = abstract[:c_match.start()].strip()
            return title + "\n\n" + clean_abstract
        return title + "\n\n" + abstract.strip()
        
    # Check if a Methods section is present
    methods_headers = {'method', 'methods', 'methodology', 'materials and methods', 'patients and methods'}
    methods_match_idx = -1
    for idx, match in enumerate(matches):
        if match.group(1).lower() in methods_headers:
            methods_match_idx = idx
            break
            
    if methods_match_idx != -1:
        # If a Methods section is found, we extract ONLY the Methods section content (+ Title)
        # to ensure robust methods isolation (preventing background/conclusions false positives)
        match = matches[methods_match_idx]
        start_idx = match.end()
        end_idx = matches[methods_match_idx + 1].start() if methods_match_idx + 1 < len(matches) else len(abstract)
        methods_content = abstract[start_idx:end_idx].strip()
        return title + "\n\n" + match.group(0) + " " + methods_content

    # If no Methods section is found, extract Title and other allowed sections (Objectives, Results, Introduction, etc.)
    allowed_headers = {
        'background', 'introduction', 'objective', 'objectives', 'aim', 'aims',
        'result', 'results', 'findings'
    }
    
    allowed_parts = []
    
    # If there is text before the first header, it's typically intro/background context, so keep it
    first_start = matches[0].start()
    pre_text = abstract[:first_start].strip()
    if pre_text:
        allowed_parts.append(pre_text)
        
    for i, match in enumerate(matches):
        header_name = match.group(1).lower()
        start_idx = match.end()
        end_idx = matches[i+1].start() if i + 1 < len(matches) else len(abstract)
        
        content = abstract[start_idx:end_idx].strip()
        if header_name in allowed_headers:
            allowed_parts.append(match.group(0) + " " + content)
            
    combined_abstract = "\n\n".join(allowed_parts)
    return title + "\n\n" + combined_abstract

def infer_publication_type(title: str, abstract: str) -> str:
    """Infers Stage 1 publication type from text keywords."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # Check full abstract for explicit publication type metadata first
    abstract_lower = (abstract or "").lower()
    
    # 1. Meta-analysis
    if "publication type: meta-analysis" in abstract_lower:
        return "meta-analysis"
    if keyword_match(combined, ["meta-analysis", "meta-analyses", "pooled analysis", "systematic overview"]):
        return "meta-analysis"
        
    # 2. Systematic review
    if keyword_match(combined, ["systematic review", "systematic reviews", "scoping review", "scoping reviews"]):
        return "systematic review"
        
    # 3. Review (general)
    if "publication type: review" in abstract_lower:
        return "review"
    
    review_keywords = [
        "literature review", "overview of reviews",
        "narrative review", "critical review", "mini-review", "minireview", "review article",
        "review paper", "this review", "the present review", "in this review", "we review",
        "current review", "comprehensive review", "review of the literature", "this mini-review",
        "this minireview", "article reviews", "reviews the current", "reviews the literature"
    ]
    if keyword_match(combined, review_keywords):
        return "review"
    if "review" in title.lower():
        if re.search(r'\breviews?\b', title, re.IGNORECASE):
            return "review"
    if re.search(r'\b(this|present|current|our)\s+review\b', combined, re.IGNORECASE):
        return "review"
    if re.search(r'\breview\s+(highlights|summarizes|discusses|focuses|provides|aims to|examines|synthesizes|outlines)\b', combined, re.IGNORECASE):
        return "review"
        
    # 4. Case study
    if keyword_match(combined, ["case study", "case studies", "case report", "case reports", "case series", "clinical case", "case-report", "case-series"]):
        return "case study"
        
    # 5. Editorial
    if keyword_match(combined, ["editorial", "editorials"]):
        return "editorial"
        
    # 6. Comment
    if keyword_match(combined, ["commentary", "commentaries", "comment", "opinion", "viewpoint"]):
        return "comment"
        
    # 7. Letter to the editor
    if keyword_match(combined, ["letter to the editor", "letters to the editor"]):
        return "letter to the editor"
        
    # 8. Perspectives paper
    if keyword_match(combined, ["perspective", "perspectives"]):
        return "perspectives paper"
        
    # Default to original research
    return "original research"

def infer_study_type(title: str, abstract: str) -> List[str]:
    """Infers Stage 2 study type from text keywords, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # First check publication type for compatibility fallback
    pub_type = infer_publication_type(title, abstract)
    if pub_type != "original research":
        if pub_type in ("review", "systematic review"):
            return ["review"]
        elif pub_type == "meta-analysis":
            return ["meta-analysis"]
        elif pub_type == "case study":
            return ["case study"]
        elif pub_type in ("editorial", "comment", "letter to the editor", "perspectives paper"):
            return ["editorial"]
            
    types = []
    
    # 1. Clinical
    # RCT
    if keyword_match(combined, ["double-blind", "randomized controlled", "placebo-controlled", "rct", "randomised controlled", "clinical trial"]):
        types.append("Clinical (RCT)")
    # prospective
    if keyword_match(combined, ["prospective", "prospectively", "prospective cohort"]):
        types.append("Clinical (prospective)")
    # retrospective
    if keyword_match(combined, ["retrospective", "retrospectively", "chart review", "historical cohort"]):
        types.append("Clinical (retrospective)")
    # observational
    if keyword_match(combined, ["observational", "cross-sectional", "survey", "surveys", "registry", "registries", "longitudinal", "case-control", "epidemiological", "cohort", "cohorts", "gwas", "genome-wide", "genomewide"]):
        types.append("Clinical (observational)")
        
    # 2. Animal Models
    # mouse
    if keyword_match(combined, ["mouse", "mice", "murine", "c57bl/6"]):
        types.append("Animal Models (mouse)")
    # rat
    if keyword_match(combined, ["rat", "rats", "wistar", "sprague-dawley"]):
        types.append("Animal Models (rat)")
    # non-human primate
    if keyword_match(combined, ["macaque", "rhesus", "monkey", "monkeys", "primate", "primates", "baboon", "chimpanzee"]):
        types.append("Animal Models (non-human primate)")
    # other animal
    if keyword_match(combined, ["dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish", "drosophila"]):
        types.append("Animal Models (other)")
    elif keyword_match(combined, ["animal", "in vivo", "animal model", "rodent", "rodents"]):
        if not any(t.startswith("Animal Models (") for t in types):
            types.append("Animal Models (other)")
            
    # 3. Cell Culture
    # primary cells
    if keyword_match(combined, ["primary cell", "primary cells", "primary culture", "primary neuronal", "primary microglia", "splenocytes", "primary hepatocytes"]):
        types.append("Cell Culture (primary cells)")
    # cell lines
    if keyword_match(combined, ["cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7", "sh-sy5y", "jurkat", "cho cells"]):
        types.append("Cell Culture (cell lines)")
    # organoids
    if keyword_match(combined, ["organoid", "organoids", "spheroid", "spheroids", "3d culture", "3d cultures"]):
        types.append("Cell Culture (organoids)")
    # co-culture
    if keyword_match(combined, ["co-culture", "co-cultures", "coculture", "cocultures"]):
        types.append("Cell Culture (co-culture)")
    elif keyword_match(combined, ["in vitro", "cultured cells", "culture assay", "cell culture", "cell cultures", "epithelial cells", "epithelial cell", "airway epithelial"]):
        if not any(t.startswith("Cell Culture (") for t in types):
            types.append("Cell Culture (cell lines)")
            
    if not types:
        types.append("Clinical (observational)")
        
    if "Clinical (RCT)" in types:
        # Remove Clinical (observational) as RCTs are interventional, not observational
        if "Clinical (observational)" in types:
            types.remove("Clinical (observational)")
            
        # Check if animal/cell keywords are in the title to keep them, otherwise remove
        title_lower = title.lower()
        animal_keywords = [
            "mouse", "mice", "murine", "rat", "rats", "rodent", "rodents", "animal", "animals", 
            "dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish", 
            "drosophila", "macaque", "rhesus", "monkey", "monkeys", "primate", "primates", 
            "baboon", "chimpanzee", "canine", "feline", "in vivo"
        ]
        cell_keywords = [
            "in vitro", "cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7", 
            "sh-sy5y", "jurkat", "cho cells", "primary cell", "primary cells", "primary culture", 
            "organoid", "organoids", "spheroid", "spheroids", "co-culture", "co-cultures", 
            "coculture", "cocultures", "microglia", "neurons", "epithelial cells", 
            "epithelial cell", "airway epithelial", "cultured cells", "culture assay", 
            "cell culture", "cell cultures"
        ]
        
        has_animal_title = any(k in title_lower for k in animal_keywords)
        has_cell_title = any(k in title_lower for k in cell_keywords)
        
        if not has_animal_title:
            types = [t for t in types if not t.startswith("Animal Models (")]
        if not has_cell_title:
            types = [t for t in types if not t.startswith("Cell Culture (")]
            
    return list(set(types))

def postprocess_study_type(study_type: List[str], population: List[str]) -> List[str]:
    """Aligns study_type with population to prevent cross-contamination false positives."""
    has_human = "human" in population
    has_animal = any(p in population for p in ["mouse", "rat", "other"])
    has_cell = "cell_line" in population
    
    types = list(study_type)
    
    if has_human and not has_animal and not has_cell:
        # Pure human study
        types = [t for t in types if not t.startswith("Animal Models (") and not t.startswith("Cell Culture (")]
        if not types:
            types = ["Clinical (observational)"]
            
    elif has_animal and not has_human:
        # Pure animal study
        types = [t for t in types if not t.startswith("Clinical (") and t != "RCT" and t != "observational"]
        if not types:
            if "mouse" in population:
                types = ["Animal Models (mouse)"]
            elif "rat" in population:
                types = ["Animal Models (rat)"]
            else:
                types = ["Animal Models (other)"]
                
    elif has_cell and not has_human and not has_animal:
        # Pure cell culture study
        types = [t for t in types if not t.startswith("Clinical (") and not t.startswith("Animal Models (") and t != "RCT" and t != "observational"]
        if not types:
            types = ["Cell Culture (cell lines)"]
            
    return list(set(types))
    
def infer_exposure_method(title: str, abstract: str, study_type: Any, population: Optional[Any] = None) -> List[str]:
    """Extracts exposure method from text keywords, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # Normalize inputs to sets/lists
    if isinstance(study_type, str):
        study_types = {study_type}
    else:
        study_types = set(study_type or [])
        
    if population is None:
        populations = set(infer_population(title, abstract, study_type))
    elif isinstance(population, str):
        populations = {population}
    else:
        populations = set(population or [])
        
    methods = []
    
    # Group A: Clinical exposure (human/clinical setting)
    if "human" in populations or study_types.intersection({"RCT", "observational"}) or any(s.startswith("Clinical (") for s in study_types):
        if keyword_match(combined, ["smoke", "smoked", "smoking", "joint", "combustion", "cigarette", "cigarettes", "vaporized", "vaporised", "vape", "vaping", "vaporizer", "vaporisation", "inhaled", "inhalation"]):
            methods.append("inhaled")
        if keyword_match(combined, ["oral", "edible", "ingested", "capsule", "gummy", "cookies", "oil ingestion", "brownie", "gavage"]):
            methods.append("oral")
        if keyword_match(combined, ["sublingual", "under the tongue", "drops", "tincture", "tinctures"]):
            methods.append("sublingual")
        if keyword_match(combined, ["injection", "intravenous", "iv", "intraperitoneal", "ip", "subcutaneous", "intramuscular", "injected"]):
            methods.append("injected")
 
    # Group B: In vitro exposure (cells/tissue setting)
    if "cell_line" in populations or "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types):
        if keyword_match(combined, ["conditioned media", "smoke extract", "cse", "vapor extract", "gaseous extract", "smoke-conditioned"]):
            methods.append("smoke/vapor conditioned media")
        if keyword_match(combined, ["cell exposure to smoke", "cells exposed to vapor", "chamber exposure of cells", "smoke stream", "direct vapor exposure", "exposure of cells to smoke", "exposure of cells to vapor", "air-liquid interface", "air liquid interface", "ali exposure", "ali", "aerosol exposure", "exposed directly to vapor", "exposed directly to smoke"]):
            methods.append("exposure of cells to smoke/vapor")
 
    # Group C: In vivo exposure (animal models)
    if populations.intersection({"mouse", "rat", "other"}) or "animal" in study_types or any(s.startswith("Animal Models (") for s in study_types):
        if keyword_match(combined, ["nose-only", "nose only", "snout exposure", "head-out"]):
            methods.append("nose only smoke/vapor")
        if keyword_match(combined, ["whole body", "whole-body", "chamber exposure", "whole body smoke", "whole body vapor"]) or (
            keyword_match(combined, ["smoke", "vapor", "vaporized", "vaporised", "vape", "vaping", "inhalation", "inhalational"]) and "nose only smoke/vapor" not in methods
        ):
            methods.append("whole body. smoke/vapor")
        if keyword_match(combined, ["injection", "injected", "intravenous", "intraperitoneal", "ip", "iv", "subcutaneous", "sc", "intramuscular", "im"]):
            methods.append("injection cannabinoids")
        if keyword_match(combined, ["gavage", "oral administration", "fed", "diet", "oral gavage", "ingested", "oral"]):
            methods.append("oral administration")
        if keyword_match(combined, ["sublingual", "sub-lingual", "under tongue"]):
            methods.append("sub-lingual")
        if keyword_match(combined, ["intranasal", "intra-nasal", "nasal instillation", "nasal drops"]):
            methods.append("intranasal")
        if keyword_match(combined, ["intratracheal", "intratracheal instillation", "intra-tracheal", "lung instillation"]):
            methods.append("intratracheal")
            
    if not methods:
        if "cell_line" in populations or "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types):
            methods.append("cannabinoids dissolved in media")
        elif "human" in populations or study_types.intersection({"RCT", "observational"}) or any(s.startswith("Clinical (") for s in study_types):
            methods.append("inhaled")
        else:
            methods.append("injection cannabinoids")
            
    return list(set(methods))
    
def infer_cannabis_type(title: str, abstract: str, study_type: Any, exposure_method: Any) -> List[str]:
    """Infers the type of cannabis product being administered or studied, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # Normalize inputs
    if isinstance(exposure_method, str):
        exposure_methods = {exposure_method}
    else:
        exposure_methods = set(exposure_method or [])
        
    types = []
    
    # 1. Edibles check
    if keyword_match(combined, ["edible", "edibles", "gummy", "gummies", "chocolate", "chocolates", "drink", "drinks", "beverage", "beverages", "brownie", "brownies", "cookies", "cookie", "capsule", "capsules"]):
        types.append("edibles")
    # 2. Vape pen check
    if keyword_match(combined, ["vape pen", "cartridge", "cartridges", "e-cigarette", "vape cartridge", "distillate vape", "vape", "vapes", "vaping", "vaporizer", "vaporizers", "vaporised", "vaporized", "vapor", "vapors", "vapour", "vapours", "aerosol", "aerosols"]):
        types.append("vape pen")
    # 3. Hashish / Kief check
    if keyword_match(combined, ["hashish", "hash", "kief", "charas", "bubble hash"]):
        types.append("hashish/kief")
    # 4. Pure Cannabinoid check
    if keyword_match(combined, ["pure thc", "pure cbd", "synthetic cannabinoid", "synthetic cannabinoids", "dronabinol", "nabilone", "marinol", "isolate", "isolates", "pure cannabinoid", "pure cannabinoids", "cannabidiol isolate"]):
        types.append("pure cannabinoid")
    # 5. Concentrates check
    if keyword_match(combined, ["shatter", "tincture", "tinctures", "resin", "concentrate", "concentrates", "extract", "extracts", "hash oil", "honey oil", "bho", "rosin", "wax"]):
        types.append("concentrates")
    # 6. Dried flower check
    if keyword_match(combined, ["flower", "bud", "buds", "dried cannabis", "joint", "joints", "combusted flower", "cannabis herb", "herbal cannabis", "marijuana cigarette", "marijuana cigarettes", "cigarette", "cigarettes"]):
        types.append("dried flower")
        
    # 7. CB receptor agonist check
    if keyword_match(combined, ["cb receptor agonist", "cb receptor agonists", "cb1 agonist", "cb1 agonists", "cb2 agonist", "cb2 agonists", "cannabinoid receptor agonist", "cannabinoid receptor agonists", "win 55,212-2", "win 55212-2", "win-55212-2", "win-55,212-2", "cp 55,940", "cp 55940", "cp-55940", "hu-210", "hu210", "jwh-018", "jwh018"]):
        types.append("CB receptor agonist")
        
    # 8. CB receptor antagonist check
    if keyword_match(combined, ["cb receptor antagonist", "cb receptor antagonists", "cb1 antagonist", "cb1 antagonists", "cb2 antagonist", "cb2 antagonists", "cannabinoid receptor antagonist", "cannabinoid receptor antagonists", "inverse agonist", "inverse agonists", "rimonabant", "sr141716", "sr 141716", "am251", "am-251", "am630", "am-630", "sr144528"]):
        types.append("CB receptor antagonist")
        
    # Fallback mappings based on exposure methods if no explicit types matched
    if not types:
        for exp in exposure_methods:
            if exp in ("smoked", "inhaled", "whole body. smoke/vapor", "nose only smoke/vapor"):
                types.append("dried flower")
            elif exp in ("vaporized", "vape pen"):
                types.append("vape pen")
            elif exp in ("oral/edible", "oral", "oral administration"):
                types.append("edibles")
            elif exp in ("tincture", "sublingual", "sub-lingual"):
                types.append("concentrates")
                
    if not types:
        types.append("unknown")
        
    return list(set(types))
 
def infer_population(title: str, abstract: str, study_type: Any) -> List[str]:
    """Extracts population (human, mouse, rat, cell_line, other) from text, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    if isinstance(study_type, str):
        study_types = {study_type}
    else:
        study_types = set(study_type or [])
        
    pops = []
    
    if "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types) or keyword_match(combined, ["cell line", "cell lines", "cell culture", "cell cultures", "cultured cells", "hela", "hepg2", "epithelial cells", "bronchial epithelial"]):
        pops.append("cell_line")
    if keyword_match(combined, ["mouse", "mice", "murine", "c57bl"]):
        pops.append("mouse")
    if keyword_match(combined, ["rat", "rats", "wistar", "sprague"]):
        pops.append("rat")
    # Human population keywords check: separate unambiguous and ambiguous terms
    human_unambiguous = [
        "patient", "patients", "participant", "participants", "volunteer", "volunteers",
        "man", "men", "woman", "women", "human", "humans", "clinical", "individual", "individuals",
        "boy", "boys", "girl", "girls", "child", "children", "pediatric", "pediatrics",
        "adolescent", "adolescents"
    ]
    human_ambiguous = ["adult", "adults", "subject", "subjects"]
    
    # If animal-related terms are present in the text, ambiguous words like "adult" or "subject"
    # are likely to refer to the animals, so we exclude them from the human keywords list.
    has_animals = keyword_match(combined, [
        "mouse", "mice", "murine", "c57bl", "rat", "rats", "wistar", "sprague",
        "rodent", "rodents", "animal", "animals", "dog", "dogs", "monkey", "monkeys",
        "pig", "pigs", "rabbit", "rabbits", "feline", "canine"
    ])
    
    human_keywords = human_unambiguous
    if not has_animals:
        human_keywords = human_unambiguous + human_ambiguous
        
    if keyword_match(combined, human_keywords):
        # Prevent false-positive "human" population in pure animal/cell culture studies
        # where terms like "human" are used contextually (e.g., "to model human disease")
        is_pure_non_clinical = (
            any(s.startswith("Animal Models (") or s.startswith("Cell Culture (") for s in study_types) or 
            "animal" in study_types or "in vitro" in study_types
        ) and not (
            any(s.startswith("Clinical (") or s == "RCT" or s == "observational" for s in study_types)
        )
        
        if is_pure_non_clinical:
            # Require actual human subjects/cohort terms to confirm human population
            human_subject_keywords = [
                "patient", "patients", "participant", "participants", "volunteer", "volunteers",
                "clinical trial", "clinical study", "men", "women", "cohort", "human subjects", "human participants"
            ]
            if keyword_match(combined, human_subject_keywords):
                pops.append("human")
        else:
            pops.append("human")
    if keyword_match(combined, ["dog", "pig", "monkey", "rabbit", "feline", "canine"]):
        pops.append("other")
        
    if not pops:
        if study_types.intersection({"RCT", "observational"}) or any(s.startswith("Clinical (") for s in study_types):
            pops.append("human")
        elif "animal" in study_types or any(s.startswith("Animal Models (") for s in study_types):
            pops.append("mouse")
        else:
            pops.append("other")
            
    return list(set(pops))

def extract_outcomes(title: str, abstract: str) -> List[str]:
    """Identifies multi-label outcome domains from the text."""
    combined = (title + " " + abstract).lower()
    outcomes = []
    
    mapping = {
        "pain": ["pain", "analgesic", "nociception", "hyperalgesia", "allodynia", "neuropathic"],
        "anxiety": ["anxiety", "anxiolytic", "fear", "panic", "generalized anxiety", "ptsd"],
        "cognition": ["cognition", "cognitive", "memory", "learning", "attention", "executive function", "dementia", "alzheimer"],
        "inflammation": ["inflammation", "inflammatory", "cytokine", "tnf", "interleukin", "il-6", "anti-inflammatory", "arthritis"],
        "addiction": ["addiction", "dependence", "withdrawal", "craving", "abuse", "substance use", "relapse"],
        "oncology": ["oncology", "cancer", "tumor", "tumour", "chemotherapy", "glioblastoma", "carcinoma", "antineoplastic"],
        "neuroprotection": ["neuroprotection", "neuroprotective", "stroke", "ischemia", "brain injury", "sclerosis", "epilepsy", "seizure"],
        "sleep": ["sleep", "insomnia", "actigraphy", "sleep quality", "melatonin"]
    }
    
    for domain, keywords in mapping.items():
        if keyword_match(combined, keywords):
            outcomes.append(domain)
            
    if not outcomes:
        outcomes.append("other")
        
    return outcomes



def detect_multiple_doses(title: str, abstract: str) -> bool:
    """Heuristic to detect if there is discernable information about multiple doses."""
    combined = (title + " " + abstract).lower()
    
    # 1. Look for dose-response keywords
    dose_keywords = [
        "dose-response", "dose response", "multiple doses", "different doses", 
        "varying doses", "graded doses", "dose-dependent", "dose dependent",
        "doses of", "dose levels", "various doses"
    ]
    if any(k in combined for k in dose_keywords):
        return True
        
    # 2. Look for multiple numerical mg or mg/kg matches, e.g., "5, 10, or 20 mg"
    dose_matches = re.findall(r'\b\d+(?:\.\d+)?\s*(?:mg|mg/kg)\b', combined)
    if len(set(dose_matches)) >= 2:
        return True
        
    return False

def detect_multiple_time_intervals(title: str, abstract: str) -> bool:
    """Heuristic to detect if there is discernable information about multiple time intervals."""
    combined = (title + " " + abstract).lower()
    
    time_keywords = [
        "time intervals", "multiple timepoints", "different timepoints", 
        "time points", "repeated administration", "longitudinal", 
        "serial measurements", "repeated measures", "time-dependent", 
        "time dependent", "course of", "days 7 and 14", "weeks 1 and 2",
        "follow-up", "follow up"
    ]
    if any(k in combined for k in time_keywords):
        return True
        
    return False

def generate_heuristic_summary(data: Dict[str, Any]) -> str:
    """Generates a fallback description/summary based on extracted heuristics."""
    study_type = data.get("study_type") or []
    cannabis_type = data.get("cannabis_type") or []
    exposure_method = data.get("exposure_method") or []
    population = data.get("population") or []
    strain_reported = data.get("strain_reported")
    
    # Helper to convert list/str to string description
    def to_desc(val):
        if isinstance(val, str):
            return val
        if not val:
            return "unspecified"
        return ", ".join(val)
        
    study_desc = to_desc(study_type)
    cannabis_desc = to_desc(cannabis_type)
    exposure_desc = to_desc(exposure_method)
    pop_desc = to_desc(population)
    
    # Vowel prefix check
    study_word = study_desc.lower()
    if study_word.startswith(('a', 'e', 'i', 'o', 'u')) or "rct" in study_word or "observational" in study_word:
        prefix = "an"
    else:
        prefix = "a"
        
    summary = f"This is {prefix} {study_desc} study investigating {cannabis_desc} cannabis administration via {exposure_desc} in {pop_desc} models."
    
    if strain_reported:
        summary += f" Reported strain: {strain_reported}."
    else:
        summary += " No specific strain was specified."
        
    return summary

def extract_all_heuristics(title: str, abstract: str) -> Dict[str, Any]:
    """Convenience pipeline to run all heuristic extractions on a paper.
    
    Args:
        title: Paper title
        abstract: Paper abstract
        
    Returns:
        Dict: Extracted fields
    """
    publication_type = infer_publication_type(title, abstract)
    study_type = infer_study_type(title, abstract)
    population = infer_population(title, abstract, study_type)
    study_type = postprocess_study_type(study_type, population)
    exposure_method = infer_exposure_method(title, abstract, study_type, population)
    thc_pct = extract_thc_pct(abstract)
    # If title mentions THC, we can check there too
    if thc_pct is None:
        thc_pct = extract_thc_pct(title)
        
    cbd_pct = extract_cbd_pct(abstract)
    if cbd_pct is None:
        cbd_pct = extract_cbd_pct(title)
        
    dose_mg = extract_dose_mg(abstract)
    strain_reported, strain_normalized = extract_strain_info(abstract)
    if not strain_reported:
        strain_reported, strain_normalized = extract_strain_info(title)
        
    duration_days = extract_duration_days(abstract)
    if duration_days is None:
        duration_days = extract_duration_days(title)
    sample_size = extract_sample_size(abstract)
    outcome_domain = extract_outcomes(title, abstract)
    cannabis_type = infer_cannabis_type(title, abstract, study_type, exposure_method)
    
    multiple_doses = detect_multiple_doses(title, abstract)
    multiple_time_intervals = detect_multiple_time_intervals(title, abstract)
    
    result = {
        "study_type": study_type,
        "exposure_method": exposure_method,
        "thc_pct": thc_pct,
        "cbd_pct": cbd_pct,
        "dose_mg": dose_mg,
        "puff_count": None,
        "thc_mg_ml": None,
        "thc_mg_g": None,
        "thc_mg_kg": None,
        "cbd_mg_ml": None,
        "cbd_mg_g": None,
        "cbd_mg_kg": None,
        "strain_reported": strain_reported,
        "strain_normalized": strain_normalized,
        "duration_days": duration_days,
        "inhaled_exposure_duration": None,
        "administration_frequency": None,
        "treatment_duration": None,
        "population": population,
        "sample_size": sample_size,
        "outcome_domain": outcome_domain,
        "methodological_quality_flags": [],
        "multiple_doses": multiple_doses,
        "multiple_time_intervals": multiple_time_intervals,
        "cannabis_type": cannabis_type,
        "publication_type": publication_type
    }
    result["summary"] = generate_heuristic_summary(result)
    return result

# --- Cannabis/Cannabinoid Positive & Negative Context Keywords for Relevance Checking ---

POSITIVE_KEYWORDS = [
    r"\bcannabi\w*",
    r"\btetrahydrocannabi\w*",
    r"\bmarijuana\w*",
    r"\bhemp\w*",
    r"\bthc\b",
    r"\bthca\b",
    r"\bcbd\s+oil\b",
    r"\bcbda\b",
    r"\bepidiolex\b",
    r"\bsativex\b",
    r"\bdronabinol\b",
    r"\bnabilone\b",
    r"\bcb1\b",
    r"\bcb2\b",
    r"\bdelta-9\b",
    r"\bchemotype\b",
]

# Explicit Acronym Collision Negative Context Patterns
CBD_BILE_DUCT_PATTERNS = [
    r"\bcommon bile duct\b",
    r"\bbile duct\b",
    r"\bbiliary\b",
    r"\bcholedochal\b",
    r"\bcholangiocarcinoma\b",
    r"\bgallbladder\b",
    r"\bcholecystectomy\b",
    r"\bduodenal\b",
    r"\bsphincter of oddi\b"
]

CBD_NEURO_PATTERNS = [
    r"\bcorticobasal degeneration\b",
    r"\bcorticobasal syndrome\b",
    r"\bcorticobasal\b",
    r"\btauopathy\b",
    r"\btau pathologies\b",
    r"\bprogressive supranuclear palsy\b",
    r"\bfrontotemporal dementia\b",
    r"\bneurofibrillary tangles\b"
]

CBD_URBAN_PATTERNS = [
    r"\bcentral business district\b",
    r"\bcentral business districts\b",
    r"\burban planning\b",
    r"\bcity center\b",
    r"\bcity centre\b",
    r"\btransit-oriented\b",
    r"\bcongestion pricing\b",
    r"\bmetropolitan area\b"
]

CBD_BIODIVERSITY_PATTERNS = [
    r"\bconvention on biological diversity\b",
    r"\bbiodiversity conservation\b",
    r"\bnagoya protocol\b",
    r"\bcartagena protocol\b",
    r"\baccess and benefit-sharing\b",
    r"\bunep\b"
]

CBD_DEFENSE_PATTERNS = [
    r"\bchemical and biological defense\b",
    r"\bhomeland security\b",
    r"\bweapon of mass destruction\b",
    r"\bwmd\b"
]

ALL_NEGATIVES = (
    CBD_BILE_DUCT_PATTERNS +
    CBD_NEURO_PATTERNS +
    CBD_URBAN_PATTERNS +
    CBD_BIODIVERSITY_PATTERNS +
    CBD_DEFENSE_PATTERNS
)

def is_cannabis_related(title: str, abstract: str) -> tuple[bool, str]:
    """Analyzes a paper's text to determine if it is cannabis-related.
    
    Returns:
        tuple: (is_relevant: bool, reason: str)
    """
    combined = (title + " " + (abstract or "")).lower()
    
    # 1. Check for negative phrase collision matches
    for pattern in ALL_NEGATIVES:
        if re.search(pattern, combined):
            # Check if there is an explicit positive keyword to override
            has_positive_override = any(re.search(p, combined) for p in POSITIVE_KEYWORDS)
            if not has_positive_override:
                category = "Acronym collision"
                if pattern in CBD_BILE_DUCT_PATTERNS: category = "Common Bile Duct (CBD)"
                elif pattern in CBD_NEURO_PATTERNS: category = "Corticobasal Degeneration (CBD)"
                elif pattern in CBD_URBAN_PATTERNS: category = "Central Business District (CBD)"
                elif pattern in CBD_BIODIVERSITY_PATTERNS: category = "Convention on Biological Diversity (CBD)"
                elif pattern in CBD_DEFENSE_PATTERNS: category = "Chemical & Biological Defense (CBD)"
                return False, f"Triggered negative pattern for: {category} ('{pattern}')"

    # 2. If it contains CBD or THC acronym, it MUST contain at least one explicit cannabis positive keyword
    has_cbd_acronym = re.search(r"\bcbd\b", combined)
    has_thc_acronym = re.search(r"\bthc\b", combined)
    
    if has_cbd_acronym or has_thc_acronym:
        has_positive = any(re.search(pattern, combined) for pattern in POSITIVE_KEYWORDS)
        if not has_positive:
            return False, "Contains 'CBD/THC' acronym but lacks any explicit cannabis context keywords."

    # 3. Check general relevance - if it doesn't contain CBD or THC, and has no cannabis terms, flag it
    has_any_positive = any(re.search(pattern, combined) for pattern in POSITIVE_KEYWORDS)
    if not has_any_positive and not has_cbd_acronym and not has_thc_acronym:
        return False, "Lacks any positive cannabis keywords or cannabinoid acronyms."

    return True, "Valid cannabis context."
