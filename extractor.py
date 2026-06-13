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

# Non-cannabinoid agents whose doses should be excluded from cannabis dose extraction
NON_CANNABINOID_AGENTS = {
    "leptin", "insulin", "dopamine", "serotonin", "norepinephrine", "noradrenaline",
    "epinephrine", "adrenaline", "glucose", "corticosterone", "cortisol", "ghrelin",
    "morphine", "nicotine", "cocaine", "amphetamine", "methamphetamine",
    "fentanyl", "heroin", "ketamine", "naloxone", "naltrexone", "buprenorphine",
    "chemerin", "adiponectin", "resistin", "lipopolysaccharide",
    "clozapine", "olanzapine", "haloperidol", "risperidone", "aripiprazole",
    "capsaicin", "progesterone", "estrogen", "testosterone", "melatonin",
    "caffeine", "ethanol", "alcohol", "saline", "vehicle", "cremophor",
    "dmso", "tween", "polyethylene glycol",
    "fluoxetine", "paroxetine", "sertraline", "escitalopram",
    "mecamylamine", "scopolamine", "atropine", "propranolol",
    "diazepam", "lorazepam", "midazolam",
    "l-dopa", "levodopa", "carbidopa",
    "metformin", "rosiglitazone", "pioglitazone",
    "lps", "tnf", "tnf-α", "il-1", "il-6", "infiksimabi",
    "losartan", "captopril", "enalapril",
}

# Cannabis/cannabinoid terms that indicate a dose is relevant
CANNABIS_AGENT_TERMS = {
    "cannabis", "cannabinoid", "cannabinoids", "thc", "cbd", "marijuana",
    "weed", "hashish", "hemp", "cannabidiol", "tetrahydrocannabinol",
    "delta-9-tetrahydrocannabinol", "delta-9", "cb1", "cb2",
    "endocannabinoid", "phytocannabinoid", "cannabigerol",
    "cannabichromene", "cannabinol", "cbn", "cbg", "cbc", "thcv", "cbdv",
    "dronabinol", "nabilone", "marinol", "cesamet",
    "sativex", "nabiximols", "epidiolex",
    "cannabis-based", "cannabinoid-based",
}

# Duration in days, weeks, months, years
DURATION_PATTERN = re.compile(
    r'(?i)(?:for|duration\s+of|period\s+of|treated\s+for|studied\s+for)\s+(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b'
)
DURATION_FALLBACK = re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\b')

# Inhaled exposure duration patterns
INHALED_DURATION_PATTERNS = [
    re.compile(r'(?i)(?:inhaled|inhalation|smok(?:ed|ing)|vap(?:ed|ing|orized|orised)|puff(?:ed|ing)?)\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|sec(?:ond)?s?|puffs?|inhalations?|h(?:ou)?rs?|breaths?)'),
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|sec(?:ond)?s?|puffs?|inhalations?|breaths?)\s+(?:of\s+)?(?:inhaled|inhalation|smok(?:ed|ing)|vap(?:ed|ing|orized|orised))'),
    re.compile(r'(?i)(?:for|during|over)\s+(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|sec(?:ond)?s?)\s+(?:of\s+)?(?:active|paced|controlled)\s+(?:inhalation|smoking|vaping|puff)'),
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*(puffs?)\b'),
]

# Cannabis administration terms vs. receptor-only mention
# Terms that indicate actual cannabis/cannabinoid product administration
_CANNABIS_ADMIN_TERMS = {
    "cannabis", "cannabinoid", "cannabinoids",
    "thc", "delta-9-tetrahydrocannabinol", "Δ9-tetrahydrocannabinol",
    "cbd", "cannabidiol", "marijuana", "hashish", "hemp", "weed",
    "cannabigerol", "cannabichromene", "cannabinol",
    "cbn", "cbg", "cbc", "thcv", "cbdv",
    "ganja", "bhang",
    "dronabinol", "nabilone", "marinol", "epidiolex", "sativex",
    "nabiximols", "cannabis-based", "cannabinoid-based",
    "phytocannabinoid",
}

# Terms that ONLY refer to cannabinoid receptor/mechanism studies (no administration)
_CANNABIS_RECEPTOR_ONLY_TERMS = {
    "cannabinoid receptor", "cannabinoid receptors",
    "cb1 receptor", "cb2 receptor",
    "cannabinoid receptor 1", "cannabinoid receptor 2",
    "endocannabinoid system", "endocannabinoid",
    "endogenous cannabinoid",
}


def _paper_has_cannabis_content(title: str, abstract: str) -> bool:
    """Check if the paper actually mentions cannabis/cannabinoid administration
    (not just cannabinoid receptors)."""
    text = (title + " " + (abstract or "")).lower()

    # Strip all receptor-only phrases so "cannabinoid" in
    # "cannabinoid receptor" doesn't count as administration mention
    for term in _CANNABIS_RECEPTOR_ONLY_TERMS:
        text = text.replace(term.lower(), "")

    # Also strip standalone "cb1" and "cb2" when not part of a product term
    text = re.sub(r'\bcb[12]\b', '', text)

    # Check for remaining cannabis administration/product terms
    for term in _CANNABIS_ADMIN_TERMS:
        if re.search(r'\b' + re.escape(term) + r'\b', text, re.IGNORECASE):
            return True

    return False


# Administration frequency patterns
FREQUENCY_PATTERNS = [
    re.compile(r'(?i)\b(once|twice|three\s+times|thrice|\d+)\s+(?:daily|per\s+day|a\s+day|each\s+day|weekly|per\s+week|a\s+week|each\s+week|monthly|per\s+month)\b'),
    re.compile(r'(?i)\b(?:administered|given|treat(?:ed|ment)?|dosed?|applied)\s+(once|twice|three\s+times|\d+\s*[-–]?\s*\d*\s*times?)\s+(?:daily|per\s+day|a\s+day|weekly|per\s+week)\b'),
    re.compile(r'(?i)\b(?:single|one[- ]time|acute)\s+(?:dose|administration|treatment|exposure)\b'),
    re.compile(r'(?i)\bdaily\b(?!\s*(?:life|activit|liv|habit|intake|consum|diet|record|log|assess|measur|score|questionnaire|survey|interview|monitor))'),
    re.compile(r'(?i)\btwice[- ]?daily\b'),
    re.compile(r'(?i)\b(\d+)\s*(?:days?|weeks?)\s*(?:per|a|each)\s*(?:week|month)\b'),
]

# Treatment duration patterns (for in vitro)
TREATMENT_DURATION_PATTERNS = [
    re.compile(r'(?i)(?:incubated|treated|exposed|stimulated|cultured|maintained)\s+(?:\w+\s+){0,4}?for\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\b'),
    re.compile(r'(?i)for\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\s+(?:at\s+\d+\s*°?C\s*)?(?:incubation|treatment|exposure|stimulation|culture)\b'),
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*(h|hrs?|minutes?|days?)\s+(?:treatment|incubation|exposure|culture|stimulation)\b'),
    re.compile(r'(?i)(?:for|during)\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\s+(?:of\s+)?(?:treatment|incubation|exposure|culture|stimulation)'),
    re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*h\b(?!\s*(?:z|ertz|z\s+frequency|z\s+stimulation))'),
]

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

def _nearest_substance_is_non_cannabinoid(pre_ctx: str, post_ctx: str) -> bool:
    """Check if the nearest named substance to the dose is a non-cannabinoid agent
    without any cannabis/cannabinoid term in the same context."""
    found_non_cannabinoid = False
    found_cannabis = False
    
    pre_words = re.findall(r'(\w+(?:[-\s]\w+)?)', pre_ctx)
    post_words = re.findall(r'(\w+(?:[-\s]\w+)?)', post_ctx)
    
    for phrase in pre_words + post_words:
        phrase_lower = phrase.lower().strip('.,;:()[]\'"')
        if phrase_lower in NON_CANNABINOID_AGENTS:
            found_non_cannabinoid = True
        if phrase_lower in CANNABIS_AGENT_TERMS:
            found_cannabis = True
    
    if found_cannabis:
        return False
    return found_non_cannabinoid


def extract_dose_mg(text: str) -> Optional[float]:
    """Extracts absolute dose in mg for cannabis/cannabinoid from text,
    filtering out doses associated with non-cannabinoid substances."""
    if not text:
        return None

    candidates = []
    for m in DOSE_MG_PATTERN.finditer(text):
        candidates.append((float(m.group(1)), m.start(), m.end()))
    if not candidates:
        for m in DOSE_MG_FALLBACK.finditer(text):
            pre = text[max(0, m.start()-5):m.end()+10].lower()
            if re.search(r'mg/(?:kg|ml|g|day)', pre):
                continue
            candidates.append((float(m.group(1)), m.start(), m.end()))

    for dose, start, end in candidates:
        pre_ctx = text[max(0, start-60):start].lower()
        post_ctx = text[end:min(len(text), end+60)].lower()

        of_match = re.search(r'\bof\s+(\w+)', post_ctx)
        if of_match:
            subj = of_match.group(1).lower().strip('.,;:()[]\'"')
            if subj in NON_CANNABINOID_AGENTS:
                continue
            if subj in CANNABIS_AGENT_TERMS:
                return dose

        if _nearest_substance_is_non_cannabinoid(pre_ctx, post_ctx):
            continue

        return dose

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

def extract_inhaled_exposure_duration(text: str) -> Optional[str]:
    """Extracts inhaled exposure duration (e.g. '10 minutes', '5 puffs') from text."""
    if not text:
        return None
    for pattern in INHALED_DURATION_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                val = groups[0]
                unit = groups[1].lower().rstrip('.')
                if unit.startswith("h"):
                    unit = "hours"
                elif unit.startswith("min"):
                    unit = "minutes"
                elif unit.startswith("sec"):
                    unit = "seconds"
                elif unit.startswith("puff"):
                    unit = "puffs"
                elif unit.startswith("inhalation"):
                    unit = "inhalations"
                elif unit.startswith("breath"):
                    unit = "breaths"
                val_f = float(val)
                if val_f.is_integer():
                    val_f = int(val_f)
                return f"{val_f} {unit}"
    return None


def extract_administration_frequency(text: str) -> Optional[str]:
    """Extracts administration frequency (e.g. 'once daily', 'single dose') from text."""
    if not text:
        return None
    for pattern in FREQUENCY_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group(0).strip()
            raw_lower = raw.lower()
            if raw_lower == "daily":
                return "once daily"
            return raw
    return None


def extract_treatment_duration(text: str) -> Optional[str]:
    """Extracts in vitro treatment duration (e.g. '24 hours', '30 minutes') from text."""
    if not text:
        return None
    for i, pattern in enumerate(TREATMENT_DURATION_PATTERNS):
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) >= 2:
                val = groups[0]
                unit_raw = groups[1].lower().rstrip('.')
                if unit_raw.startswith("d"):
                    if 1900 <= float(val) <= 2030:
                        continue
                if unit_raw.startswith("h"):
                    unit = "hours"
                    if float(val) == 1:
                        unit = "hour"
                elif unit_raw.startswith("min"):
                    unit = "minutes"
                elif unit_raw.startswith("d"):
                    unit = "days"
                    if float(val) == 1:
                        unit = "day"
                else:
                    unit = unit_raw
                val_f = float(val)
                if val_f.is_integer():
                    val_f = int(val_f)
                return f"{val_f} {unit}"
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
    # Check if not cannabis-related
    is_related, reason = is_cannabis_related(title, abstract)
    if not is_related:
        return "not cannabis-related"
        
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
        types.append("Animal Models (Mouse)")
    # rat
    if keyword_match(combined, ["rat", "rats", "wistar", "sprague-dawley"]):
        types.append("Animal Models (Rat)")
    # other rodents
    if keyword_match(combined, ["hamster", "hamsters", "gerbil", "gerbils", "guinea pig", "guinea pigs", "voles", "vole"]):
        types.append("Animal Models (Other Rodents)")
    # non-human primate
    if keyword_match(combined, ["macaque", "rhesus", "monkey", "monkeys", "primate", "primates", "baboon", "chimpanzee"]):
        types.append("Animal Models (Non-Human Primates)")
    # other animal
    if keyword_match(combined, ["dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish", "drosophila"]):
        types.append("Animal Models (Other)")
    elif keyword_match(combined, ["animal", "in vivo", "animal model", "rodent", "rodents"]):
        if not any(t.startswith("Animal Models (") for t in types):
            types.append("Animal Models (Other)")
            
    # 3. Cell Culture
    # primary cells
    if keyword_match(combined, ["primary cell", "primary cells", "primary culture", "primary neuronal", "primary microglia", "splenocytes", "primary hepatocytes"]):
        types.append("Cell Culture (Primary Cells)")
    # cell lines
    if keyword_match(combined, ["cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7", "sh-sy5y", "jurkat", "cho cells"]):
        types.append("Cell Culture (Cell Lines)")
    # organoids
    if keyword_match(combined, ["organoid", "organoids", "spheroid", "spheroids", "3d culture", "3d cultures"]):
        types.append("Cell Culture (Organoids)")
    # co-culture
    if keyword_match(combined, ["co-culture", "co-cultures", "coculture", "cocultures"]):
        types.append("Cell Culture (Co-Culture)")
    # PCLS (precision-cut lung slices)
    if keyword_match(combined, ["precision-cut lung slices", "pcls", "precision cut lung slices", "lung slice", "lung slices"]):
        types.append("Cell Culture (PCLS)")
    elif keyword_match(combined, ["in vitro", "cultured cells", "culture assay", "cell culture", "cell cultures", "epithelial cells", "epithelial cell", "airway epithelial"]):
        if not any(t.startswith("Cell Culture (") for t in types):
            types.append("Cell Culture (Other In Vitro)")
            
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
            "cell culture", "cell cultures", "pcls", "precision-cut", "lung slice", "lung slices"
        ]
        
        has_animal_title = any(k in title_lower for k in animal_keywords)
        has_cell_title = any(k in title_lower for k in cell_keywords)
        
        if not has_animal_title:
            types = [t for t in types if not t.startswith("Animal Models (")]
        if not has_cell_title:
            types = [t for t in types if not t.startswith("Cell Culture (")]
            
    return list(set(types))

def infer_exposure_method(title: str, abstract: str, study_type: Any) -> List[str]:
    """Extracts exposure method from text keywords, focusing on Methods section if available."""
    if not _paper_has_cannabis_content(title, abstract):
        return ["unknown"]

    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    
    # Normalize inputs to sets/lists
    if isinstance(study_type, str):
        study_types = {study_type}
    else:
        study_types = set(study_type or [])
        
    methods = []
    
    # Group A: Clinical exposure (human/clinical setting)
    if study_types.intersection({"RCT", "observational"}) or any(s.startswith("Clinical (") for s in study_types):
        if keyword_match(combined, ["smoke", "smoked", "smoking", "joint", "combustion", "cigarette", "cigarettes", "vaporized", "vaporised", "vape", "vaping", "vaporizer", "vaporisation", "inhaled", "inhalation"]):
            methods.append("inhaled")
        if keyword_match(combined, ["oral", "edible", "ingested", "capsule", "gummy", "cookies", "oil ingestion", "brownie", "gavage"]):
            methods.append("oral")
        if keyword_match(combined, ["sublingual", "under the tongue", "drops", "tincture", "tinctures"]):
            methods.append("sublingual")
        if keyword_match(combined, ["injection", "intravenous", "iv", "intraperitoneal", "ip", "subcutaneous", "intramuscular", "injected"]):
            methods.append("injected")
 
    # Group B: In vitro exposure (cells/tissue setting)
    if "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types):
        if keyword_match(combined, ["conditioned media", "smoke extract", "cse", "vapor extract", "gaseous extract", "smoke-conditioned"]):
            methods.append("smoke/vapor conditioned media")
        if keyword_match(combined, ["cell exposure to smoke", "cells exposed to vapor", "chamber exposure of cells", "smoke stream", "direct vapor exposure", "exposure of cells to smoke", "exposure of cells to vapor", "air-liquid interface", "air liquid interface", "ali exposure", "ali", "aerosol exposure", "exposed directly to vapor", "exposed directly to smoke"]):
            methods.append("exposure of cells to smoke/vapor")
 
    # Group C: In vivo exposure (animal models)
    if "animal" in study_types or any(s.startswith("Animal Models (") for s in study_types):
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
        if "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types):
            methods.append("cannabinoids dissolved in media")
        elif study_types.intersection({"RCT", "observational"}) or any(s.startswith("Clinical (") for s in study_types):
            methods.append("inhaled")
        else:
            methods.append("injection cannabinoids")
            
    return list(set(methods))
    
def infer_cannabis_type(title: str, abstract: str, study_type: Any, exposure_method: Any) -> List[str]:
    """Infers the type of cannabis product being administered or studied, focusing on Methods section if available."""
    if not _paper_has_cannabis_content(title, abstract):
        return ["unknown"]

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
 
def get_intro_objective_text(title: str, abstract: str) -> str:
    """Extracts Title, Introduction/Background, and Objectives/Aims sections,
    discarding Methods, Results, Discussion, Conclusions, and other sections.
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
        words = abstract.split()
        first_part = " ".join(words[:100]) if len(words) > 100 else abstract
        return title + "\n\n" + first_part

    intro_objective_headers = {
        'background', 'introduction', 'objective', 'objectives', 'aim', 'aims'
    }

    allowed_parts = []

    first_start = matches[0].start()
    pre_text = abstract[:first_start].strip()
    if pre_text:
        allowed_parts.append(pre_text)

    for i, match in enumerate(matches):
        header_name = match.group(1).lower()
        start_idx = match.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(abstract)
        content = abstract[start_idx:end_idx].strip()
        if header_name in intro_objective_headers:
            allowed_parts.append(match.group(0) + " " + content)

    combined_abstract = "\n\n".join(allowed_parts)
    return title + "\n\n" + combined_abstract


def extract_outcomes(title: str, abstract: str) -> List[str]:
    """Identifies multi-label outcome domains from the introduction/objective sections only."""
    combined = get_intro_objective_text(title, abstract).lower()
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
    
    # Vowel prefix check
    study_word = study_desc.lower()
    if study_word.startswith(('a', 'e', 'i', 'o', 'u')) or "rct" in study_word or "observational" in study_word:
        prefix = "an"
    else:
        prefix = "a"
        
    summary = f"This is {prefix} {study_desc} study investigating {cannabis_desc} cannabis administration via {exposure_desc}."
    
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
    exposure_method = infer_exposure_method(title, abstract, study_type)
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
        
    sample_size = extract_sample_size(abstract)
    outcome_domain = extract_outcomes(title, abstract)
    cannabis_type = infer_cannabis_type(title, abstract, study_type, exposure_method)

    combined_text = title + " " + (abstract or "")

    study_set = set(study_type) if isinstance(study_type, list) else {study_type} if isinstance(study_type, str) else set()

    is_clinical = any(s.startswith("Clinical (") for s in study_set)
    is_invivo = any(s.startswith("Animal Models (") for s in study_set)
    is_invitro = any(s.startswith("Cell Culture (") for s in study_set)

    if is_clinical or is_invivo:
        duration_days = extract_duration_days(abstract)
        if duration_days is None:
            duration_days = extract_duration_days(title)
        exposure_list = exposure_method if isinstance(exposure_method, list) else [exposure_method]
        is_inhaled = any("inhaled" in e or "smok" in e or "vapor" in e or "nose" in e or "whole body" in e for e in exposure_list)
        inhaled_exposure_duration = extract_inhaled_exposure_duration(combined_text) if is_inhaled else None
        administration_frequency = extract_administration_frequency(combined_text)
    else:
        duration_days = None
        inhaled_exposure_duration = None
        administration_frequency = None

    if is_invitro:
        treatment_duration = extract_treatment_duration(combined_text)
    else:
        treatment_duration = None

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
        "thc_uM": None,
        "cbd_uM": None,
        "strain_reported": strain_reported,
        "strain_normalized": strain_normalized,
        "duration_days": duration_days,
        "inhaled_exposure_duration": inhaled_exposure_duration,
        "administration_frequency": administration_frequency,
        "treatment_duration": treatment_duration,
        "sample_size": sample_size,
        "outcome_domain": outcome_domain,
        "multiple_doses": multiple_doses,
        "multiple_time_intervals": multiple_time_intervals,
        "cannabis_type": cannabis_type,
        "publication_type": publication_type
    }
    result["summary"] = generate_heuristic_summary(result)
    return result

# --- Cannabis/Cannabinoid Positive & Negative Context Keywords for Relevance Checking ---

POSITIVE_KEYWORDS = [
    r"\b(?:endo|phyto)?cannabi\w*",
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

def is_gpr_lpi_dud(title: str, abstract: str) -> tuple[bool, str]:
    """Helper to detect if a paper is an orphan GPR receptor/LPI study that is not cannabis-related.
    These papers mention cannabinoids (THC, AEA, CBD) in the first 1-2 sentences of the abstract
    for background context, but the actual study is about GPR55/LPI or GPR18 or GPR119 and doesn't
    test or administer any cannabinoid.
    """
    title_lower = title.lower()
    abstract_lower = (abstract or "").lower()
    combined = title_lower + " " + abstract_lower
    
    # 1. Check if the paper deals with GPR55, LPI, GPR18, or GPR119
    has_gpr = any(term in combined for term in ["gpr55", "gpr-55", "gpr18", "gpr-18", "gpr119", "gpr-119", "g-protein coupled receptor 55", "g protein-coupled receptor 55", "lysophosphatidylinositol"])
    if not has_gpr:
        return False, ""
        
    # 2. Check if any positive keywords (or cbd/thc/cb1/cb2/cnr/endocannabinoid) are in the title.
    title_has_positive = any(re.search(pattern, title_lower) for pattern in POSITIVE_KEYWORDS) or any(term in title_lower for term in ["cbd", "thc", "cb1", "cb2", "cnr1", "cnr2", "cnr 1", "cnr 2", "endocannabinoid", "endocannabinoids"])
    if title_has_positive:
        return False, ""
        
    # 3. Check if it contains the canonical CB1/CB2/CNR1/CNR2 receptors in the abstract/title.
    has_canonical_cb = any(re.search(rf"\b{term}\b", combined) for term in ["cb1", "cb2", "cnr1", "cnr2", "cnr 1", "cnr 2", "cannabinoid receptor 1", "cannabinoid receptor 2", "cannabinoid receptor type 1", "cannabinoid receptor type 2"])
    if has_canonical_cb:
        return False, ""
        
    # 4. Split abstract into sentences
    sentences = [s.strip() for s in re.split(r'\.\s+', abstract_lower) if s.strip()]
    if not sentences:
        return False, ""
        
    # Specific cannabinoid substances we want to track
    cannabinoid_substances = [
        "thc", "cbd", "cannabidiol", "tetrahydrocannabinol", "anandamide", "aea", 
        "2-ag", "2-arachidonoylglycerol", "win 55,212-2", "win55,212-2", 
        "cp 55,940", "cp55,940", "rimonabant", "am251", "am630", "sr141716", "epidiolex", "sativex"
    ]
    
    # Find all sentence indices containing cannabinoid substances
    cannabinoid_sentence_indices = []
    for idx, s in enumerate(sentences):
        if any(re.search(rf"\b{term}\b", s) for term in cannabinoid_substances):
            cannabinoid_sentence_indices.append(idx)
            
    if cannabinoid_sentence_indices:
        all_in_background = all(idx <= 1 for idx in cannabinoid_sentence_indices)
        if all_in_background:
            remaining_text = " ".join(sentences[2:])
            has_gpr_lpi_focus = any(term in remaining_text for term in ["gpr55", "gpr-55", "gpr18", "gpr-18", "gpr119", "gpr-119", "lpi", "lysophosphatidyl", "nagly", "oleoylethanolamide"])
            if has_gpr_lpi_focus:
                return True, "Paper is a GPR/LPI/orphan receptor study; cannabinoids are only mentioned in background context (first 2 sentences of abstract)."
    else:
        # No specific cannabinoid substance mentioned. Check general terms.
        has_general_terms = any(re.search(rf"\b{term}\b", combined) for term in ["cannabinoid", "cannabinoids", "endocannabinoid", "endocannabinoids", "marijuana", "hemp"])
        if has_general_terms:
            # Let's see if the general terms only appear in background (first 2 sentences)
            general_sentence_indices = []
            for idx, s in enumerate(sentences):
                if any(re.search(rf"\b{term}\b", s) for term in ["cannabinoid", "cannabinoids", "endocannabinoid", "endocannabinoids", "marijuana", "hemp"]):
                    general_sentence_indices.append(idx)
            
            # If they only appear in the first 2 sentences, it is a dud
            if general_sentence_indices and all(idx <= 1 for idx in general_sentence_indices):
                return True, "GPR/LPI study; general cannabinoid terms only mentioned in background context (first 2 sentences of abstract)."
            
            # Let's also check if "endocannabinoid system" or "endocannabinoids" is mentioned in a characterization way but no cannabinoid is tested
            passive_mentions_only = True
            for idx in general_sentence_indices:
                if idx > 1:
                    sentence_text = sentences[idx]
                    is_passive = any(w in sentence_text for w in ["did not affect", "no changes", "levels", "expression", "concentrations", "synthesis", "content", "system"])
                    is_active = any(w in sentence_text for w in ["treated", "administered", "administration", "agonist", "antagonist", "blocking", "block", "effect of"])
                    if is_active and not is_passive:
                        passive_mentions_only = False
                        break
            if passive_mentions_only:
                return True, "GPR/LPI study; general cannabinoid terms are only mentioned passively or in the background, no active cannabinoid testing."
        else:
            return True, "GPR/LPI study with no cannabinoid substances or general cannabinoid terms."
            
    return False, ""

def is_cannabis_related(title: str, abstract: str) -> tuple[bool, str]:
    """Analyzes a paper's text to determine if it is cannabis-related.
    
    Returns:
        tuple: (is_relevant: bool, reason: str)
    """
    # 0. Check for GPR55 / LPI / GPR18 / GPR119 duds
    is_dud, reason = is_gpr_lpi_dud(title, abstract)
    if is_dud:
        return False, reason

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
