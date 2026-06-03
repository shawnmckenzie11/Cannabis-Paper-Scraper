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
    """Extracts the Methods section from the abstract if present.
    
    Falls back to combining the title and the abstract if no clear Methods section is found.
    """
    if not abstract:
        return title
    # Regex to find Methods section: case insensitive, handles METHODS, METHODOLOGY, METHOD, PATIENTS AND METHODS, MATERIALS AND METHODS
    # Usually followed by a colon or a newline, up to the next section like Results, Discussion, etc.
    pattern = r'\b(?:methods|methodology|method|patients and methods|materials and methods)\s*:\s*(.*?)(?=\b(?:results|conclusions|discussion|background|aims|objectives)\b\s*:|$)'
    match = re.search(pattern, abstract, re.IGNORECASE | re.DOTALL)
    if match:
        methods_content = match.group(1).strip()
        # Include title in the context as it contains key study design/population clues
        return title + "\n\n" + methods_content
    return title + "\n\n" + abstract

def infer_study_type(title: str, abstract: str) -> str:
    """Infers study type from text keywords, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    if keyword_match(combined, ["meta-analysis", "pooled analysis", "systematic overview"]):
        return "meta-analysis"
    if keyword_match(combined, ["systematic review", "literature review", "overview of reviews"]) or ("review" in title.lower() and "review" in combined):
        return "review"
    if keyword_match(combined, ["case study", "case studies", "case report", "case reports", "case series", "clinical case", "case-report", "case-series"]):
        return "case study"
    if keyword_match(combined, ["editorial", "commentary", "opinion", "perspective", "editorials", "commentaries", "perspectives", "viewpoint", "letter to the editor", "letters to the editor"]):
        return "editorial"
    if keyword_match(combined, ["double-blind", "randomized controlled", "placebo-controlled", "rct", "randomised controlled"]):
        return "RCT"
    if keyword_match(combined, ["observational", "cohort", "case-control", "cross-sectional", "longitudinal", "survey", "registry"]):
        return "observational"
    if keyword_match(combined, ["mouse", "mice", "rat", "rats", "rodent", "in vivo", "animal model", "murine"]):
        return "animal"
    if keyword_match(combined, ["in vitro", "cell line", "cultured cells", "culture assay", "microglia", "neurons", "epithelial cells", "epithelial cell", "airway epithelial", "cell culture", "cell cultures", "co-culture", "primary cells", "primary cell", "organoid", "organoids", "spheroids"]):
        return "in vitro"
        
    return "observational"
    
def infer_exposure_method(title: str, abstract: str, study_type: str, population: Optional[str] = None) -> str:
    """Extracts exposure method from text keywords, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    if not population:
        population = infer_population(title, abstract, study_type)
        
    # Group A: Clinical exposure (human/clinical setting)
    if population == "human" or study_type in ("RCT", "observational"):
        if keyword_match(combined, ["smoke", "smoked", "smoking", "joint", "combustion", "cigarette", "cigarettes", "vaporized", "vaporised", "vape", "vaping", "vaporizer", "vaporisation", "inhaled", "inhalation"]):
            return "inhaled"
        if keyword_match(combined, ["oral", "edible", "ingested", "capsule", "gummy", "cookies", "oil ingestion", "brownie", "gavage"]):
            return "oral"
        if keyword_match(combined, ["sublingual", "under the tongue", "drops", "tincture", "tinctures"]):
            return "sublingual"
        if keyword_match(combined, ["injection", "intravenous", "iv", "intraperitoneal", "ip", "subcutaneous", "intramuscular", "injected"]):
            return "injected"
            
        # Fallbacks for clinical
        return "inhaled" # Default for human clinical studies is usually inhaled administration

    # Group B: In vitro exposure (cells/tissue setting)
    elif population == "cell_line" or study_type == "in vitro":
        if keyword_match(combined, ["conditioned media", "smoke extract", "cse", "vapor extract", "gaseous extract", "smoke-conditioned"]):
            return "smoke/vapor conditioned media"
        if keyword_match(combined, ["cell exposure to smoke", "cells exposed to vapor", "chamber exposure of cells", "smoke stream", "direct vapor exposure", "exposure of cells to smoke", "exposure of cells to vapor", "air-liquid interface", "air liquid interface", "ali exposure", "ali", "aerosol exposure", "exposed directly to vapor", "exposed directly to smoke"]):
            return "exposure of cells to smoke/vapor"
            
        # Default for in vitro since most cell assays dissolve cannabinoids in media
        return "cannabinoids dissolved in media"

    # Group C: In vivo exposure (animal models)
    else:
        if keyword_match(combined, ["nose-only", "nose only", "snout exposure", "head-out"]):
            return "nose only smoke/vapor"
        if keyword_match(combined, ["whole body", "whole-body", "chamber exposure", "whole body smoke", "whole body vapor"]):
            return "whole body. smoke/vapor"
        if keyword_match(combined, ["injection", "injected", "intravenous", "intraperitoneal", "ip", "iv", "subcutaneous", "sc", "intramuscular", "im"]):
            return "injection cannabinoids"
        if keyword_match(combined, ["gavage", "oral administration", "fed", "diet", "oral gavage", "ingested", "oral"]):
            return "oral administration"
        if keyword_match(combined, ["sublingual", "sub-lingual", "under tongue"]):
            return "sub-lingual"
        if keyword_match(combined, ["intranasal", "intra-nasal", "nasal instillation", "nasal drops"]):
            return "intranasal"
        if keyword_match(combined, ["intratracheal", "intratracheal instillation", "intra-tracheal", "lung instillation"]):
            return "intratracheal"
            
        # Fallback mappings for in vivo animal studies
        return "injection cannabinoids" # Standard default for animal models

    return "unknown"

def infer_cannabis_type(title: str, abstract: str, study_type: str, exposure_method: str) -> str:
    """Infers the type of cannabis product being administered or studied, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # 1. Edibles check
    if keyword_match(combined, ["edible", "edibles", "gummy", "gummies", "chocolate", "chocolates", "drink", "drinks", "beverage", "beverages", "brownie", "brownies", "cookies", "cookie", "capsule", "capsules"]):
        return "edibles"
    # 2. Vape pen check
    if keyword_match(combined, ["vape pen", "cartridge", "cartridges", "e-cigarette", "vape cartridge", "distillate vape"]):
        return "vape pen"
    # 3. Hashish / Kief check
    if keyword_match(combined, ["hashish", "hash", "kief", "charas", "bubble hash"]):
        return "hashish/kief"
    # 4. Pure Cannabinoid check
    if keyword_match(combined, ["pure thc", "pure cbd", "synthetic cannabinoid", "synthetic cannabinoids", "dronabinol", "nabilone", "marinol", "isolate", "isolates", "pure cannabinoid", "pure cannabinoids", "cannabidiol isolate"]):
        return "pure cannabinoid"
    # 5. Concentrates check
    if keyword_match(combined, ["shatter", "tincture", "tinctures", "resin", "concentrate", "concentrates", "extract", "extracts", "hash oil", "honey oil", "bho", "rosin", "wax"]):
        return "concentrates"
    # 6. Dried flower check
    if keyword_match(combined, ["flower", "bud", "buds", "dried cannabis", "joint", "joints", "combusted flower", "cannabis herb", "herbal cannabis", "marijuana cigarette", "marijuana cigarettes", "cigarette", "cigarettes"]):
        return "dried flower"
        
    # Fallback mappings based on exposure method
    if exposure_method in ("smoked", "inhaled", "whole body. smoke/vapor", "nose only smoke/vapor"):
        return "dried flower"
    elif exposure_method in ("vaporized", "vape pen"):
        return "vape pen"
    elif exposure_method in ("oral/edible", "oral", "oral administration"):
        return "edibles"
    elif exposure_method in ("tincture", "sublingual", "sub-lingual"):
        return "concentrates"
        
    return "unknown"

def infer_population(title: str, abstract: str, study_type: str) -> str:
    """Extracts population (human, mouse, rat, cell_line, other) from text, focusing on Methods section if available."""
    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    if study_type == "in vitro" or keyword_match(combined, ["cell line", "hela", "hepg2", "cells", "culture"]):
        return "cell_line"
    if keyword_match(combined, ["mouse", "mice", "murine", "c57bl"]):
        return "mouse"
    if keyword_match(combined, ["rat", "rats", "wistar", "sprague"]):
        return "rat"
    if keyword_match(combined, ["patient", "subject", "participant", "volunteer", "man", "woman", "human", "clinical", "adult", "individuals"]):
        return "human"
    if keyword_match(combined, ["dog", "pig", "monkey", "rabbit", "feline", "canine"]):
        return "other"
        
    if study_type in ("RCT", "observational"):
        return "human"
    if study_type == "animal":
        return "mouse"
        
    return "other"

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

def determine_quality_flags(
    title: str,
    abstract: str,
    study_type: str,
    exposure_method: str,
    population: str,
    thc_pct: Optional[float],
    dose_mg: Optional[float],
    strain_reported: Optional[str],
    strain_normalized: Optional[str]
) -> List[str]:
    """Generates quality flags based on the extracted metadata and paper text."""
    combined = (title + " " + abstract).lower()
    flags = []
    
    # 1. No strain specified
    if not strain_reported:
        flags.append("no_strain_specified")
        
    # 2. Self-report only
    if any(k in combined for k in ["self-report", "questionnaire", "survey", "diary", "subjective rating"]):
        flags.append("self_report_only")
        
    # 3. THC not quantified
    # Triggered if it's a cannabis/THC study but neither thc_pct nor dose_mg is known
    # (We can assume it's cannabis/THC study since we are scraping cannabis, so if no THC% and no dose_mg, flag it)
    if thc_pct is None and dose_mg is None:
        flags.append("THC_not_quantified")
        
    # 4. No control group
    if study_type in ("RCT", "observational", "animal"):
        if any(k in combined for k in ["no control", "uncontrolled", "without a control", "before-after study", "observational case series"]):
            flags.append("no_control_group")
        elif not any(k in combined for k in ["control group", "placebo", "sham", "controlled by", "vs placebo", "vs control"]):
            # If none of the classic control group keywords are present, flag it
            flags.append("no_control_group")
            
    # 5. Animal model only
    if population in ("mouse", "rat", "other"):
        flags.append("animal_model_only")
        
    # 6. Label not verified
    # Triggered if a strain is reported but we couldn't normalize it, OR text mentions unverified labels
    if strain_reported and not strain_normalized:
        flags.append("label_not_verified")
    elif "unverified" in combined or "label accuracy" in combined:
        flags.append("label_not_verified")
        
    return flags

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
    study_type = data.get("study_type") or "unspecified"
    cannabis_type = data.get("cannabis_type") or "unspecified"
    exposure_method = data.get("exposure_method") or "unspecified"
    population = data.get("population") or "unspecified"
    strain_reported = data.get("strain_reported")
    
    # Map population nicely
    pop_str = population
    if population == "human":
        pop_str = "human"
    elif population == "cell_line":
        pop_str = "cell line"
    elif population in ("mouse", "rat"):
        pop_str = population
        
    # Vowel prefix check
    study_word = study_type.lower()
    if study_word.startswith(('a', 'e', 'i', 'o', 'u')) or study_type in ("RCT", "observational"):
        prefix = "an"
    else:
        prefix = "a"
        
    summary = f"This is {prefix} {study_type} study investigating {cannabis_type} cannabis administration via {exposure_method} in {pop_str} models."
    
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
    study_type = infer_study_type(title, abstract)
    population = infer_population(title, abstract, study_type)
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
    
    flags = determine_quality_flags(
        title=title,
        abstract=abstract,
        study_type=study_type,
        exposure_method=exposure_method,
        population=population,
        thc_pct=thc_pct,
        dose_mg=dose_mg,
        strain_reported=strain_reported,
        strain_normalized=strain_normalized
    )
    
    multiple_doses = detect_multiple_doses(title, abstract)
    multiple_time_intervals = detect_multiple_time_intervals(title, abstract)
    
    result = {
        "study_type": study_type,
        "exposure_method": exposure_method,
        "thc_pct": thc_pct,
        "cbd_pct": cbd_pct,
        "dose_mg": dose_mg,
        "strain_reported": strain_reported,
        "strain_normalized": strain_normalized,
        "duration_days": duration_days,
        "population": population,
        "sample_size": sample_size,
        "outcome_domain": outcome_domain,
        "methodological_quality_flags": flags,
        "multiple_doses": multiple_doses,
        "multiple_time_intervals": multiple_time_intervals,
        "cannabis_type": cannabis_type
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
