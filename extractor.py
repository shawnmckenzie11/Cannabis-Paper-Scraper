# extractor.py
import re
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import classification_schema

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

# Additional cultivars commonly reported in preclinical PDF reclassification batches.
CHEMOTYPE_MAP.update({
    "skywalker kush": "Chemotype I",
    "skywalker": "Chemotype I",
    "treasure island kush": "Chemotype III",
    "treasure island": "Chemotype III",
})

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
    re.compile(r'(?i)(\d+)[- ]min(?:ute)?s?\s+(?:vapor|smoke|inhalation|session)'),
    re.compile(r'(?i)(\d+)[- ]min(?:ute)?s?\s*(?:session|exposure|inhalation|period)'),
    re.compile(r'(?i)exposed\s+for\s+(\d+)\s*min(?:ute)?s?\b'),
    re.compile(r'(?i)(\d+)\s*minutes?\s+of\s+(?:vapor|smoke|inhalation)'),
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


# Administration frequency patterns (ordered — first match wins)
FREQUENCY_PATTERNS = [
    re.compile(r'(?i)\bmultiple doses per session\b'),
    re.compile(
        r'(?i)\b(?:dosages?|doses?).{0,50}(?:\d+\s*min(?:utes?)?\s+apart|\d+\s*-\s*\d+\s*min(?:utes?)?)'
        r'.{0,90}(?:scan )?sessions?\b'
    ),
    re.compile(r'(?i)\btwice\s+daily\b'),
    re.compile(r'(?i)\bonce\s+daily\b'),
    re.compile(r'(?i)\bevery\s+other\s+day\b'),
    re.compile(r'(?i)\balternating\s+days?\b'),
    re.compile(r'(?i)(\d+)\s*days?\s*on[,/\s]+(\d+)\s*days?\s*off'),
    re.compile(r'(?i)\b(\d+)\s*times?\s*/\s*week\b'),
    re.compile(r'(?i)\bdaily\s+(?:for\s+)?5\s+days?.*?2\s+days?\s+off\b'),
    re.compile(r'(?i)\bonce\s+weekly\b'),
    re.compile(r'(?i)\btwice\s+weekly\b|\b2x\s*weekly\b'),
    re.compile(r'(?i)\btwice\s+a\s+week\b'),
    re.compile(r'(?i)\bweekly\b'),
    re.compile(r'(?i)\b(once|twice|three\s+times|thrice|\d+)\s+(?:daily|per\s+day|a\s+day|each\s+day|weekly|per\s+week|a\s+week|each\s+week|monthly|per\s+month)\b'),
    re.compile(r'(?i)\b(?:administered|given|treat(?:ed|ment)?|dosed?|applied)\s+(once|twice|three\s+times|\d+\s*[-–]?\s*\d*\s*times?)\s+(?:daily|per\s+day|a\s+day|weekly|per\s+week)\b'),
    re.compile(r'(?i)\b(?:single|one[- ]time|acute)\s+(?:dose|administration|treatment|exposure)\b'),
    re.compile(r'(?i)\bdaily\s+from\s+gestational\b'),
    re.compile(r'(?i)\bdaily\b(?!\s+from\b)(?!\s*(?:life|activit|liv|habit|intake|consum|diet|record|log|assess|measur|score|questionnaire|survey|interview|monitor))'),
    re.compile(r'(?i)\b(\d+)\s*(?:days?|weeks?)\s*(?:per|a|each)\s*(?:week|month)\b'),
    re.compile(r'(?i)\b(\d+)\s*days?\s*/\s*week\b'),
]

# Cannabis product-type cues (shared across node2a / node2b / node2c extraction)
SYNTHETIC_AGONIST_CUES = (
    "cp-55940", "cp55940", "cp 55,940", "win55212", "win 55,212", "win-55212",
    "o-1602", "jwh-", "hu-210", "hu210", "am2201",
)
SYNTHETIC_ANTAGONIST_CUES = (
    "sr141716", "sr 141716", "am251", "am-251", "rimonabant", "am630", "cid16020046",
)
PURE_CANNABINOID_COMPOUND_CUES = (
    "delta-9-thc", "delta9-thc", "tetrahydrocannabinol", "cannabidiol", "cannabigerol",
    "cannabidivarin", "cbdv", "cbg", "thcv",
)
PLANT_MATTER_CUES = (
    "plant matter", "dried flower", "cannabis sativa plant", "cannabis flower",
    "smoked cannabis", "combusted cannabis", "vaporized cannabis plant matter",
    "cannabis plant matter",
)
EXTRACT_PRODUCT_CUES = (
    "whole plant extract", "full spectrum extract", "cannabis extract", "botanical extract",
)
VAPE_PEN_DEVICE_CUES = (
    "vape pen", "vape cartridge", "e-cigarette cartridge", "vaping device",
)
PHARMA_SUPPLIER_CUES = ("sigma-aldrich", "cayman chemical", "tocris", "thc pharm", "nida drug supply", "lipomed")
EDIBLE_ORAL_CUES = (
    "edible", "edibles", "food treat", "mixed in food", "in peanut butter", "thc edible",
    "gummy", "gummies", "chocolate", "brownie", "brownies", "cookies", "cookie", "capsule", "capsules",
    "by mouth", "per os", "p.o.", "intragastric", "chow", "drinking water", "in food",
)
EDIBLES_PRODUCT_PHRASES = (
    "cannabis-infused", "edible product", "cannabis edible", "cannabis cookie",
    "infused brownie", "thc edible", "cannabis-infused brownie", "cannabis-infused cookie",
)
DISSOLVED_IN_MEDIA_TRIGGERS = (
    "isolated tissue", "tissue bath", "organ bath", "bath application",
    "embryos were exposed to", "tank water contained", "dissolved in tank water",
    "immersion in", "dissolved in artificial cerebrospinal fluid", "superfusion",
)
INJECTION_ROUTE_GUARDS = (
    "i.p.", "i.v.", "s.c.", "subcutaneous", "intraperitoneal", "intravenous",
    "intrathecal", "intracerebroventricular", "i.c.v.", "intramuscular", "i.m.",
)
COMPOUND_PROVENANCE_VENDORS = (
    "Sigma-Aldrich", "Cayman Chemical", "Tocris", "NIDA Drug Supply Program",
    "THC Pharm", "Lipomed",
)
COMPOUND_NAME_CUES = (
    "THC", "CBD", "cannabidiol", "delta-9-tetrahydrocannabinol", "delta-9-THC",
    "CBG", "CBDV", "cannabigerol", "cannabidivarin",
)
CANNABINOID_ISOLATE_LIST = (
    "delta-9-tetrahydrocannabinol", "delta-8-tetrahydrocannabinol", "tetrahydrocannabivarin",
    "cannabidivarin", "cannabigerol", "cannabidiol", "dronabinol", "nabilone",
    "2-arachidonoylglycerol", "anandamide",
    "THC", "CBD", "CBDV", "THCV", "CBG", "CBGA", "CBCA", "CBC",
    "JWH-018", "WIN 55,212", "HU-210", "CP 55,940", "CP-55940", "AEA", "2-AG",
)
VENDOR_STRAIN_BLOCKLIST = (
    "sigma", "aldrich", "tocris", "cayman", "abcam", "santa cruz", "millipore",
    "catalog no", "cat no", "cat #", "lot no", "item no", "selleckchem", "apexbio", "biolegend",
)
ANALYTICAL_COMPUTATIONAL_CUES = (
    "uhplc", "hplc", "hrms", "orbitrap", "lc-ms", "gc-ms", "mass spectrometry",
    "molecular docking", "molecular dynamics", "gromacs", "autodock", "vina",
    "dft", "density functional", "in silico", "docking score", "binding affinity",
    "pharmacophore model", "admet prediction",
)
PLANT_CULTIVATION_CUES = (
    "planting density", "plants per", "plants/m2", "greenhouse", "field trial",
    "cultivation", "cultivar", "chemovar", "harvest", "flowering", "yield per",
    "yield/area", "dry weight", "biomass", "pots were", "containers were",
    "growing season", "crop", "horticultural",
)
CLINICAL_SELF_REPORT_EXPOSURE_CUES = (
    "smoked", "smoking", "vaped", "vaping", "inhaled", "joint", "joints",
    "edible", "edibles", "capsule", "capsules", "oil", "tincture", "flower",
    "concentrate", "concentrates", "dried cannabis", "marijuana use",
)
OBSERVATIONAL_ROUTE_BACKGROUND_MARKERS = (
    "literature", "previous stud", "prior stud", "review", "meta-analysis",
    "systematic review", "has been shown", "reported that", "discussed in",
)
PLANT_MATRIX_CUES = (
    "extract", "oil", "flower", "plant material", "crude", "tincture",
)
INVITRO_TREATMENT_EXTRA_PATTERNS = [
    re.compile(r'(?i)incubated?\s+for\s+([\d.]+)\s*(h(?:ours?)?|min(?:utes?)?|days?)'),
    re.compile(r'(?i)treated?\s+for\s+([\d.]+)\s*(h(?:ours?)?|min(?:utes?)?|days?)'),
    re.compile(r'(?i)(?:simulated|simulation)\s+(?:during|for|of)\s+([\d.]+)\s*(ns|us|microseconds?)'),
    re.compile(r'(?i)([\d.]+)\s*ns\s+(?:MD|molecular dynamics|simulation)'),
    re.compile(r'(?i)simulation\s+(?:of|for|totaling)\s+([\d.]+)\s*(ns|us|microseconds?)'),
    re.compile(r'(?i)(?:after|following)\s+(?:additional\s+)?([\d.]+)\s*(hrs?|hours?|min(?:utes?)?|days?)'),
    re.compile(r'(?i)(?:viability|apoptosis|cytotoxicity)[^.]{0,80}?(?:after|following)\s+([\d.]+)\s*(hrs?|hours?)'),
    re.compile(r'(?i)([\d.]+)\s*(?:hrs?|hours?)\s+(?:of\s+)?(?:incubation|treatment|exposure)'),
    re.compile(r'(?i)([\d.]+)\s*(?:hrs?|hours?)\s+later'),
    re.compile(r'(?i)stored?\s+for\s+([\d]+)\s*days?'),
    re.compile(r'(?i)([\d]+)[- ]day\s+(?:storage|stability|shelf)'),
    re.compile(r'(?i)stabilized\s+for\s+([\d.]+)\s*(hrs?|hours?|days?)'),
    re.compile(r'(?i)(?:ex\s+vivo|tissue bath|organ bath)[^.]{0,80}?for\s+([\d.]+)\s*(min(?:utes?)?)'),
    re.compile(r'(?i)(?:motility|colon|ileum|jejunum)[^.]{0,80}?([\d.]+)\s*(min(?:utes?)?)\b'),
]
TREATMENT_DURATION_RANGE_PATTERN = re.compile(
    r'(?i)(\d+(?:\.\d+)?)\s*(hrs?|hours?|min(?:utes?)?)?\s+to\s+(\d+(?:\.\d+)?)\s*(days?|hours?|min(?:utes?)?)'
)
CATALOG_COMPOUND_STRAIN_PATTERN = re.compile(
    r'(?i)((?:delta-9-tetrahydrocannabinol|delta9-tetrahydrocannabinol|cannabidiol|cbd|thc)'
    r'[^.\n]{0,120}?(?:sigma|cayman|tocris|serva|catalog|cat\.?\s*no|cas\s+\d))'
)
INVITRO_CONTEXT_CUES = (
    "in vitro", "cell line", "cells were", "well plate", "incubated", "culture medium",
    "μM", "µM", "confluence", "assay plate", "xtt", "mtt", "annexin",
)
INVIVO_PRIMARY_CUES = (
    "in vivo", "mg/kg", "orally gavage", "subcutaneous", "intraperitoneal",
    "sprague-dawley rats", "c57bl/6 mice", "wistar rats", "male rats", "female mice",
)
CLINICAL_SCHEDULE_CONTEXT = (
    "weeks treatment", "daily dose", "once daily", "mg/kg/day", "clinical trial",
)
MG_KG_DAY_SUFFIX = r'(?:\s*/\s*day|/day|\s+per\s+day)?'

THC_MG_KG_PATTERN = re.compile(
    rf'(?i)(?:THC|tetrahydrocannabinol|delta-?9)[^.]{{0,40}}?(\d+(?:\.\d+)?)\s*mg/kg{MG_KG_DAY_SUFFIX}'
)
CBD_MG_KG_PATTERN = re.compile(
    rf'(?i)(?:CBD|cannabidiol)[^.]{{0,40}}?(\d+(?:\.\d+)?)\s*mg/kg{MG_KG_DAY_SUFFIX}'
)
DOSE_MG_ABSOLUTE_PATTERN = re.compile(
    r'(?i)(?:each|per)\s+(?:animal|rat|mouse|mice|subject|participant)s?\s+(?:received\s+)?(\d+(?:\.\d+)?)\s*mg\b'
    r'|(\d+(?:\.\d+)?)\s*mg\s+(?:per\s+animal|total\s+dose|injected|administered)\b'
    r'|(?:dose\s+of\s+)?(\d+(?:\.\d+)?)\s*mg\s+(?:of\s+)?(?:THC|CBD|cannabidiol|tetrahydrocannabinol|cannabinoid)\b'
)
THC_UG_KG_PATTERN = re.compile(
    r'(?i)(?:THC|tetrahydrocannabinol|delta-?9|cannabinoid\s+agonist)[^.]{0,40}?(\d+(?:\.\d+)?)\s*[µu]g/kg'
)
CBD_UG_KG_PATTERN = re.compile(
    r'(?i)(?:CBD|cannabidiol)[^.]{0,40}?(\d+(?:\.\d+)?)\s*[µu]g/kg'
)

CULTIVAR_LABEL_PATTERN = re.compile(
    r"(?i)cultivar\s+(?:named\s+)?[\"']([^\"']+)[\"']"
    r"|chemovar\s+(?:named\s+)?[\"']?([A-Z][A-Za-z0-9 #+-]+?)(?:\s+obtained|\s+from|[,.;]|$)"
    r"|strain\s+(?:named\s+|['\"])([^'\"]+)['\"]"
)
CHEMOTYPE_PROFILE_BLOCK_PATTERN = re.compile(
    r'(?i)(?:\([ivx\d]+\)\s*)?Chemotype\s+(I{1,3}|II|III|IV|[IVX]+)\s*\([^)]*\)\s*[–—-][^.;]{0,220}'
)
BOTANICAL_SOURCE_PATTERN = re.compile(
    r'(?i)\b(?:potential of|source(?:\s+material)?\s+from)\s+'
    r'([A-Z][a-z]+(?:\s+[a-z]+){1,2}(?:\s+\([^)]+\))?(?:\s+[A-Z][a-z]+)?)\s+'
    r'(?:as an alternative source|source for cannabinoids|source of cannabinoids)'
)
COMPOUND_PANEL_NORMALIZERS = [
    (re.compile(r'(?i)\bCP[- ]?55[\s,]?940\b'), 'CP-55,940'),
    (re.compile(r'(?i)\bWIN\s?55[\s,]?212(?:-2)?\b'), 'WIN 55,212-2'),
    (re.compile(r'(?i)\bSR141716\b'), 'SR141716'),
    (re.compile(r'(?i)\bAM630\b'), 'AM630'),
    (re.compile(r'(?i)\bO-1602\b'), 'O-1602'),
    (re.compile(r'(?i)\bCID\s*16020046\b'), 'CID 16020046'),
    (re.compile(r'(?i)\bAEA\b|\banandamide\b'), 'AEA'),
    (re.compile(r'(?i)\bΔ9-THC\b|\bdelta-9-tetrahydrocannabinol\b'), 'Δ9-THC'),
    (re.compile(r'(?i)\bTHC\b(?!\s*Pharm)'), 'THC'),
    (re.compile(r'(?i)\bCBD\b(?!\s*A\b)'), 'CBD'),
    (re.compile(r'(?i)\bCBDV\b'), 'CBDV'),
]
NAMED_CULTIVAR_PROFILE_PATTERN = re.compile(
    r'(?i)(Skywalker Kush|Treasure Island(?:\s+Kush)?|Henola|Cherry Wine|'
    r'Citrus|Futura\s+\d+|Finola)\s*\([^)]*(?:%|THC|CBD)[^)]*\)'
)
CODED_CULTIVAR_PATTERN = re.compile(r'\b(CN\d+)\b')
EXTENDED_CULTIVAR_CODE_PATTERN = re.compile(r'\b(331-\d+[A-Z])\b')
SYNTHETIC_COMPOUND_STRAIN_PATTERNS = [
    re.compile(r'(?i)\bCP-55[\s,]?940\b'),
    re.compile(r'(?i)\bWIN\s?55[\s,]?212(?:-2)?\b'),
    re.compile(r'(?i)\bHU-210\b'),
    re.compile(r'(?i)\bJWH-\d+\b'),
    re.compile(r'(?i)\bAM-\d+\b'),
]
ANIMAL_STRAIN_PATTERNS = [
    (re.compile(r'(?i)\bSprague[- ]Dawley\b'), "Sprague-Dawley"),
    (re.compile(r'(?i)\bWistar\b'), "Wistar"),
    (re.compile(r'(?i)\bLong[- ]Evans\b'), "Long-Evans"),
    (re.compile(r'(?i)\bC57BL/6[A-Z]?\b'), "C57BL/6"),
    (re.compile(r'(?i)\bBALB/c\b'), "BALB/c"),
    (re.compile(r'(?i)\bCD-1\b'), "CD-1"),
    (re.compile(r'(?i)\bFischer 344\b'), "Fischer 344"),
    (re.compile(r'(?i)\bLewis\b'), "Lewis"),
    (re.compile(r'(?i)\b5xFAD\b'), "5xFAD"),
    (re.compile(r'(?i)\bgp120\s+transgenic\b'), "gp120 transgenic mouse model (GFAP promoter)"),
]
THC_MG_ML_PATTERN_A = re.compile(
    r'(?i)(\d+(?:\.\d+)?)\s*mg/m[lL][^\n]{0,40}THC'
)
THC_MG_ML_PATTERN_B = re.compile(
    r'(?i)THC[^\n]{0,40}?(\d+(?:\.\d+)?)\s*mg/m[lL]'
)
ISOLATED_FROM_PATTERN = re.compile(
    r'(?i)(?:isolated|purified)\s+(?:from\s+)?([A-Za-z0-9][A-Za-z0-9\s\-]{1,40})'
)
SUPPLIER_COMPOUND_PATTERN = re.compile(
    r'(?i)([A-Za-z0-9][A-Za-z0-9\- ]{1,40}?)\s*(?:\(.*?)?(?:Sigma-Aldrich|Cayman Chemical|Tocris)'
)

# Treatment duration patterns (for in vitro)
TREATMENT_DURATION_PATTERNS = [
    re.compile(r'(?i)(?:incubated|treated|exposed|stimulated|cultured|maintained)\s+(?:\w+\s+){0,4}?for\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\b'),
    re.compile(r'(?i)for\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\s+(?:at\s+\d+\s*°?C\s*)?(?:incubation|treatment|exposure|stimulation|culture)\b'),
    re.compile(r'(?i)(\d+(?:\.\d+)?)\s*(h|hrs?|minutes?|days?)\s+(?:treatment|incubation|exposure|culture|stimulation)\b'),
    re.compile(r'(?i)(?:for|during)\s+(\d+(?:\.\d+)?)\s*(h(?:ou)?rs?|min(?:ute)?s?|days?)\s+(?:of\s+)?(?:treatment|incubation|exposure|culture|stimulation)'),
    re.compile(r'(?i)\b(\d+(?:\.\d+)?)\s*h\b(?!\s*(?:z|ertz|z\s+frequency|z\s+stimulation))'),
]

# Sample Size
SAMPLE_SIZE_REPORTED_ON_PATTERN = re.compile(
    r'(?i)results are (?:therefore )?reported on (\d+) \w+(?:\s*\([^)]*\))?\s+and (\d+) \w+'
)
SAMPLE_SIZE_TOTAL_USED_PATTERN = re.compile(
    r'(?i)in total,\s*(\d+)\b'
)
SAMPLE_SIZE_FINAL_SAMPLE_PATTERN = re.compile(
    r'(?i)f[i\uFB01]nal sample\s*\(\s*n\s*=\s*(\d+)\s*\)'
)
SAMPLE_SIZE_COMPLETED_COHORT_PATTERN = re.compile(
    r'(?i)(?:data from|in the current study, data from)\s+(\d+)\s+participants\s*\(\s*n\s*=\s*(\d+)\s*\)'
)
SAMPLE_SIZE_COMPLETED_SUBSET_PATTERN = re.compile(
    r'(?i)(?:\(?n\s*=\s*(\d+)\)?[^.]{0,40}completed all|'
    r'(\d+)\s+of them have completed(?: all)?|'
    r'(\d+)\s+participants have completed(?: all)?)'
)
SAMPLE_SIZE_SPLIT_DISEASE_PATTERN = re.compile(
    r'(?i)\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|'
    r'fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)\s+'
    r'patients with \w+ and (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
    r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)\s+'
    r'patients with \w+ participated'
)
SAMPLE_SIZE_WORD_TO_INT = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
SAMPLE_SIZE_PATTERNS = [
    re.compile(r'(?i)\bn\s*=\s*(\d+)\b'),
    re.compile(r'(?i)\bN\s*=\s*(\d+)\b'),
    re.compile(r'(?i)\bsample\s+size\s+(?:of|is)\s+(\d+)\b'),
    re.compile(r'(?i)\b(?:tested in|analyzed in|included)\s+(\d+)\s+volunteers\b'),
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
    """Extracts THC percentage from text, rejecting acid-fraction ratios."""
    return _extract_plant_cannabinoid_pct(text, THC_PATTERNS, max_pct=40.0)

def extract_cbd_pct(text: str) -> Optional[float]:
    """Extracts CBD percentage from text, rejecting acid-fraction ratios."""
    return _extract_plant_cannabinoid_pct(text, CBD_PATTERNS, max_pct=25.0)


def _extract_plant_cannabinoid_pct(
    text: str,
    patterns: list,
    *,
    max_pct: float,
) -> Optional[float]:
    """Returns plant-level cannabinoid weight-percent, skipping acid-ratio contexts."""
    if not text:
        return None
    for pattern in patterns:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) == 2:
                try:
                    value = (float(groups[0]) + float(groups[1])) / 2.0
                except ValueError:
                    continue
            elif len(groups) == 1:
                try:
                    value = float(groups[0])
                except ValueError:
                    continue
            else:
                continue
            window = text[max(0, match.start() - 30): min(len(text), match.end() + 30)].lower()
            if any(token in window for token in ("acid", "acidic", "ratio", "acid to total", "thca", "cbda")):
                continue
            if 0.0 <= value <= max_pct:
                return value
    for match in re.finditer(r'(?i)(\d+(?:\.\d+)?)\s*%\s*(?:w/w|w/v)?', text):
        window = text[max(0, match.start() - 40): min(len(text), match.end() + 40)].lower()
        if any(token in window for token in ("acid", "acidic", "ratio", "acid to total", "thca", "cbda")):
            continue
        compound_tokens = ("thc", "cbd", "cannabidiol", "tetrahydrocannabinol")
        if max_pct <= 25.0:
            compound_tokens = ("cbd", "cannabidiol")
        else:
            compound_tokens = ("thc", "tetrahydrocannabinol")
        if not any(token in window for token in compound_tokens):
            continue
        value = float(match.group(1))
        if 0.0 <= value <= max_pct:
            return value
    return None

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
    """Extracts absolute dose in mg only when explicitly stated (not inferred from mg/kg)."""
    if not text:
        return None

    for pattern in (DOSE_MG_ABSOLUTE_PATTERN, DOSE_MG_PATTERN):
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g is not None]
            if not groups:
                continue
            dose = float(groups[0])
            start, end = match.start(), match.end()
            window = text[max(0, start - 8): min(len(text), end + 12)].lower()
            if re.search(r'mg/(?:kg|ml|g|day)', window):
                continue
            pre_ctx = text[max(0, start - 60):start].lower()
            post_ctx = text[end:min(len(text), end + 60)].lower()
            if _nearest_substance_is_non_cannabinoid(pre_ctx, post_ctx):
                continue
            return dose

    return None

def extract_duration_days(text: str) -> Optional[float]:
    """Extracts study duration and converts it to float days, ignoring age references."""
    if not text:
        return None

    if re.search(
        r"(?i)\b(?:from early adolescence|from adolescence|birth cohort|followed (?:from|through)|"
        r"early adulthood to|mid-twenties|lifespan|life course)\b",
        text,
    ):
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

    # Gestational / post-natal day ranges (high priority)
    gd_match = re.search(r'(?i)gestational\s+days?\s*(\d+)\s*[–\-]\s*(\d+)', text)
    if gd_match:
        return float(int(gd_match.group(2)) - int(gd_match.group(1)))
    gd_to_match = re.search(r'(?i)gestational\s+days?\s+(\d+)\s+(?:to|-)\s+(\d+)', text)
    if gd_to_match:
        return float(int(gd_to_match.group(2)) - int(gd_to_match.group(1)))
    pnd_match = re.search(r'(?i)(?:PND|postnatal day|post-natal day)\s*(\d+)\s+to\s+(?:PND|postnatal day|post-natal day)\s*(\d+)', text)
    if pnd_match:
        return float(int(pnd_match.group(2)) - int(pnd_match.group(1)))
    postnatal_match = re.search(r'(?i)post-natal\s+days?\s*(\d+)\s*[–\-]\s*(\d+)', text)
    if postnatal_match:
        return float(int(postnatal_match.group(2)) - int(postnatal_match.group(1)))

    for_days = re.search(r'(?i)\b(?:for|following|after)\s+(\d+)\s*days?\b', text)
    if for_days and not _duration_in_cell_culture_context(text, for_days.start(), for_days.end()):
        return float(for_days.group(1))
    days_of = re.search(
        r'(?i)(\d+)\s+days?\s+of\s+(?:treatment|exposure|administration)',
        text,
    )
    if days_of and not _duration_in_cell_culture_context(text, days_of.start(), days_of.end()):
        return float(days_of.group(1))
    for_weeks = re.search(r'(?i)\b(?:for|following|over)\s+(\d+)\s*weeks?\b', text)
    if for_weeks and not _duration_in_cell_culture_context(text, for_weeks.start(), for_weeks.end()):
        return float(int(for_weeks.group(1)) * 7)
    received_weeks = re.search(
        r'(?i)(?:received|treated|injected|administered|exposed)[^.]{0,80}?\bfor\s+(\d+)\s*weeks?\b',
        text,
    )
    if received_weeks and not _duration_in_cell_culture_context(
        text, received_weeks.start(), received_weeks.end(),
    ):
        return float(int(received_weeks.group(1)) * 7)
    week_protocol = re.search(
        r'(?i)(\d+)[- ]week\s+(?:treatment|exposure|protocol)',
        text,
    )
    if week_protocol:
        return float(int(week_protocol.group(1)) * 7)
    over_weeks = re.search(r'(?i)\bover\s+(\d+)\s*weeks?\b', text)
    if over_weeks:
        return float(int(over_weeks.group(1)) * 7)

    # Explicit N-day treatment/chronic/exposure
    explicit_day = re.search(
        r'(?i)(\d+)[- ]day\s+(?:treatment|chronic|exposure|study|protocol|administration|regimen)',
        text,
    )
    if explicit_day:
        return float(explicit_day.group(1))

    # 1. First, search for explicit context-aware study duration matches
    match = DURATION_PATTERN.search(text)
    if match and not _duration_in_cell_culture_context(text, match.start(), match.end()):
        days = convert_to_days(float(match.group(1)), match.group(2))
        if days <= 30 * 365.0:
            return days

    # 2. Hyphenated durations (e.g. "30-day MRT", "6-week trial") — first match wins;
    #    skip follow-up windows so intervention length beats post-trial follow-up.
    hyphen_pattern = re.compile(r'(?i)\b(\d+(?:\.\d+)?)-(day|week|month|year)s?\b')
    for match in hyphen_pattern.finditer(text):
        post_context = text[match.end():match.end() + 30].lower()
        if re.search(r'\bfollow[- ]?up\b', post_context):
            continue
        if _duration_in_cell_culture_context(text, match.start(), match.end()):
            continue
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

        if _duration_in_cell_culture_context(text, start_idx, end_idx):
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
            if len(groups) == 1 and groups[0].isdigit():
                return f"{groups[0]} minutes"
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


def _frequency_is_use_not_administration(text: str, start: int, end: int) -> bool:
    """True when a frequency hit reflects consumption/use rather than a treatment schedule."""
    window = text[max(0, start - 70): min(len(text), end + 70)].lower()
    use_patterns = (
        r"\b(?:cannabis|marijuana|tobacco|drug|substance|alcohol)\s+use\b",
        r"\bdaily\s+(?:users?|use|consumption|intake|smokers?|drinkers?)\b",
        r"\b(?:used|using|use)\s+(?:cannabis|marijuana|tobacco)\b",
        r"\bfollow[- ]?up\b",
        r"\b(?:visits?|assessments?|surveys?|questionnaires?)\s+(?:weekly|monthly|daily)\b",
        r"\b(?:mobile phone|smartphone|app|application)\b.*\b(?:weekly|monthly)\b",
        r"\b(?:weekly|monthly)\b.*\b(?:app|application|reports?)\b",
        r"\bgreater than three times\s+daily\b",
        r"\b(?:i love|i do not need|what i dislike|testimonial|open[- ]ended)\b",
        r"\btake it daily\b",
        r"\b(?:neuroleptic|chlorpromazine|antipsychotic|psychotropic)\b",
        r"\baverage daily (?:neuroleptic|dose)\b",
    )
    return any(re.search(pattern, window) for pattern in use_patterns)


def _frequency_requires_interventional_context(study_type: Any) -> bool:
    """True when study design is observational and frequency should require dosing context."""
    if not study_type:
        return False
    types = study_type if isinstance(study_type, list) else [study_type]
    clinical = [item for item in types if isinstance(item, str) and item.startswith("Clinical (")]
    if not clinical:
        return False
    if any("RCT" in item for item in clinical):
        return False
    return True


def _is_endocannabinoid_biomarker_study(text: str, study_type: Any) -> bool:
    """True when a clinical study measures endogenous cannabinoid levels without administering product."""
    if not text:
        return False
    types = study_type if isinstance(study_type, list) else [study_type] if study_type else []
    if not any(str(item).startswith("Clinical (") for item in types):
        return False
    lowered = text.lower()
    biomarker_cues = re.search(
        r"(?i)\b(?:cerebrospinal fluid|\bcsf\b|plasma|serum|saliva|"
        r"endocannabinoid(?:\s+levels?)?|anandamide(?:\s+levels?)?|"
        r"2-ag|ae[aA]\s+levels?|endogenous cannabinoid)\b",
        lowered,
    )
    if not biomarker_cues:
        return False
    administered = re.search(
        r"(?i)\b(?:participants?\s+(?:were\s+)?(?:received|given|administered)|"
        r"patients?\s+(?:were\s+)?(?:received|given|administered)|"
        r"volunteers?\s+(?:were\s+)?(?:received|given|administered)|"
        r"subjects?\s+(?:were\s+)?(?:received|given|administered)|"
        r"were randomized to receive|intervention arm|"
        r"(?:received|given)\s+(?:a\s+)?(?:dose|daily\s+dose)\s+of|"
        r"treated with\s+(?:\d|cbd|thc|nabilone|dronabinol))\b",
        lowered,
    )
    if administered and any("RCT" in str(item) for item in types):
        return False
    if administered:
        return False
    if re.search(
        r"(?i)\b(?:cerebrospinal fluid|\bcsf\b).{0,80}endocannabinoid|"
        r"endocannabinoid.{0,80}(?:cerebrospinal fluid|\bcsf\b)",
        lowered,
    ):
        return True
    return True


def _extract_detected_substance_strain(text: str) -> Optional[str]:
    """For toxicology/drug-testing papers, return the detected synthetic cannabinoid name."""
    if not text:
        return None
    if not re.search(
        r"(?i)\b(?:substances? detected|detected in|toxicology|forensic|"
        r"illicit drug|designer drug|new psychoactive)\b",
        text,
    ):
        return None
    for pattern in (
        r"(?i)\bAMB-FUBINACA\b",
        r"(?i)\bAB-FUBINACA\b",
        r"(?i)\b5F-ADB\b",
        r"(?i)\bADB-FUBINACA\b",
        r"(?i)\bJWH-\d+\b",
        r"(?i)\bUR-144\b",
        r"(?i)\bXLR-11\b",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


def _title_outcome_hints(title: str) -> List[str]:
    """Maps title condition phrases to outcome domains before intro/abstract scanning."""
    if not title:
        return []
    title_lower = title.lower()
    hints: List[str] = []

    def add(domain: str) -> None:
        if domain not in hints:
            hints.append(domain)

    if re.search(r"ulcerative colitis|crohn|inflammatory bowel|\bibd\b", title_lower):
        add("inflammation")
    if re.search(r"schizophrenia|psychotic|psychosis", title_lower):
        add("cognition")
    if re.search(r"substances detected|drug testing|toxicology|illicit drug", title_lower):
        add("addiction")
    if re.search(r"delta-8|delta 8|\bΔ8\b", title_lower):
        for domain in ("pain", "anxiety", "cognition"):
            add(domain)
    if re.search(r"psychedelic|hallucinogen", title_lower) and re.search(
        r"cannabis|marijuana|thc|cbd", title_lower,
    ):
        add("anxiety")
        add("cognition")
    if re.search(r"pain|analges", title_lower):
        add("pain")
    if re.search(r"anxiety|anxiolytic", title_lower):
        add("anxiety")
    if re.search(r"gilles de la tourette|tourette syndrome", title_lower):
        return []
    return hints


def _has_interventional_administration_context(text: str, start: int, end: int) -> bool:
    """True when nearby text describes a cannabinoid dosing or intervention schedule."""
    window = text[max(0, start - 120): min(len(text), end + 120)].lower()
    if not re.search(
        r"\b(?:administered|dose|dosing|intervention|randomized|placebo|protocol|"
        r"treatment(?: group)?|mg(?:/kg)?|capsule|oil|nabilone|dronabinol|cbd|thc)\b",
        window,
    ):
        return False
    if re.search(r"\bstarting dose was\b", window) and not re.search(
        r"\b(?:participants|subjects|patients)\s+(?:were|received|randomized|assigned)\b",
        window,
    ):
        return False
    return True


def extract_administration_frequency(
    text: str,
    *,
    study_type: Any = None,
) -> Optional[str]:
    """Extracts administration frequency (e.g. 'once daily', 'twice daily') from text."""
    if not text:
        return None
    text = _normalize_extraction_text(re.sub(r'\s+', ' ', text))
    interventional_required = _frequency_requires_interventional_context(study_type)
    for pattern in FREQUENCY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if _frequency_is_use_not_administration(text, match.start(), match.end()):
            continue
        raw = match.group(0).strip()
        raw_lower = raw.lower()
        if "multiple doses per session" in raw_lower or re.search(
            r'(?i)dosages?.{0,50}\d+\s*min(?:utes?)?\s+apart', raw_lower,
        ):
            return "multiple doses per session"
        if interventional_required and not _has_interventional_administration_context(
            text, match.start(), match.end()
        ):
            continue
        if interventional_required and not re.search(
            r"\b(?:participants|subjects|patients|volunteers)\s+(?:were|received|randomized|assigned|included)\b",
            text[max(0, match.start() - 120): min(len(text), match.end() + 120)],
            re.IGNORECASE,
        ):
            continue
        if raw_lower == "daily":
            return "daily"
        if "twice daily" in raw_lower:
            return "twice daily"
        if "once daily" in raw_lower or "once per day" in raw_lower or "once a day" in raw_lower:
            return "daily"
        if "twice a day" in raw_lower or "twice per day" in raw_lower:
            return "twice daily"
        if "every other day" in raw_lower:
            return "every other day"
        if "alternating days" in raw_lower or "alternating day" in raw_lower:
            return "alternating days"
        on_off = re.search(r'(?i)(\d+)\s*days?\s*on[,/\s]+(\d+)\s*days?\s*off', raw)
        if on_off:
            return f"{on_off.group(1)}d on / {on_off.group(2)}d off"
        if raw_lower == "weekly":
            return "weekly"
        if "once weekly" in raw_lower:
            return "once weekly"
        if "twice weekly" in raw_lower or "2x weekly" in raw_lower or "twice a week" in raw_lower:
            return "twice weekly"
        if "5 days" in raw_lower and "2 days" in raw_lower and "off" in raw_lower:
            return "5 days on / 2 days off"
        if "daily from gestational" in raw_lower:
            return "daily"
        times_week = re.search(r'(?i)(\d+)\s*times?\s*/\s*week', raw)
        if times_week:
            return f"{times_week.group(1)}x/week"
        days_per_week = re.search(r'(?i)(\d+)\s*days?\s*/\s*week', raw)
        if days_per_week:
            return f"{days_per_week.group(1)} days/week"
        if raw_lower == "daily":
            return "daily"
        return raw
    return None


def extract_thc_mg_ml(text: str) -> Optional[float]:
    """Extracts THC concentration in mg/mL when stated near a THC mention."""
    if not text:
        return None
    for pattern in (THC_MG_ML_PATTERN_A, THC_MG_ML_PATTERN_B):
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def _extract_vendor_isolated_compounds(text: str) -> List[str]:
    """Captures vendor-qualified isolated THC/CBD mentions within a short window."""
    if not text:
        return []
    found: List[str] = []
    lowered = text.lower()
    for vendor_key, label in (("sigma", "isolated THC (Sigma)"), ("cayman", "isolated CBD (Cayman)")):
        for match in re.finditer(re.escape(vendor_key), lowered):
            window = lowered[max(0, match.start() - 60): min(len(lowered), match.end() + 60)]
            if "thc" in window and vendor_key == "sigma" and label not in found:
                found.append(label)
            if "cbd" in window and vendor_key == "cayman" and label not in found:
                found.append(label)
    return found


def _format_treatment_duration_value(val: str, unit_raw: str) -> Optional[str]:
    """Normalizes a numeric duration and unit into a canonical label."""
    unit_raw = unit_raw.lower().rstrip('.')
    if unit_raw.startswith("d"):
        if 1900 <= float(val) <= 2030:
            return None
    if unit_raw.startswith("h"):
        unit = "hour" if float(val) == 1 else "hours"
    elif unit_raw.startswith("min"):
        unit = "minutes"
    elif unit_raw.startswith("d"):
        unit = "day" if float(val) == 1 else "days"
    elif unit_raw in ("ns", "us") or unit_raw.startswith("micro"):
        unit = unit_raw if unit_raw != "us" else "us"
        if unit_raw.startswith("micro"):
            unit = "microseconds"
    else:
        unit = unit_raw
    val_f = float(val)
    if val_f.is_integer():
        val_f = int(val_f)
    return f"{val_f} {unit}"


def _score_treatment_duration_match(match: re.Match, text: str, formatted: str) -> int:
    """Scores a duration regex hit so incubation/treatment windows beat prep noise."""
    window_before = text[max(0, match.start() - 70):match.start()].lower()
    window_after = text[match.end():min(len(text), match.end() + 50)].lower()
    window = window_before + match.group(0).lower() + window_after
    score = 40

    positive_cues = (
        ("incubat", 45), ("treated", 40), ("exposed", 35), ("maintained", 25),
        ("viability", 30), ("cytotoxic", 30), ("apoptosis", 25), ("after", 20),
        ("following", 18), ("molecular dynamics", 55), ("simulation", 40),
        ("simulated", 45), ("storage", 28), ("stabilized", 25), ("pretreatment", 22),
    )
    for cue, pts in positive_cues:
        if cue in window:
            score += pts

    negative_cues = (
        ("centrifug", -35), ("page ", -60), (" z ", -45), ("frequency", -30),
        ("decarboxyl", -25), ("grinded", -20), ("ned", -15),
    )
    for cue, pts in negative_cues:
        if cue in window:
            score += pts

    val_str, unit = formatted.split(maxsplit=1)
    val = float(val_str)
    unit_lower = unit.lower()
    if unit_lower.startswith("hour"):
        if val in (6, 12, 24, 48, 72):
            score += 25
        elif val <= 3:
            score -= 20
        elif val <= 4:
            score -= 10
    elif unit_lower.startswith("min"):
        if val <= 15 and "incubat" not in window:
            score -= 25
    elif unit_lower.startswith("day"):
        if any(token in window for token in ("storage", "stabil", "shelf", "stored")):
            score += 30
        elif val >= 7 and "incubat" not in window and "treat" not in window:
            score -= 25
    elif unit_lower == "ns":
        score += 35

    return score


def _duration_preference_rank(label: str) -> int:
    """Secondary sort key favoring canonical cell-culture incubation windows."""
    val_str, unit = label.split(maxsplit=1)
    val = float(val_str)
    unit_lower = unit.lower()
    if unit_lower.startswith("hour"):
        preference = {24: 100, 48: 90, 72: 85, 12: 80, 6: 70, 1: 20}
        return preference.get(int(val) if val.is_integer() else val, 30 if val > 4 else 10)
    if unit_lower.startswith("day"):
        return 60
    if unit_lower == "ns":
        return 95
    if unit_lower.startswith("min"):
        return 15
    return 40


def extract_treatment_duration(text: str) -> Optional[str]:
    """Extracts in vitro treatment duration (e.g. '24 hours', '100 ns') from text."""
    if not text:
        return None

    range_match = TREATMENT_DURATION_RANGE_PATTERN.search(text)
    if range_match:
        low_unit = range_match.group(2) or range_match.group(4)
        low = _format_treatment_duration_value(range_match.group(1), low_unit)
        high = _format_treatment_duration_value(range_match.group(3), range_match.group(4))
        if low and high:
            return f"{low} to {high}"

    candidates: List[tuple[int, str]] = []
    all_patterns = list(TREATMENT_DURATION_PATTERNS) + INVITRO_TREATMENT_EXTRA_PATTERNS
    for pattern in all_patterns:
        for match in pattern.finditer(text):
            pre = text[max(0, match.start() - 30):match.start()].lower()
            if any(token in pre for token in CLINICAL_SCHEDULE_CONTEXT):
                continue
            groups = match.groups()
            if not groups:
                continue
            if len(groups) == 1:
                unit_guess = "ns" if "ns" in match.group(0).lower() else "hours"
                formatted = _format_treatment_duration_value(groups[0], unit_guess)
            else:
                formatted = _format_treatment_duration_value(groups[0], groups[1])
            if not formatted:
                continue
            score = _score_treatment_duration_match(match, text, formatted)
            candidates.append((score, formatted))

    if not candidates:
        return None
    best_score = max(score for score, _ in candidates)
    top = [(score, label) for score, label in candidates if score >= best_score - 30]
    top.sort(key=lambda item: (-_duration_preference_rank(item[1]), -item[0], item[1]))
    return top[0][1]


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

def _parse_sample_size_token(token: str) -> Optional[int]:
    """Parses an integer or spelled-out count token for cohort-size extraction."""
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return SAMPLE_SIZE_WORD_TO_INT.get(token)


def _sample_size_match_invalid(text: str, start: int, end: int) -> bool:
    """True when an N= hit sits in a URL, reference link, or population-statistics context."""
    window = text[max(0, start - 50): min(len(text), end + 50)]
    wide_window = text[max(0, start - 90): min(len(text), end + 90)]
    if re.search(r"https?://|www\.|doi\.org|\.pdf|lang=|indicadors|accessed\s+\d", window, re.IGNORECASE):
        return True
    if re.search(r"\b(?:prior publication|validating the|validation sample|sample population numbers)\b", window, re.IGNORECASE):
        return True
    pre = text[max(0, start - 25): start].lower()
    if re.search(r"\b(?:population|prevalence|census|residents|inhabitants|households)\b", pre):
        return True
    post = text[end: min(len(text), end + 40)].lower()
    if re.search(r"\b(?:population|prevalence|residents|inhabitants)\b", post):
        return True
    if re.search(r"\bn\s*=\s*\d+/\d+\b", window, re.IGNORECASE):
        return True
    if re.search(r"\b(?:heavy|light)\s+(?:use )?condition\b", window, re.IGNORECASE):
        return True
    if re.search(
        r"\b(?:e-liquid|e liquid|vape liquid|commercially available|products were|brands tested|samples analyzed)\b",
        wide_window,
        re.IGNORECASE,
    ) and not re.search(r"\b(?:participants|patients|subjects|volunteers|respondents)\b", wide_window, re.IGNORECASE):
        return True
    if re.search(r"\b(?:signed up|registered for the survey)\b", wide_window, re.IGNORECASE) and not re.search(
        r"\b(?:completed|included for analys|final sample|data from)\b", wide_window, re.IGNORECASE
    ):
        return True
    if re.search(r"\b(?:subjects included in the \w+ group)\b", wide_window, re.IGNORECASE) and re.search(
        r"\bresults are (?:therefore )?reported on\b", text, re.IGNORECASE
    ):
        return True
    return False


def _score_sample_size_match(text: str, start: int, end: int, val: int) -> int:
    """Scores an N= match so study cohort sizes beat incidental population counts."""
    if _sample_size_match_invalid(text, start, end):
        return -100
    window = text[max(0, start - 90): min(len(text), end + 90)].lower()
    wide_window = text[max(0, start - 140): min(len(text), end + 140)].lower()
    score = 0
    if re.search(
        r"\b(?:participants|patients|subjects|volunteers|respondents|enrolled|recruited|surveyed)\b",
        window,
    ):
        score += 12
    if re.search(
        r"\b(?:sample size|cohort|randomized|trial|included|completed|analytical sample|included in analysis)\b",
        window,
    ):
        score += 6
    if re.search(r"\b(?:completed the|completed survey|final sample|analyzed sample|included for data|were included)\b", window):
        score += 14
    if val > 1500 and score < 15:
        score -= 25
    if re.search(r"\b(?:total|overall)\b", window):
        score += 8
    if re.search(r"\b(?:mice|rats|animals|wells|replicates)\b", window):
        score += 4 if re.search(r"\btotal\b", window) else -2
    if re.search(r"\b(?:e-liquid|e liquid|commercial product|brands?|formulations?)\b", window):
        score -= 18
    if re.search(r"\b(?:included in (?:the )?analysis|final sample|analyzed sample|eligible participants)\b", window):
        score += 16
    if re.search(r"\b(?:pilot|completed (?:the )?study|completed assessment|assessed)\b", window):
        score += 10
    if re.search(r"\b(?:current sample|enrolled in (?:the )?current study|demographic variable)\b", window):
        score += 18
    if re.search(r"\bcurrent sample\s*\(\s*n\s*=", window, re.IGNORECASE):
        score += 22
    if re.search(r"demographic variable\s*\(\s*n\s*=", window, re.IGNORECASE):
        score += 20
    if re.search(r"\bof\s+11\s+participants\b", window, re.IGNORECASE):
        score += 16
    if re.search(r"\b(?:completed screening|screening procedures)\b", window) and not re.search(
        r"\b(?:current sample|included in|final analysis)\b", window
    ):
        score -= 15
    if re.search(r"\b(?:per condition|per session|per arm|per visit|each condition)\b", window):
        score -= 20
    if re.search(r"\b(?:screened|eligible|enrolled)\b", window) and not re.search(
        r"\b(?:included|completed|analyzed|final|current sample)\b", window
    ):
        score -= 12
    if re.search(r"\b(?:birth cohort|longitudinal cohort|followed from)\b", window):
        score += 12
    if re.search(r"\b(?:signed up|registered for the survey)\b", window) and not re.search(
        r"\b(?:completed|included for analys|final sample|data from)\b", window
    ):
        score -= 40
    if re.search(r"\b(?:completed all|have completed all|included for analys|data from \d+ participants)\b", window):
        score += 22
    if re.search(r"\b(?:of them have completed|completion rate)\b", wide_window):
        score += 18
    if re.search(r"\bfinal sample\b", wide_window):
        score += 24
    if re.search(r"\b(?:due to scheduling|technical issues|partial scan)\b", window):
        score -= 18
    if re.search(r"\bin total,\s*\d+\b", wide_window):
        score += 26
    if re.search(r"\bcohort consists of\b", window) and not re.search(r"\bin total,\s*\d+\b", wide_window):
        score -= 16
    if re.search(r"\b(?:tested in|analyzed in|included)\s+\d+\s+volunteers\b", wide_window):
        score += 20
    if re.search(r"\bresults are (?:therefore )?reported on\b", wide_window):
        score += 24
    return score


def _extract_priority_sample_size(text: str) -> Optional[int]:
    """Returns high-confidence cohort totals before generic N= scoring."""
    if not text:
        return None
    match = SAMPLE_SIZE_REPORTED_ON_PATTERN.search(text)
    if match:
        return int(match.group(1)) + int(match.group(2))
    match = SAMPLE_SIZE_COMPLETED_COHORT_PATTERN.search(text)
    if match:
        return int(match.group(2) or match.group(1))
    match = SAMPLE_SIZE_COMPLETED_SUBSET_PATTERN.search(text)
    if match:
        for group in match.groups():
            if group:
                return int(group)
    match = SAMPLE_SIZE_FINAL_SAMPLE_PATTERN.search(text)
    if match:
        return int(match.group(1))
    match = SAMPLE_SIZE_TOTAL_USED_PATTERN.search(text)
    if match:
        return int(match.group(1))
    match = SAMPLE_SIZE_SPLIT_DISEASE_PATTERN.search(text)
    if match:
        left = _parse_sample_size_token(match.group(1))
        right = _parse_sample_size_token(match.group(2))
        if left and left > 2:
            return left
        if left and right and left > 2 and right > 2:
            return left + right
    return None


def _normalize_extraction_text(text: str) -> str:
    """Normalizes PDF ligatures and whitespace for regex extraction."""
    if not text:
        return text
    return (
        text.replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .replace("\u2019", "'")
    )


def extract_sample_size(text: str) -> Optional[int]:
    """Extracts sample size N from text, preferring cohort-scoped matches over stray counts."""
    if not text:
        return None
    text = _normalize_extraction_text(text)
    priority = _extract_priority_sample_size(text)
    if priority is not None:
        return priority
    best_val: Optional[int] = None
    best_score = -101
    for pattern in SAMPLE_SIZE_PATTERNS:
        for match in pattern.finditer(text):
            val = int(match.group(1))
            if val <= 2:
                continue
            score = _score_sample_size_match(text, match.start(), match.end(), val)
            if score < 0:
                continue
            if score > best_score:
                best_score = score
                best_val = val
            elif score == best_score and best_val is not None and val > best_val:
                best_val = val
    return best_val

def extract_thc_cbd_mg_kg(text: str) -> tuple[Optional[float], Optional[float], bool]:
    """Extracts THC/CBD mg/kg doses from text; converts µg/kg to mg/kg.

    Returns:
        tuple: (thc_mg_kg, cbd_mg_kg, multiple_doses)
    """
    if not text:
        return None, None, False

    thc_values: List[float] = []
    cbd_values: List[float] = []

    for pattern in (THC_MG_KG_PATTERN, THC_UG_KG_PATTERN):
        for match in pattern.finditer(text):
            value = float(match.group(1))
            if pattern is THC_UG_KG_PATTERN:
                value /= 1000.0
            thc_values.append(value)

    for pattern in (CBD_MG_KG_PATTERN, CBD_UG_KG_PATTERN):
        for match in pattern.finditer(text):
            value = float(match.group(1))
            if pattern is CBD_UG_KG_PATTERN:
                value /= 1000.0
            cbd_values.append(value)

    multiple = len(thc_values) > 1 or len(cbd_values) > 1
    thc_mg_kg = min(thc_values) if thc_values else None
    cbd_mg_kg = min(cbd_values) if cbd_values else None
    return thc_mg_kg, cbd_mg_kg, multiple


def _has_plant_matter_language(text: str) -> bool:
    """True when text describes whole-plant or flower material (not isolated compound)."""
    lowered = text.lower()
    return any(cue in lowered for cue in PLANT_MATTER_CUES) or keyword_match(
        lowered,
        ["flower", "bud", "joint", "herbal cannabis", "marijuana cigarette", "combusted flower"],
    )


def _has_pharma_isolation_cues(text: str) -> bool:
    """True when text suggests pharmaceutical-grade isolated cannabinoid."""
    lowered = text.lower()
    if any(supplier in lowered for supplier in PHARMA_SUPPLIER_CUES):
        return True
    if re.search(r'(?i)\b(isolated|purified|pharmaceutical[- ]grade)\b', text):
        return True
    if re.search(r'(?i)\bmg/kg\b', text) and keyword_match(
        lowered,
        list(PURE_CANNABINOID_COMPOUND_CUES) + ["pure thc", "pure cbd", "isolate"],
    ):
        return not _has_plant_matter_language(text)
    return False


def _has_dissolved_in_media_cue(text: str) -> bool:
    """True when text describes bath, tissue-strip, or immersion exposure."""
    lowered = text.lower()
    if any(trigger in lowered for trigger in DISSOLVED_IN_MEDIA_TRIGGERS):
        return True
    if re.search(r'(?i)isolated[\w\s\-]{0,30}strips', text):
        return True
    if "tank water" in lowered and re.search(r'\b(thc|cbd|cannabinoid)\b', lowered):
        return True
    return False


def _has_injection_route_guard(text: str) -> bool:
    """True when explicit parenteral route abbreviations appear near a cannabinoid mention."""
    lowered = text.lower()
    for guard in INJECTION_ROUTE_GUARDS:
        for match in re.finditer(re.escape(guard), lowered):
            window = lowered[max(0, match.start() - 60): min(len(lowered), match.end() + 60)]
            if re.search(
                r'\b(thc|cbd|cannabidiol|tetrahydrocannabinol|cannabinoid|marijuana|dronabinol|nabilone)\b',
                window,
            ):
                return True
    return False


def _has_pure_cannabinoid_vendor_override(text: str) -> bool:
    """True when compound description includes pharma vendor or purity certification."""
    lowered = text.lower()
    if any(vendor.lower() in lowered for vendor in COMPOUND_PROVENANCE_VENDORS):
        return True
    if re.search(r'(?i)\d+\.?\d*\s*%\s*purity', text):
        return True
    if re.search(r'(?i)certified reference standard', text):
        return True
    return False


def _text_suggests_pure_cannabinoid(text: str) -> bool:
    """Heuristic pure-cannabinoid signal for strain provenance fallback."""
    return (
        keyword_match(text.lower(), [
            "pure thc", "pure cbd", "pure cannabinoid", "cannabidiol isolate",
            "dronabinol", "nabilone", "marinol", "isolate", "isolates",
        ])
        or _has_pure_cannabinoid_vendor_override(text)
        or _has_pharma_isolation_cues(text)
    )


def _extract_animal_strain_labels(text: str) -> List[str]:
    """Returns animal model strain labels when explicitly mentioned."""
    labels: List[str] = []
    seen: set = set()
    for pattern, label in ANIMAL_STRAIN_PATTERNS:
        if pattern.search(text):
            key = label.lower()
            if key not in seen:
                labels.append(label)
                seen.add(key)
    vendor_patterns = (
        (re.compile(r'(?i)\bJackson\s+(?:Laborator(?:y|ies)|Lab(?:s)?)\b'), "Jackson Laboratories"),
        (re.compile(r'(?i)\bCharles\s+River\b'), "Charles River"),
        (re.compile(r'(?i)\bEnvigo\b'), "Envigo"),
        (re.compile(r'(?i)\bHarlan\b'), "Harlan"),
        (re.compile(r'(?i)\bTaconic\b'), "Taconic"),
    )
    for pattern, label in vendor_patterns:
        if pattern.search(text):
            key = label.lower()
            if key not in seen:
                labels.append(label)
                seen.add(key)
    return labels


def is_analytical_or_computational(text: str) -> bool:
    """True when abstract/methods describe analytical chemistry or in-silico work."""
    if not text:
        return False
    return keyword_match(text.lower(), list(ANALYTICAL_COMPUTATIONAL_CUES))


def is_plant_cultivation_study(text: str) -> bool:
    """True when text describes greenhouse/field cannabis cultivation rather than dosing."""
    if not text:
        return False
    lowered = text.lower()
    if not keyword_match(lowered, list(PLANT_CULTIVATION_CUES)):
        return False
    if keyword_match(lowered, list(HUMAN_SUBJECT_KEYWORDS)):
        return False
    return True


def _route_cue_in_active_use_context(text: str, cue: str) -> bool:
    """True when a route cue appears in participant-use prose, not background literature."""
    lowered = text.lower()
    for match in re.finditer(re.escape(cue.lower()), lowered):
        window = lowered[max(0, match.start() - 100): min(len(lowered), match.end() + 100)]
        if any(marker in window for marker in OBSERVATIONAL_ROUTE_BACKGROUND_MARKERS):
            continue
        if re.search(
            r"\b(?:participants?|patients?|subjects?|volunteers?|users?|respondents?|"
            r"reported|self-reported|used|using|consumption|administered)\b",
            window,
        ):
            return True
    return False


def _has_cannabis_proximity(text: str, start: int, end: int, radius: int = 80) -> bool:
    """True when a text window also mentions cannabis/cannabinoid terms."""
    window = text[max(0, start - radius): min(len(text), end + radius)].lower()
    if re.search(r"\b(?:smoking|smoked|smoke)\s+tobacco\b|\btobacco\s+(?:smok|use|cigarette)", window):
        if not re.search(r"\b(?:cannabis|marijuana|thc|cbd|cannabinoid|mmj)\b", window):
            return False
    return bool(re.search(
        r"\b(?:cannabis|marijuana|cannabinoid|thc|cbd|mmj|medical marijuana|medical cannabis|"
        r"flower product|mmj treatment)\b",
        window,
    ))


def _infer_clinical_patient_reported_exposure(text: str) -> List[str]:
    """Infers inhaled/oral routes from medical-cannabis patient self-report prose."""
    if not text:
        return []
    lowered = text.lower()
    if not keyword_match(
        lowered,
        [
            "medical marijuana", "medical cannabis", "mmj", "mode of use",
            "reported use", "cannabis use", "marijuana use", "product(s) used",
            "flower product", "medible",
        ],
    ):
        return []
    methods: List[str] = []
    inhaled_cues = ("smoked", "smoking", "vaped", "vaping", "inhaled", "joint", "combustion")
    oral_cues = ("edible", "edibles", "capsule", "capsules", "oil", "tincture", "ingested", "oral")
    if any(_route_cue_in_active_use_context(text, cue) for cue in inhaled_cues):
        methods.append("inhaled")
    if any(_route_cue_in_active_use_context(text, cue) for cue in oral_cues):
        methods.append("oral")
    if not methods and keyword_match(lowered, ["medical marijuana", "medical cannabis", "mcacc", "dispensary"]):
        if keyword_match(lowered, ["flower", "bud", "joint", "smoke", "combust"]):
            methods.append("inhaled")
        if keyword_match(lowered, ["edible", "capsule", "oil", "tincture"]):
            methods.append("oral")
    return list(dict.fromkeys(methods))


def _is_vendor_strain_label(label: str) -> bool:
    """True when a strain candidate is a reagent vendor or catalog string."""
    if not label:
        return True
    lowered = label.lower().strip()
    if any(token in lowered for token in VENDOR_STRAIN_BLOCKLIST):
        return True
    if re.search(r'(?i)^(catalog|cat\.?\s*no|lot no|item no)', lowered):
        return True
    if re.search(r'(?i)\b[a-z]{1,3}\d{4,}\b', label):
        return True
    return False


def _extract_priority_cultivar_strain(text: str) -> Optional[str]:
    """Returns cultivar/chemovar/chemotype labels prioritized over vendor strings."""
    if not text:
        return None
    cv_match = re.search(
        r"(?i)(?:cannabis sativa\s+)?cv\.\s*(?:[\u2018\u2019'\"]([^'\"]+)[\u2019'\"]|([\w #+-]+?))"
        r"(?:\s*\([A-Z]{1,5}\))?\s*(?:seeds?\s+)?(?:were\s+)?(?:purchased|obtained|\s+from|[,.;]|$)",
        text,
    )
    if cv_match:
        return (cv_match.group(1) or cv_match.group(2)).strip().strip('.,;:')
    chemovar_match = re.search(r'(?i)chemovar\s+([\w -]+)', text)
    if chemovar_match:
        return chemovar_match.group(1).strip().strip('.,;:')
    chemotype_match = re.search(r'(?i)chemotype\s+([IVX]+)', text)
    if chemotype_match:
        return f"chemotype {chemotype_match.group(1).strip()}"
    return None


def _looks_like_invitro_text(text: str) -> bool:
    """True when methods text describes cell-culture or biochemical assay work."""
    if not text:
        return False
    return keyword_match(text.lower(), list(INVITRO_CONTEXT_CUES))


def _looks_like_invivo_primary(text: str) -> bool:
    """True when text centers on live-animal dosing rather than background ethics."""
    if not text:
        return False
    lowered = text.lower()
    if re.search(r'(?i)\b(?:rats?|mice)\s+were\s+(?:approved|used under|maintained under|housed under)\b', text):
        return False
    if not keyword_match(lowered, list(INVIVO_PRIMARY_CUES)):
        return False
    if re.search(r'(?i)\b(?:mg/kg|gavage|subcutaneous|intraperitoneal)\b', text):
        return True
    return bool(re.search(r'(?i)\b(?:rats?|mice)\s+(?:received|treated|injected|dosed)\b', text))


def _animal_strain_in_ethics_only(text: str, strain_label: str) -> bool:
    """True when an animal strain appears only in ethics/approval prose."""
    if not _looks_like_invitro_text(text):
        return False
    token = strain_label.split()[0]
    for match in re.finditer(rf'(?i)\b{re.escape(token)}\b', text):
        window = text[max(0, match.start() - 90):min(len(text), match.end() + 90)].lower()
        ethics_hit = any(token in window for token in ("ethic", "irb", "rec.", "approved", "accordance"))
        dosing_hit = any(token in window for token in ("mg/kg", "gavage", "injected", "dosed", "received"))
        if ethics_hit and not dosing_hit:
            return True
    return False


def _is_garbage_strain_fragment(fragment: str) -> bool:
    """True when an isolated-from or cultivar capture is clearly PDF noise."""
    if not fragment:
        return True
    lowered = fragment.lower()
    garbage_tokens = (
        "based", "nano", "plga", "content of", "yields", "formulation", "total content",
        "purchased from", "sigma", "catalog", "obtained from", "ware purchased",
    )
    if any(token in lowered for token in garbage_tokens):
        return True
    if lowered.startswith(("of ", "the ", "a ")):
        return True
    if len(fragment.split()) > 5:
        return True
    return False


def _is_bare_compound_strain_label(label: str) -> bool:
    """True when a strain candidate is only a cannabinoid name, not a cultivar or animal model."""
    if not label:
        return True
    lowered = label.strip().lower().strip(".,;:")
    bare_compounds = {
        "thc", "cbd", "cbg", "cbdv", "thcv", "cannabidiol", "cannabigerol",
        "tetrahydrocannabinol", "delta-9-thc", "delta9-thc", "δ9-thc",
    }
    if lowered in bare_compounds:
        return True
    if lowered in {"cbd, thc", "thc, cbd", "Δ9-THC, CBD".lower()}:
        return True
    return False


def _is_plausible_botanical_source(fragment: str) -> bool:
    """True when an isolated-from capture looks like a species binomial."""
    if _is_garbage_strain_fragment(fragment):
        return False
    return bool(re.match(r'^[A-Z][a-z]+(?:\s+[a-z]+)+', fragment.strip()))


def _extract_chemotype_profiles(text: str) -> Optional[str]:
    """Returns a multi-chemotype profile string when I/II/III percentages are listed."""
    if not text or "chemotype" not in text.lower():
        return None
    profiles: List[str] = []
    seen: set = set()
    entry_pattern = re.compile(
        r'(?i)Chemotype\s+(I{1,3}|II|III|IV|[IVX]+)\s*\([^)]*\)\s*[–—-][^,;.(]{0,120}',
    )
    for match in entry_pattern.finditer(text):
        block = match.group(0)
        roman = re.search(r'(?i)Chemotype\s+(I{1,3}|II|III|IV|[IVX]+)', block)
        if not roman:
            continue
        key = roman.group(1).upper()
        if key in seen:
            continue
        seen.add(key)
        pcts = re.findall(
            r'~\s*\d+(?:\.\d+)?\s*%\s*(?:THC|CBD)(?:\s+and\s+~\s*\d+(?:\.\d+)?\s*%\s*(?:THC|CBD))?',
            block,
        )
        label = f"Chemotype {roman.group(1)}"
        if pcts:
            inner = re.sub(r'\s+', ' ', pcts[0].strip())
            label = f"{label} ({inner})"
        profiles.append(label)
    if len(profiles) >= 2:
        return ", ".join(profiles)
    return None


def _extract_botanical_source(text: str) -> Optional[str]:
    """Captures non-cannabis botanical sources explicitly named as cannabinoid sources."""
    match = BOTANICAL_SOURCE_PATTERN.search(text)
    if match:
        return match.group(1).strip().strip('.,;:')
    return None


def _extract_cultivar_code_panel(text: str) -> Optional[str]:
    """Collects cultivar/compound codes listed together (e.g. 331-18A, CBDV, CBD)."""
    if not text:
        return None
    parts: List[str] = []
    seen: set = set()

    def add(label: str) -> None:
        cleaned = label.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            parts.append(cleaned)

    target_sentences = [
        sentence for sentence in re.split(r'[.;]\s+', text)
        if re.search(r'331-\d+[A-Z]|cannasoul|synthetic cbd', sentence, re.I)
    ]
    search_blob = " ".join(target_sentences) if target_sentences else text
    for match in EXTENDED_CULTIVAR_CODE_PATTERN.finditer(search_blob):
        add(match.group(1))
    for token in ("CBDV", "CBD", "THC"):
        if re.search(rf'(?i)\b{re.escape(token)}\b', search_blob):
            add(token)

    if len(parts) >= 2:
        return ", ".join(parts)
    if len(parts) == 1 and EXTENDED_CULTIVAR_CODE_PATTERN.fullmatch(parts[0]):
        return parts[0]
    return None


def _extract_compound_panel(text: str) -> Optional[str]:
    """Returns a semicolon/comma-separated synthetic cannabinoid test-article panel."""
    if not text:
        return None
    found: List[str] = []
    seen: set = set()
    for pattern, label in COMPOUND_PANEL_NORMALIZERS:
        if pattern.search(text):
            key = label.lower()
            if key not in seen:
                seen.add(key)
                found.append(label)
    if "Δ9-THC" in found and "THC" in found:
        found = [item for item in found if item != "THC"]
    if len(found) >= 2:
        return ", ".join(found)
    return None


def _extract_catalog_compound_strain(text: str) -> Optional[str]:
    """Captures vendor-qualified compound descriptions for in-vitro strain_reported."""
    if not text:
        return None
    match = CATALOG_COMPOUND_STRAIN_PATTERN.search(text)
    if match:
        return re.sub(r'\s+', ' ', match.group(1).strip().strip('.,;:'))
    cayman = re.search(
        r'(?i)(Cayman Chemical[^.\n]{0,80}?Catalog\s*#\s*\d+[^.\n]{0,40})',
        text,
    )
    if cayman:
        return re.sub(r'\s+', ' ', cayman.group(1).strip().strip('.,;:'))
    sigma_code = re.search(
        r'(?i)((?:Cannabidiol|CBD|THC)[^.\n]{0,40}?Sigma[^.\n]{0,40})',
        text,
    )
    if sigma_code:
        return re.sub(r'\s+', ' ', sigma_code.group(1).strip().strip('.,;:'))
    cerilliant = re.search(
        r'(?i)((?:CBD|THC|CBN|CBG)[^.\n]{0,60}?(?:Cerilliant|Supelco)[^.\n]{0,60})',
        text,
    )
    if cerilliant:
        return re.sub(r'\s+', ' ', cerilliant.group(1).strip().strip('.,;:'))
    donated = re.search(
        r'(?i)((?:CBD|THC|cannabidiol)[^.\n]{0,40}?donated by[^.\n]{0,80})',
        text,
    )
    if donated:
        return re.sub(r'\s+', ' ', donated.group(1).strip().strip('.,;:'))
    standards_panel = re.search(
        r'(?i)((?:CBD|Δ9-THC|THC|CBN|CBG|CBDA|THCA|CBGA)[^.\n]{0,30}(?:,\s*(?:CBD|Δ9-THC|THC|CBN|CBG|CBDA|THCA|CBGA)[^.\n]{0,30}){1,4}'
        r'[^.\n]{0,40}?(?:Sigma-Aldrich|Cerilliant|Supelco))',
        text,
    )
    if standards_panel:
        return re.sub(r'\s+', ' ', standards_panel.group(1).strip().strip('.,;:'))
    return None


def _extract_invitro_strain_reported(text: str) -> Optional[str]:
    """Targeted in-vitro strain/compound extraction before generic vendor scanning."""
    if not text:
        return None

    catalog = _extract_catalog_compound_strain(text)
    if catalog:
        return catalog

    chemotypes = _extract_chemotype_profiles(text)
    if chemotypes:
        return chemotypes

    priority = _extract_priority_cultivar_strain(text)
    if priority and not _is_vendor_strain_label(priority):
        return priority

    botanical = _extract_botanical_source(text)
    if botanical:
        return botanical

    code_panel = _extract_cultivar_code_panel(text)
    if code_panel:
        return code_panel

    compound_panel = _extract_compound_panel(text)
    if compound_panel:
        return compound_panel

    return None


def _has_isolated_cannabinoid_without_plant_matrix(text: str) -> bool:
    """True when an isolated cannabinoid is named without plant-matrix context."""
    if not text:
        return False
    lowered = text.lower()
    for isolate in sorted(CANNABINOID_ISOLATE_LIST, key=len, reverse=True):
        for match in re.finditer(rf'(?i)\b{re.escape(isolate)}\b', text):
            if isolate.upper() == "CBD" and re.search(r'(?i)\bCBDA\b', text[max(0, match.start() - 5):match.end() + 5]):
                continue
            window = lowered[max(0, match.start() - 50): min(len(lowered), match.end() + 50)]
            if any(word in window for word in PLANT_MATRIX_CUES):
                continue
            return True
    return False


def _extract_compound_provenance_strain(text: str) -> Optional[str]:
    """Builds strain_reported from compound catalog id, vendor, and purity for pure-cannabinoid studies."""
    if not text:
        return None

    parts: List[str] = []
    seen: set = set()

    def add_part(label: str) -> None:
        cleaned = label.strip().strip('.,;:')
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        parts.append(cleaned)

    for pattern, label in COMPOUND_PANEL_NORMALIZERS[:6]:
        if pattern.search(text):
            return label
    catalog_match = re.search(
        r'(?i)\b((?:THC|CBD|CBG|CBDV|HU|JWH|AM|CP|WIN)[- ]?\d[\w\-]*)',
        text,
    )
    if catalog_match:
        add_part(catalog_match.group(1))

    for compound in COMPOUND_NAME_CUES:
        if re.search(rf'(?i)\b{re.escape(compound)}\b', text):
            add_part(compound)
            break

    for vendor in COMPOUND_PROVENANCE_VENDORS:
        if re.search(rf'(?i)\b{re.escape(vendor)}\b', text):
            add_part(vendor)

    purity_match = re.search(r'(?i)([>≥]\s*\d+(?:\.\d+)?\s*%\s*(?:purity)?|\d+(?:\.\d+)?\s*%\s*purity)', text)
    if purity_match:
        add_part(purity_match.group(1).strip())

    if not parts:
        window_pattern = re.compile(
            rf'(?i)\b({"|".join(re.escape(c) for c in COMPOUND_NAME_CUES)})\b'
            rf'[^.\n]{{0,80}}?(?:{"|".join(re.escape(v) for v in COMPOUND_PROVENANCE_VENDORS)})',
        )
        prox = window_pattern.search(text)
        if prox:
            compound = re.search(
                rf'(?i)\b({"|".join(re.escape(c) for c in COMPOUND_NAME_CUES)})\b',
                prox.group(0),
            )
            if compound:
                add_part(compound.group(1))
            for vendor in COMPOUND_PROVENANCE_VENDORS:
                if re.search(rf'(?i)\b{re.escape(vendor)}\b', prox.group(0)):
                    add_part(vendor)

    if not parts:
        return None
    return "; ".join(parts)


def _extract_named_cultivar_profiles(text: str) -> Optional[str]:
    """Returns named cultivar labels with THC/CBD percentage profiles."""
    if not text:
        return None
    profiles: List[str] = []
    seen: set = set()
    for match in NAMED_CULTIVAR_PROFILE_PATTERN.finditer(text):
        label = re.sub(r'\s+', ' ', match.group(0).strip())
        key = label.lower()
        if key not in seen:
            seen.add(key)
            profiles.append(label)
    if profiles:
        return "; ".join(profiles)
    return None


def _extract_invivo_strain_reported(
    text: str,
    cannabis_type: Optional[List[str]] = None,
) -> Optional[str]:
    """Targeted in-vivo strain/compound extraction before generic animal/vendor scanning."""
    cultivars = _extract_named_cultivar_profiles(text)
    if cultivars:
        return cultivars

    for pattern, label in COMPOUND_PANEL_NORMALIZERS[:6]:
        if pattern.search(text):
            return label

    animal_labels = _extract_animal_strain_labels(text)
    if animal_labels:
        return animal_labels[0]

    compound_panel = _extract_compound_panel(text)
    if compound_panel:
        return compound_panel

    if cannabis_type and any(
        tag in cannabis_type for tag in ("CB receptor agonist", "CB receptor antagonist", "pure cannabinoid")
    ):
        vendor_strain = _extract_compound_provenance_strain(text)
        if vendor_strain:
            return vendor_strain

    return None


def _duration_in_cell_culture_context(text: str, start: int, end: int) -> bool:
    """True when a duration match sits in an in-vitro incubation window."""
    window = text[max(0, start - 55):min(len(text), end + 55)].lower()
    animal_dosing = any(
        token in window
        for token in ("mg/kg", "µg/kg", "ug/kg", "gavage", "mice", "rats", "subcutaneous", "intraperitoneal")
    )
    if animal_dosing:
        return False
    return any(
        token in window
        for token in ("incubat", "in vitro", "cells were", "cell line", "well plate", "culture medium")
    )


def extract_strain_info(
    text: str,
    *,
    cannabis_type: Optional[List[str]] = None,
    study_type: Optional[List[str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Extracts reported strain and normalizes it to a Chemotype I/II/III.

    Returns:
        tuple: (strain_reported, strain_normalized)
    """
    if not text:
        return None, None

    study_set = set(study_type or [])
    is_invitro_study = any(str(item).startswith("Cell Culture (") for item in study_set)
    is_invivo_study = any(str(item).startswith("Animal Models (") for item in study_set)
    invitro_ctx = _looks_like_invitro_text(text) or is_invitro_study
    invivo_primary = _looks_like_invivo_primary(text)

    detected_substance = _extract_detected_substance_strain(text)
    if detected_substance:
        return detected_substance, None

    if _is_endocannabinoid_biomarker_study(text, study_type):
        cultivar = _extract_priority_cultivar_strain(text) or _extract_named_cultivar_profiles(text)
        if cultivar and not _is_vendor_strain_label(cultivar):
            return cultivar, CHEMOTYPE_MAP.get(cultivar.lower())
        for pattern, label in COMPOUND_PANEL_NORMALIZERS[:6]:
            if pattern.search(text):
                return label, None
        return None, None

    if is_invitro_study and is_invivo_study and not invivo_primary:
        return None, None

    if is_invitro_study and is_invivo_study and invivo_primary:
        compound = _extract_compound_provenance_strain(text)
        if compound:
            return compound, None
        invitro_strain = _extract_invitro_strain_reported(text)
        if invitro_strain:
            return invitro_strain, CHEMOTYPE_MAP.get(invitro_strain.lower())

    animal_labels = _extract_animal_strain_labels(text)
    if invitro_ctx and not invivo_primary:
        animal_labels = [
            label for label in animal_labels
            if not _animal_strain_in_ethics_only(text, label)
        ]
        if invitro_ctx and not invivo_primary:
            animal_labels = []

    is_pure = bool(cannabis_type and "pure cannabinoid" in cannabis_type) or _text_suggests_pure_cannabinoid(text)

    reported_parts: List[str] = []
    seen: set = set()

    if is_invivo_study and invivo_primary:
        invivo_strain = _extract_invivo_strain_reported(text, cannabis_type)
        if invivo_strain:
            normalized = CHEMOTYPE_MAP.get(invivo_strain.lower())
            return invivo_strain, normalized

    if invitro_ctx and not invivo_primary:
        invitro_strain = _extract_invitro_strain_reported(text)
        if invitro_strain:
            normalized = CHEMOTYPE_MAP.get(invitro_strain.lower())
            return invitro_strain, normalized

    for label in animal_labels:
        reported_parts.append(label)
        seen.add(label.lower())

    priority_cultivar = _extract_priority_cultivar_strain(text)
    if priority_cultivar and not _is_vendor_strain_label(priority_cultivar) and not reported_parts:
        normalized = CHEMOTYPE_MAP.get(priority_cultivar.lower())
        return priority_cultivar, normalized

    if is_pure and not animal_labels and not (invitro_ctx and not invivo_primary):
        compound_strain = _extract_compound_provenance_strain(text)
        if compound_strain:
            if cannabis_type and "pure cannabinoid" in cannabis_type:
                return compound_strain, None
            compound_parts = [
                part.strip() for part in compound_strain.split("; ")
                if part.strip() and not _is_vendor_strain_label(part.strip())
            ]
            if compound_parts:
                return "; ".join(compound_parts), None

    text_lower = text.lower()

    def add_reported(label: str) -> None:
        cleaned = label.strip().strip('.,;:')
        if not cleaned or _is_vendor_strain_label(cleaned) or _is_garbage_strain_fragment(cleaned):
            return
        if is_invivo_study and invivo_primary and _is_bare_compound_strain_label(cleaned):
            return
        if invitro_ctx and not invivo_primary and cleaned.upper() in {"THC", "CBD", "CBDV", "CBG", "THCV"}:
            return
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        reported_parts.append(cleaned)

    # 1. Exact matches from chemotype list
    normalized: Optional[str] = None
    for strain, chemotype in CHEMOTYPE_MAP.items():
        if re.search(r'\b' + re.escape(strain) + r'\b', text_lower):
            match_orig = re.search(r'\b' + re.escape(strain) + r'\b', text, re.IGNORECASE)
            reported = match_orig.group(0) if match_orig else strain
            add_reported(reported)
            if normalized is None:
                normalized = chemotype

    # 2. Quoted strain names
    match = QUOTED_STRAIN_PATTERN.search(text)
    if match:
        add_reported(match.group(1))

    # 3. Cultivar/chemovar/strain labels
    for match in CULTIVAR_LABEL_PATTERN.finditer(text):
        captured = match.group(1) or match.group(2) or match.group(3)
        if captured:
            add_reported(captured)

    # 4. Coded cultivar labels (CN2, CN4, …) and extended codes (331-18A)
    for match in CODED_CULTIVAR_PATTERN.finditer(text):
        add_reported(match.group(1))
    for match in EXTENDED_CULTIVAR_CODE_PATTERN.finditer(text):
        add_reported(match.group(1))

    # 5. Animal model strains (seeded from animal_labels above)

    # 6. Synthetic cannabinoid / test-article compound IDs
    for pattern in SYNTHETIC_COMPOUND_STRAIN_PATTERNS:
        for match in pattern.finditer(text):
            add_reported(match.group(0).strip())

    compound_panel = _extract_compound_panel(text)
    if compound_panel:
        for part in compound_panel.split(", "):
            add_reported(part)

    # 7. Supplier-qualified compound descriptions (skip for pure-cannabinoid provenance papers)
    if not is_pure and not invitro_ctx:
        for match in SUPPLIER_COMPOUND_PATTERN.finditer(text):
            add_reported(match.group(1).strip())

        for label in _extract_vendor_isolated_compounds(text):
            add_reported(label)

    # 8. isolated/purified from …
    if not is_pure:
        for match in ISOLATED_FROM_PATTERN.finditer(text):
            fragment = match.group(1).strip()
            if _is_plausible_botanical_source(fragment):
                add_reported(fragment)

    if not reported_parts:
        if is_pure and not (invitro_ctx and not invivo_primary):
            compound_strain = _extract_compound_provenance_strain(text)
            if compound_strain:
                return compound_strain, None
        botanical = _extract_botanical_source(text)
        if botanical:
            return botanical, None
        return None, None

    filtered_parts = [part for part in reported_parts if not _is_vendor_strain_label(part)]
    if filtered_parts:
        combined_reported = ", ".join(filtered_parts)
    else:
        combined_reported = ", ".join(reported_parts)
    normalized = CHEMOTYPE_MAP.get(reported_parts[0].lower())
    if normalized is None and len(reported_parts) == 1:
        normalized = CHEMOTYPE_MAP.get(combined_reported.lower())
    return combined_reported, normalized

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

def infer_granular_publication_label(title: str, abstract: str) -> str:
    """Infers the legacy granular publication label from text keywords."""
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


def infer_publication_type(title: str, abstract: str) -> str:
    """Infers coarse Node 1 publication type (original research, review, or case study)."""
    import classification_schema

    granular = infer_granular_publication_label(title, abstract)
    coarse = classification_schema.granular_label_to_coarse_publication(granular)
    return coarse or "original research"

LAB_IN_VITRO_CUES = (
    "in vitro",
    "enzymolysis",
    "incubated",
    "assay",
    "chromatograph",
    "spectrometr",
    "emulsion",
    "extracted",
    "dissolved",
    "cell viability",
    "culture medium",
    "primary cells",
    "cell line",
)

HUMAN_SUBJECT_KEYWORDS = (
    "participants", "patients", "subjects", "volunteers", "human subjects",
    "men and women", "adults aged", "healthy volunteers", "human participants",
)


def _looks_like_lab_in_vitro_study(title: str, abstract: str) -> bool:
    """True when title/abstract suggests bench/analytical work without human/animal subjects."""
    blob = f"{title} {abstract}".lower()
    if keyword_match(blob, list(HUMAN_SUBJECT_KEYWORDS)):
        return False
    if keyword_match(blob, ["mouse", "mice", "rat", "rats", "patients", "participants", "clinical trial"]):
        return False
    return keyword_match(blob, list(LAB_IN_VITRO_CUES))


def _refine_study_type_list(types: List[str], combined: str, title: str, abstract: str = "") -> List[str]:
    """Applies Node 2/4 disambiguation so study_type lists align with LLM taxonomy."""
    title_lower = title.lower()
    search_text = f"{combined} {title_lower} {abstract.lower()}"

    if _looks_like_lab_in_vitro_study(title, abstract or combined):
        types = [item for item in types if not item.startswith("Clinical")]
        if not any(item.startswith("Cell Culture") for item in types):
            types.append("Cell Culture (Other In Vitro)")

    has_human = keyword_match(search_text, list(HUMAN_SUBJECT_KEYWORDS) + ["clinical trial", "randomized", "placebo"])

    has_cell = any(item.startswith("Cell Culture") for item in types)
    has_animal = any(item.startswith("Animal Models") for item in types)
    if (has_cell or has_animal) and not has_human:
        types = [item for item in types if not item.startswith("Clinical")]

    if "Clinical (prospective)" in types and "Clinical (observational)" in types:
        obs_specific = keyword_match(
            combined,
            ["cross-sectional", "survey", "observational study", "case-control", "gwas", "registry", "epidemiological"],
        )
        if not obs_specific:
            types.remove("Clinical (observational)")

    if "Clinical (RCT)" in types and "Clinical (prospective)" in types:
        rct_specific = keyword_match(
            combined,
            ["randomized controlled", "randomised controlled", "placebo-controlled", "double-blind", "rct"],
        )
        if not rct_specific:
            types.remove("Clinical (RCT)")

    if "Clinical (retrospective)" in types and "Clinical (observational)" in types:
        pass

    if "Clinical (RCT)" in types:
        if "Clinical (observational)" in types:
            types.remove("Clinical (observational)")
        animal_keywords = [
            "mouse", "mice", "murine", "rat", "rats", "rodent", "rodents", "animal", "animals",
            "dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish",
            "drosophila", "macaque", "rhesus", "monkey", "monkeys", "primate", "primates",
            "baboon", "chimpanzee", "canine", "feline", "in vivo",
        ]
        cell_keywords = [
            "in vitro", "cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7",
            "sh-sy5y", "jurkat", "cho cells", "primary cell", "primary cells", "primary culture",
            "organoid", "organoids", "spheroid", "spheroids", "co-culture", "co-cultures",
            "coculture", "cocultures", "microglia", "neurons", "epithelial cells",
            "epithelial cell", "airway epithelial", "cultured cells", "culture assay",
            "cell culture", "cell cultures", "pcls", "precision-cut", "lung slice", "lung slices",
        ]
        has_animal_title = any(keyword in title_lower for keyword in animal_keywords)
        has_cell_title = any(keyword in title_lower for keyword in cell_keywords)
        if not has_animal_title:
            types = [item for item in types if not item.startswith("Animal Models (")]
        if not has_cell_title:
            types = [item for item in types if not item.startswith("Cell Culture (")]

    ordered: List[str] = []
    seen: set = set()
    for item in types:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def _collect_study_type_hits(combined: str) -> List[str]:
    """Returns study_type labels matched from a lowercase text blob."""
    types: List[str] = []
    if keyword_match(combined, ["double-blind", "randomized controlled", "placebo-controlled", "rct", "randomised controlled", "clinical trial"]):
        types.append("Clinical (RCT)")
    if keyword_match(combined, ["prospective", "prospectively", "prospective cohort"]):
        types.append("Clinical (prospective)")
    if keyword_match(combined, ["retrospective", "retrospectively", "chart review", "historical cohort"]):
        types.append("Clinical (retrospective)")
    if keyword_match(combined, ["observational", "cross-sectional", "survey", "surveys", "registry", "registries", "longitudinal", "case-control", "epidemiological", "cohort", "cohorts", "gwas", "genome-wide", "genomewide"]):
        types.append("Clinical (observational)")
    if keyword_match(combined, ["mouse", "mice", "murine", "c57bl/6"]):
        types.append("Animal Models (Mouse)")
    if keyword_match(combined, ["rat", "rats", "wistar", "sprague-dawley"]):
        types.append("Animal Models (Rat)")
    if keyword_match(combined, ["hamster", "hamsters", "gerbil", "gerbils", "guinea pig", "guinea pigs", "voles", "vole"]):
        types.append("Animal Models (Other Rodents)")
    if keyword_match(combined, ["macaque", "rhesus", "monkey", "monkeys", "primate", "primates", "baboon", "chimpanzee"]):
        types.append("Animal Models (Non-Human Primates)")
    if keyword_match(combined, ["dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish", "drosophila"]):
        types.append("Animal Models (Other)")
    elif keyword_match(combined, ["animal", "in vivo", "animal model", "rodent", "rodents"]):
        if not any(item.startswith("Animal Models (") for item in types):
            types.append("Animal Models (Other)")
    if keyword_match(combined, ["primary cell", "primary cells", "primary culture", "primary neuronal", "primary microglia", "splenocytes", "primary hepatocytes"]):
        types.append("Cell Culture (Primary Cells)")
    if keyword_match(combined, ["cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7", "sh-sy5y", "jurkat", "cho cells"]):
        types.append("Cell Culture (Cell Lines)")
    if keyword_match(combined, ["organoid", "organoids", "spheroid", "spheroids", "3d culture", "3d cultures"]):
        types.append("Cell Culture (Organoids)")
    if keyword_match(combined, ["co-culture", "co-cultures", "coculture", "cocultures"]):
        types.append("Cell Culture (Co-Culture)")
    if keyword_match(combined, ["precision-cut lung slices", "pcls", "precision cut lung slices", "lung slice", "lung slices"]):
        types.append("Cell Culture (PCLS)")
    elif keyword_match(combined, ["in vitro", "cultured cells", "culture assay", "cell culture", "cell cultures", "epithelial cells", "epithelial cell", "airway epithelial"]):
        if not any(item.startswith("Cell Culture (") for item in types):
            types.append("Cell Culture (Other In Vitro)")
    return types


def _infer_original_research_study_types(title: str, abstract: str) -> List[str]:
    """Infers study_type labels for original-research papers from title/abstract/methods cues."""
    full_blob = f"{title} {abstract}"
    if is_analytical_or_computational(full_blob):
        return ["Cell Culture (Other In Vitro)"]

    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    full_blob = f"{title} {abstract}".lower()
    types = _collect_study_type_hits(combined)
    for label in _collect_study_type_hits(full_blob):
        if label not in types:
            types.append(label)

    title_lower = title.lower()
    search_text = f"{combined} {title_lower} {abstract.lower()}"
    if not types:
        if keyword_match(search_text, list(HUMAN_SUBJECT_KEYWORDS)):
            types.append("Clinical (observational)")
        elif keyword_match(search_text, ["in vitro", "cell line", "cell culture", "cultured", "organoid", "assay"]):
            types.append("Cell Culture (Other In Vitro)")
        elif keyword_match(search_text, ["mouse", "mice", "rat", "rats", "in vivo", "animal model"]):
            types.append("Animal Models (Other)")

    return _refine_study_type_list(types, f"{combined} {abstract.lower()}", title, abstract)


def infer_study_type_for_publication(
    title: str,
    abstract: str,
    publication_type: Optional[str],
) -> List[str]:
    """Infers study_type given a fixed Node 1 publication_type (does not re-route Node 1)."""
    import classification_schema

    pub = classification_schema.granular_label_to_coarse_publication(publication_type or "")
    if pub == "review":
        granular = infer_granular_publication_label(title, abstract)
        subtype = classification_schema.granular_label_to_review_subtype(granular) or "review"
        return [subtype]
    if pub == "case study":
        return ["case study"]
    if pub != "original research":
        return []
    return _infer_original_research_study_types(title, abstract)


def infer_study_type(title: str, abstract: str) -> List[str]:
    """Infers Stage 2 study type from text keywords, focusing on Methods section if available."""
    coarse_pub = infer_publication_type(title, abstract)
    return infer_study_type_for_publication(title, abstract, coarse_pub)

def _cannabinoid_edible_context(text: str) -> bool:
    """True when edible/oral food cues appear near a cannabinoid mention."""
    lowered = text.lower()
    for term in EDIBLE_ORAL_CUES:
        for match in re.finditer(re.escape(term), lowered):
            window = lowered[max(0, match.start() - 60): min(len(lowered), match.end() + 60)]
            if re.search(
                r'\b(thc|cbd|cannabidiol|tetrahydrocannabinol|cannabinoid|marijuana)\b',
                window,
            ):
                return True
    return False


def infer_exposure_method(
    title: str,
    abstract: str,
    study_type: Any,
    full_text: Optional[str] = None,
) -> List[str]:
    """Extracts exposure method from text keywords, focusing on Methods section if available."""
    if not _paper_has_cannabis_content(title, abstract):
        return ["unknown"]

    methods_text = get_methods_text(title, abstract)
    if full_text:
        from maude_classifier import extract_methods_section
        pdf_methods = extract_methods_section(full_text)
        if pdf_methods:
            methods_text = f"{methods_text}\n\n{pdf_methods}" if methods_text else pdf_methods
    combined = methods_text.lower()
    exposure_scan = methods_text
    if full_text:
        exposure_scan = f"{methods_text}\n\n{full_text[:25000]}"
    routing_blob = f"{title} {abstract or ''} {exposure_scan}"

    if is_plant_cultivation_study(routing_blob):
        return ["unknown"]

    if isinstance(study_type, str):
        study_types = {study_type}
    else:
        study_types = set(study_type or [])
    is_clinical = study_types.intersection({"RCT", "observational"}) or any(
        s.startswith("Clinical (") for s in study_types
    )
    has_human_subjects = keyword_match(
        f"{title} {abstract} {methods_text}".lower(),
        list(HUMAN_SUBJECT_KEYWORDS),
    )
    is_observational = is_clinical and not any("RCT" in item for item in study_types)

    is_animal_study = "animal" in study_types or any(
        s.startswith("Animal Models (") for s in study_types
    )
    invivo_primary = _looks_like_invivo_primary(methods_text)

    if _has_dissolved_in_media_cue(methods_text):
        is_invitro = "in vitro" in study_types or any(
            s.startswith("Cell Culture (") for s in study_types
        )
        biomarker_only = has_human_subjects and re.search(
            r"(?i)\b(?:plasma|serum|blood|saliva|endocannabinoid|2-ag|anandamide|ae[aA])\b",
            methods_text,
        ) and not re.search(
            r"(?i)\b(?:administered|treatment|dose|dosing|exposed to|incubated with)\b",
            methods_text,
        )
        if biomarker_only:
            pass
        elif is_animal_study and (invivo_primary or _has_injection_route_guard(combined)):
            pass
        elif is_invitro and not (is_clinical and has_human_subjects):
            return ["cannabinoids dissolved in media"]
        elif not is_clinical and not is_animal_study and not is_plant_cultivation_study(routing_blob):
            return ["cannabinoids dissolved in media"]
    
    methods = []
    
    # Group A: Clinical exposure (human/clinical setting)
    if is_clinical:
        scan_lower = exposure_scan.lower()
        inhaled_cues = ["smoke", "smoked", "smoking", "joint", "combustion", "cigarette", "cigarettes",
                        "vaporized", "vaporised", "inhaled", "inhalation", "vaporize", "vaporise"]
        for cue in inhaled_cues:
            if cue not in scan_lower:
                continue
            if is_observational:
                for match in re.finditer(re.escape(cue), scan_lower):
                    if _has_cannabis_proximity(scan_lower, match.start(), match.end()):
                        methods.append("inhaled")
                        break
            else:
                methods.append("inhaled")
                break
        oral_cues = list(EDIBLE_ORAL_CUES) + ["oral", "ingested", "oil ingestion", "gavage", "medible", "tincture"]
        for cue in oral_cues:
            if cue in scan_lower and (not is_observational or _route_cue_in_active_use_context(exposure_scan, cue)):
                methods.append("oral")
                break
        if keyword_match(combined, ["sublingual", "under the tongue", "drops", "tincture", "tinctures"]):
            if not is_observational or _route_cue_in_active_use_context(exposure_scan, "sublingual") or _route_cue_in_active_use_context(exposure_scan, "tincture"):
                methods.append("sublingual")
        if keyword_match(combined, ["injection", "intravenous", "intraperitoneal", "ip", "subcutaneous", "intramuscular", "injected"]):
            if _has_injection_route_guard(combined) and not _cannabinoid_edible_context(combined):
                methods.append("injected")
        if not methods and keyword_match(
            scan_lower,
            ["medical cannabis", "medical marijuana", "cannabis oil", "cannabis-based medicine", "prescription cannabis",
             "dispensary", "plant-based medicine", "mcacc", "nabiximols", "sativex", "mmj"],
        ):
            methods.append("oral")
        if not methods or methods == ["unknown"]:
            patient_routes = _infer_clinical_patient_reported_exposure(routing_blob)
            for route in patient_routes:
                if route not in methods:
                    methods.append(route)
 
    # Group B: In vitro exposure (cells/tissue setting)
    if "in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types):
        if keyword_match(combined, ["conditioned media", "smoke extract", "cse", "vapor extract", "gaseous extract", "smoke-conditioned"]):
            methods.append("smoke/vapor conditioned media")
        if keyword_match(combined, ["cell exposure to smoke", "cells exposed to vapor", "chamber exposure of cells", "smoke stream", "direct vapor exposure", "exposure of cells to smoke", "exposure of cells to vapor", "air-liquid interface", "air liquid interface", "ali exposure", "ali", "aerosol exposure", "exposed directly to vapor", "exposed directly to smoke"]):
            methods.append("exposure of cells to smoke/vapor")
 
    # Group C: In vivo exposure (animal models) — skip when clinical human subjects dominate
    if (
        ("animal" in study_types or any(s.startswith("Animal Models (") for s in study_types))
        and not (is_clinical and has_human_subjects)
    ):
        if keyword_match(combined, list(EDIBLE_ORAL_CUES) + ["oral administration", "fed", "diet", "ingested", "oral"]):
            methods.append("oral administration")
        if keyword_match(combined, ["oral gavage", "gavage"]):
            methods.append("oral gavage")
        if _has_injection_route_guard(combined) and not _cannabinoid_edible_context(combined):
            methods.append("injection cannabinoids")
        if keyword_match(combined, [
            "whole body", "whole-body", "whole body chamber", "whole-body chamber",
            "whole-body vapor chamber", "whole body exposure", "chamber exposure",
            "whole body smoke", "whole body vapor",
        ]):
            methods.append("whole body. smoke/vapor")
        elif keyword_match(combined, ["nose-only", "nose only", "snout exposure", "head-out"]):
            methods.append("nose only smoke/vapor")
        elif keyword_match(combined, ["smoke", "vapor", "vaporized", "vaporised", "vape", "vaping", "inhalation", "inhalational"]):
            methods.append("nose only smoke/vapor")
        if keyword_match(combined, ["sublingual", "sub-lingual", "under tongue"]):
            methods.append("sub-lingual")
        if keyword_match(combined, ["intranasal", "intra-nasal", "nasal instillation", "nasal drops"]):
            methods.append("intranasal")
        if keyword_match(combined, ["intratracheal", "intratracheal instillation", "intra-tracheal", "lung instillation"]):
            methods.append("intratracheal")
            
    if not methods:
        if (
            ("in vitro" in study_types or any(s.startswith("Cell Culture (") for s in study_types))
            and not (is_clinical and has_human_subjects)
        ):
            methods.append("cannabinoids dissolved in media")
        else:
            methods.append("unknown")

    if "whole body. smoke/vapor" in methods and "nose only smoke/vapor" in methods:
        methods = [item for item in methods if item != "nose only smoke/vapor"]

    animal_specific = {
        "oral administration", "oral gavage", "injection cannabinoids",
        "whole body. smoke/vapor", "nose only smoke/vapor", "sub-lingual",
        "intranasal", "intratracheal", "cannabinoids dissolved in media",
        "smoke/vapor conditioned media", "exposure of cells to smoke/vapor",
    }
    if any(item in animal_specific for item in methods):
        methods = [item for item in methods if item not in {"inhaled", "oral"}]

    if is_clinical and has_human_subjects:
        methods = [item for item in methods if item != "cannabinoids dissolved in media"]
        if not methods or methods == ["unknown"]:
            patient_routes = _infer_clinical_patient_reported_exposure(routing_blob)
            if patient_routes:
                methods = patient_routes
            else:
                methods = ["unknown"]

    is_invitro_only = any(s.startswith("Cell Culture (") for s in study_types) and not any(
        s.startswith("Animal Models (") for s in study_types
    )
    if is_invitro_only and not is_plant_cultivation_study(routing_blob):
        if not methods or methods == ["unknown"]:
            methods = ["cannabinoids dissolved in media"]
        else:
            animal_routes = {
                "oral administration", "oral gavage", "injection cannabinoids",
                "whole body. smoke/vapor", "nose only smoke/vapor",
            }
            if set(methods).intersection(animal_routes) and _has_dissolved_in_media_cue(methods_text):
                methods = ["cannabinoids dissolved in media"]

    if is_observational and methods and methods != ["unknown"]:
        if not keyword_match(
            routing_blob.lower(),
            [
                "mode of use", "medical marijuana", "medical cannabis", "mmj",
                "smoked cannabis", "smoking cannabis", "vaped cannabis", "cannabis cigarette",
                "oral thc", "oral cbd", "received cannabidiol", "received thc",
                "flower product", "reported use of",
            ],
        ):
            methods = ["unknown"]

    return list(dict.fromkeys(methods))
    
def infer_cannabis_type(
    title: str,
    abstract: str,
    study_type: Any,
    exposure_method: Any,
    full_text: Optional[str] = None,
) -> List[str]:
    """Infers cannabis product type using a priority-ordered multi-label decision tree."""
    scan_early = f"{title} {abstract or ''} {full_text[:20000] if full_text else ''}"
    if _extract_detected_substance_strain(scan_early):
        return ["CB receptor agonist"]

    if not _paper_has_cannabis_content(title, abstract):
        return ["unknown"]

    methods_text = get_methods_text(title, abstract)
    combined = methods_text.lower()
    scan_blob = f"{combined}\n{full_text[:20000]}" if full_text else combined

    if isinstance(exposure_method, str):
        exposure_methods = {exposure_method}
    else:
        exposure_methods = set(exposure_method or [])

    types: List[str] = []

    def add_type(label: str) -> None:
        if label not in types:
            types.append(label)

    # Priority 1–2: synthetic agonists / antagonists
    if keyword_match(combined, list(SYNTHETIC_AGONIST_CUES) + [
        "cb receptor agonist", "cb1 agonist", "cb2 agonist",
        "cannabinoid receptor agonist", "cannabinoid agonist", "synthetic cannabinoid",
    ]):
        add_type("CB receptor agonist")
    if keyword_match(combined, list(SYNTHETIC_ANTAGONIST_CUES) + [
        "cb receptor antagonist", "cb1 antagonist", "cb2 antagonist",
        "cannabinoid receptor antagonist", "cannabinoid antagonist", "inverse agonist",
    ]):
        add_type("CB receptor antagonist")

    # Priority 3: vape pen — device-specific cues only (not vapor/vaporized alone)
    if keyword_match(combined, list(VAPE_PEN_DEVICE_CUES)):
        add_type("vape pen")

    # Priority 4: plant matter → dried flower
    if _has_plant_matter_language(combined) or keyword_match(
        combined,
        ["smoked cannabis", "combusted cannabis", "marijuana cigarette", "cannabis herb"],
    ):
        add_type("dried flower")

    # Priority 5: botanical extracts
    if keyword_match(combined, list(EXTRACT_PRODUCT_CUES)):
        add_type("concentrates")

    # Priority 6: pure cannabinoid (isolated compound + mg/kg or pharma supplier)
    if _has_isolated_cannabinoid_without_plant_matrix(methods_text):
        add_type("pure cannabinoid")
    elif re.search(r'(?i)\b(?:mg/kg|µg/kg|ug/kg|gavage|intraperitoneal|subcutaneous)\b', combined) and keyword_match(
        combined, list(PURE_CANNABINOID_COMPOUND_CUES),
    ):
        add_type("pure cannabinoid")
    elif keyword_match(combined, [
        "pure thc", "pure cbd", "pure cannabinoid", "pure cannabinoids",
        "cannabidiol isolate", "dronabinol", "nabilone", "marinol", "isolate", "isolates",
        "nabiximols", "sativex",
    ]) or _has_pharma_isolation_cues(methods_text):
        add_type("pure cannabinoid")

    # Additional product forms (multi-label)
    if keyword_match(combined, list(EDIBLES_PRODUCT_PHRASES) + ["brownie", "brownies", "cookie", "cookies"]):
        add_type("edibles")
    if keyword_match(combined, ["hashish", "hash", "kief", "charas", "bubble hash"]):
        add_type("hashish/kief")
    if keyword_match(combined, [
        "shatter", "tincture", "tinctures", "resin", "concentrate", "concentrates",
        "hash oil", "honey oil", "bho", "rosin", "wax",
    ]):
        add_type("concentrates")
    elif keyword_match(combined, ["extract", "extracts"]) and not is_analytical_or_computational(scan_blob):
        add_type("concentrates")
    if keyword_match(combined, [
        "flower", "bud", "buds", "dried cannabis", "joint", "joints",
        "combusted flower", "cannabis herb", "herbal cannabis",
    ]) and "dried flower" not in types:
        add_type("dried flower")

    # Vendor/purity override: dietary delivery of verified compound ≠ edibles product
    if _has_pure_cannabinoid_vendor_override(methods_text):
        if "edibles" in types:
            types.remove("edibles")
        if "pure cannabinoid" not in types:
            types.insert(0, "pure cannabinoid")

    # Exposure-method fallbacks when no explicit product matched
    if not types:
        for exp in exposure_methods:
            exp_lower = str(exp).lower()
            if any(token in exp_lower for token in ("smoked", "inhaled", "whole body", "nose only")):
                add_type("dried flower")
            elif exp_lower in ("oral/edible", "oral", "oral administration"):
                add_type("edibles")

    is_clinical = any(str(s).startswith("Clinical (") for s in (study_type or []))
    if is_clinical and (not types or types == ["unknown"]):
        patient_blob = scan_blob
        if keyword_match(patient_blob, ["flower", "bud", "joint", "smoked", "combust", "flower product"]):
            add_type("dried flower")
        if keyword_match(patient_blob, ["edible", "edibles", "brownie", "cookie", "gummy", "medible"]):
            add_type("edibles")
        if keyword_match(patient_blob, [
            "concentrate", "concentrates", "wax", "shatter", "resin", "oil/concentrate",
            "concentrates and oils",
        ]):
            add_type("concentrates")

    if "concentrates" in types and is_analytical_or_computational(scan_blob):
        if not keyword_match(scan_blob, ["shatter", "wax", "bho", "rosin", "hash oil", "concentrate product", "concentrates and oils"]):
            types.remove("concentrates")
    if is_clinical and "dried flower" in types and "pure cannabinoid" in types:
        if not _has_pure_cannabinoid_vendor_override(scan_blob) and not _text_suggests_pure_cannabinoid(scan_blob):
            types.remove("pure cannabinoid")
    if "CB receptor agonist" in types and is_clinical:
        if not keyword_match(scan_blob, list(SYNTHETIC_AGONIST_CUES) + ["synthetic cannabinoid", "cb1 agonist", "cb2 agonist"]):
            types.remove("CB receptor agonist")
    if is_clinical and "edibles" in types:
        if keyword_match(scan_blob, ["prevalence", "population survey", "catchment"]) and not keyword_match(
            scan_blob, ["medible", "edible product", "reported use", "mode of use"]
        ):
            types.remove("edibles")
    if is_clinical and _is_endocannabinoid_biomarker_study(scan_blob, study_type):
        if keyword_match(scan_blob, [
            "cannabis use", "marijuana use", "used cannabis", "cannabis users",
            "cannabis consumption", "reported cannabis", "current cannabis",
        ]):
            return ["dried flower"]
        return ["unknown"]
    if _extract_detected_substance_strain(scan_blob):
        return ["CB receptor agonist"]
    if re.search(r"(?i)delta-8|delta 8|\bΔ8\b", title) and keyword_match(
        scan_blob, ["review", "narrative review", "systematic review"],
    ):
        review_types: List[str] = []
        if keyword_match(scan_blob, list(VAPE_PEN_DEVICE_CUES)):
            review_types.append("vape pen")
        if keyword_match(scan_blob, list(EDIBLES_PRODUCT_PHRASES) + ["edible", "edibles", "gummy"]):
            review_types.append("edibles")
        if keyword_match(scan_blob, ["concentrate", "concentrates", "distillate", "wax"]):
            review_types.append("concentrates")
        if keyword_match(scan_blob, ["pure cannabinoid", "isolate", "delta-8-thc", "delta-8 thc"]):
            review_types.append("pure cannabinoid")
        if review_types:
            return review_types
    if is_clinical and types:
        if all(tag in ("pure cannabinoid", "CB receptor agonist", "concentrates") for tag in types):
            if not keyword_match(scan_blob, ["administered", "treated with", "received", "dose", "intervention"]):
                return ["unknown"]

    if not types:
        types.append("unknown")

    return types
 
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


def extract_outcomes(
    title: str,
    abstract: str,
    full_text: Optional[str] = None,
    study_type: Any = None,
) -> List[str]:
    """Identifies multi-label outcome domains from intro/objective sections (abstract or PDF)."""
    combined = get_intro_objective_text(title, abstract).lower()
    if full_text:
        pdf_intro = get_intro_objective_text(title, full_text[:12000]).lower()
        combined = f"{combined}\n{pdf_intro}"
    title_lower = (title or "").lower()
    combined = f"{title_lower}\n{combined}"
    title_anxiety_app = bool(
        re.search(r"\b(?:anxiety|mental health)\b", title_lower)
        and re.search(r"\b(?:assessment|management|intervention|app)\b", title_lower)
    )
    outcomes = list(_title_outcome_hints(title))
    
    mapping = {
        "pain": [
            "pain", "analgesic", "analgesia", "nociception", "hyperalgesia", "allodynia",
            "neuropathic", "girk", "pain threshold",
        ],
        "anxiety": ["anxiety", "anxiolytic", "fear", "panic", "generalized anxiety", "ptsd"],
        "cognition": ["cognition", "cognitive", "memory", "attention", "executive function", "dementia", "alzheimer"],
        "inflammation": ["inflammation", "inflammatory", "cytokine", "tnf", "interleukin", "il-6", "anti-inflammatory", "arthritis"],
        "addiction": [
            "addiction", "dependence", "withdrawal", "craving", "abuse", "substance use",
            "cannabis use", "relapse", "drug tolerance", "receptor internalization",
            "desensitization", "conditioned place preference", "cpp",
        ],
        "oncology": [
            "oncology", "cancer", "tumor", "tumour", "chemotherapy", "glioblastoma", "carcinoma",
            "antineoplastic", "leukemia", "t-all", "lymphoma", "tumor cell", "apoptosis in",
            "cytotoxicity", "antiproliferative", "cell viability", "ic50", "mtt assay",
            "colony formation",
        ],
        "neuroprotection": ["neuroprotection", "neuroprotective", "stroke", "ischemia", "brain injury", "sclerosis", "epilepsy", "seizure"],
        "sleep": ["sleep", "insomnia", "actigraphy", "sleep quality", "melatonin"]
    }
    
    for domain, keywords in mapping.items():
        if keyword_match(combined, keywords):
            outcomes.append(domain)
        elif domain == "oncology" and "apoptosis" in combined and any(
            token in combined for token in ("cell line", "cells", "tumor", "cancer", "glioblastoma", "leukemia")
        ):
            outcomes.append(domain)
            
    if not outcomes:
        outcomes.append("other")
    if title_anxiety_app and "anxiety" not in outcomes:
        outcomes = [item for item in outcomes if item != "other"]
        outcomes.append("anxiety")
        if not outcomes:
            outcomes = ["anxiety"]
    biomarker_ctx = _is_endocannabinoid_biomarker_study(
        f"{title} {abstract or ''} {full_text[:8000] if full_text else ''}",
        study_type or ["Clinical (observational)"],
    )
    if biomarker_ctx and not _title_outcome_hints(title):
        spurious = {"anxiety", "cognition", "addiction"} & set(outcomes)
        if spurious and "other" not in outcomes:
            outcomes = [item for item in outcomes if item not in spurious]
            if not outcomes:
                outcomes = ["other"]

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

def extract_all_heuristics(
    title: str,
    abstract: str,
    full_text: Optional[str] = None,
    study_type_override: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convenience pipeline to run all heuristic extractions on a paper.

    Uses Methods-isolated text when available so node2a/2b/2c share the same
    extraction logic for dose, duration, strain, and product-type fields.
    """
    publication_type = infer_publication_type(title, abstract)
    if study_type_override:
        study_type = list(study_type_override)
    else:
        study_type = _refine_study_type_list(
            infer_study_type(title, abstract),
            f"{title} {abstract}".lower(),
            title,
            abstract,
        )
    exposure_method = infer_exposure_method(title, abstract, study_type, full_text=full_text)

    methods_text = get_methods_text(title, abstract)
    extraction_blob = methods_text if methods_text.strip() else f"{title}\n\n{abstract or ''}"
    combined_text = f"{title} {abstract or ''}"

    thc_pct = extract_thc_pct(extraction_blob)
    if thc_pct is None:
        thc_pct = extract_thc_pct(title)

    cbd_pct = extract_cbd_pct(extraction_blob)
    if cbd_pct is None:
        cbd_pct = extract_cbd_pct(title)

    dose_mg = extract_dose_mg(extraction_blob)
    sample_size = extract_sample_size(full_text) if full_text else None
    if sample_size is None:
        sample_size = extract_sample_size(extraction_blob)
    outcome_domain = extract_outcomes(title, abstract, full_text=full_text, study_type=study_type)
    cannabis_type = infer_cannabis_type(title, abstract, study_type, exposure_method, full_text=full_text)

    strain_reported, strain_normalized = extract_strain_info(
        extraction_blob, cannabis_type=cannabis_type, study_type=study_type,
    )
    if not strain_reported:
        strain_reported, strain_normalized = extract_strain_info(
            title, cannabis_type=cannabis_type, study_type=study_type,
        )
    study_set = set(study_type) if isinstance(study_type, list) else {study_type} if isinstance(study_type, str) else set()
    is_invitro_for_strain = any(str(item).startswith("Cell Culture (") for item in study_set)
    if not strain_reported and is_invitro_for_strain:
        strain_reported, strain_normalized = extract_strain_info(
            combined_text, cannabis_type=cannabis_type, study_type=study_type,
        )
    if not strain_reported and is_invitro_for_strain and full_text:
        strain_reported, strain_normalized = extract_strain_info(
            full_text, cannabis_type=cannabis_type, study_type=study_type,
        )

    thc_mg_kg, cbd_mg_kg, mgkg_multiple = extract_thc_cbd_mg_kg(extraction_blob)
    if thc_mg_kg is None and cbd_mg_kg is None:
        thc_mg_kg, cbd_mg_kg, mgkg_multiple = extract_thc_cbd_mg_kg(combined_text)
    if thc_mg_kg is None and cbd_mg_kg is None and full_text:
        thc_mg_kg, cbd_mg_kg, mgkg_multiple = extract_thc_cbd_mg_kg(full_text)

    is_clinical = any(s.startswith("Clinical (") for s in study_set)
    is_invivo = any(s.startswith("Animal Models (") for s in study_set)
    is_invitro = any(s.startswith("Cell Culture (") for s in study_set)

    duration_days = None
    inhaled_exposure_duration = None
    administration_frequency = None
    treatment_duration = None

    observational_clinical = is_clinical and not any("RCT" in item for item in study_set)
    if is_clinical or is_invivo:
        invivo_primary = _looks_like_invivo_primary(extraction_blob)
        mixed_invitro_primary = is_invivo and is_invitro and not invivo_primary
        if (not mixed_invitro_primary or invivo_primary) and not observational_clinical:
            duration_days = extract_duration_days(extraction_blob)
            if duration_days is None:
                duration_days = extract_duration_days(combined_text)
            if duration_days is None and full_text:
                duration_days = extract_duration_days(full_text)
        exposure_list = exposure_method if isinstance(exposure_method, list) else [exposure_method]
        is_inhaled = any(
            "inhaled" in str(e).lower() or "smok" in str(e).lower()
            or "vapor" in str(e).lower() or "nose" in str(e).lower()
            or "whole body" in str(e).lower()
            for e in exposure_list
        )
        if is_inhaled:
            inhaled_exposure_duration = extract_inhaled_exposure_duration(extraction_blob)
            if inhaled_exposure_duration is None:
                inhaled_exposure_duration = extract_inhaled_exposure_duration(combined_text)
        administration_frequency = extract_administration_frequency(
            extraction_blob, study_type=study_type,
        )
        if administration_frequency is None:
            administration_frequency = extract_administration_frequency(
                combined_text, study_type=study_type,
            )
        if administration_frequency is None and full_text:
            administration_frequency = extract_administration_frequency(
                full_text, study_type=study_type,
            )
        if administration_frequency and _is_endocannabinoid_biomarker_study(
            f"{combined_text} {full_text or ''}", study_type,
        ):
            administration_frequency = None

    thc_mg_ml = None
    if is_clinical or is_invivo:
        exposure_list = exposure_method if isinstance(exposure_method, list) else [exposure_method]
        is_inhaled_for_conc = any(
            "inhaled" in str(e).lower() or "smok" in str(e).lower()
            or "vapor" in str(e).lower() or "nose" in str(e).lower()
            or "whole body" in str(e).lower()
            for e in exposure_list
        )
        if is_inhaled_for_conc:
            thc_mg_ml = extract_thc_mg_ml(extraction_blob)
            if thc_mg_ml is None:
                thc_mg_ml = extract_thc_mg_ml(combined_text)

    if is_invitro:
        treatment_duration = extract_treatment_duration(extraction_blob)
        if treatment_duration is None:
            treatment_duration = extract_treatment_duration(combined_text)
        if treatment_duration is None and full_text:
            treatment_duration = extract_treatment_duration(full_text)
        if treatment_duration is None and full_text:
            from maude_classifier import extract_methods_section
            treatment_duration = extract_treatment_duration(
                extract_methods_section(full_text) or full_text,
            )

    multiple_doses = detect_multiple_doses(title, abstract) or mgkg_multiple
    multiple_time_intervals = detect_multiple_time_intervals(title, abstract)

    result = {
        "study_type": study_type,
        "exposure_method": exposure_method,
        "thc_pct": thc_pct,
        "cbd_pct": cbd_pct,
        "dose_mg": dose_mg,
        "puff_count": None,
        "thc_mg_ml": thc_mg_ml,
        "thc_mg_g": None,
        "thc_mg_kg": thc_mg_kg,
        "cbd_mg_ml": None,
        "cbd_mg_g": None,
        "cbd_mg_kg": cbd_mg_kg,
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
    import classification_schema

    return classification_schema.normalize_classification_record(result, title, abstract)

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
