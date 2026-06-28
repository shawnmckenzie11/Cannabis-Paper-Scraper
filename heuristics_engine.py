import os
import json
import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("heuristics_engine")

# Robust in-memory fallback config containing all baseline rules
FALLBACK_CONFIG = {
  "version": "1.1.0",
  "name": "Maude Heuristics Fallback Configuration",
  "constants": {
    "synthetic_agonist_cues": [
      "cp-55940", "cp55940", "cp 55,940", "win55212", "win 55,212", "win-55212",
      "o-1602", "jwh-", "hu-210", "hu210", "am2201"
    ],
    "synthetic_antagonist_cues": [
      "sr141716", "sr 141716", "am251", "am-251", "rimonabant", "am630", "cid16020046"
    ],
    "pure_cannabinoid_compound_cues": [
      "delta-9-thc", "delta9-thc", "tetrahydrocannabinol", "cannabidiol", "cannabigerol",
      "cannabidivarin", "cbdv", "cbg", "thcv"
    ],
    "plant_matter_cues": [
      "plant matter", "dried flower", "cannabis sativa plant", "cannabis flower",
      "smoked cannabis", "combusted cannabis", "vaporized cannabis plant matter",
      "cannabis plant matter"
    ],
    "extract_product_cues": [
      "whole plant extract", "full spectrum extract", "cannabis extract", "botanical extract"
    ],
    "vape_pen_device_cues": [
      "vape pen", "vape cartridge", "e-cigarette cartridge", "vaping device"
    ],
    "edible_oral_cues": [
      "edible", "edibles", "food treat", "mixed in food", "in peanut butter", "thc edible",
      "gummy", "gummies", "chocolate", "brownie", "brownies", "cookies", "cookie", "capsule", "capsules",
      "by mouth", "per os", "p.o.", "intragastric", "chow", "drinking water", "in food"
    ],
    "dissolved_in_media_triggers": [
      "isolated tissue", "tissue bath", "organ bath", "bath application",
      "embryos were exposed to", "tank water contained", "dissolved in tank water",
      "immersion in", "dissolved in artificial cerebrospinal fluid", "superfusion",
      "colon strips", "colon strip", "isolated rat colon", "isometric conditions",
      "dissolved in dmso", "diluted in dmso", "cell culture media", "culture medium",
      "treatment medium", "liposome", "liposomes", "mof", "drug delivery",
      "molecular dynamics simulation", "in silico", "incubated with cbd", "incubated with thc"
    ],
    "injection_route_guards": [
      "i.p.", "i.v.", "s.c.", "subcutaneous", "intraperitoneal", "intravenous",
      "intrathecal", "intracerebroventricular", "i.c.v.", "intramuscular", "i.m."
    ],
    "analytical_computational_cues": [
      "uhplc", "hplc", "hrms", "orbitrap", "lc-ms", "gc-ms", "gas chromatography",
      "flame ionization", "mass spectrometry", "thermal degradation", "pyrolysis",
      "kinetic degradation", "deep eutectic", "extraction yield", "extraction time",
      "extraction of bioactive", "certified reference material",
      "molecular docking", "molecular dynamics", "gromacs", "autodock", "vina",
      "dft", "density functional", "in silico", "docking score", "binding affinity",
      "pharmacophore model", "admet prediction", "swissadme", "pkcsm"
    ],
    "species_patterns": {
      "mouse": "\\bmice\\b|\\bmouse\\b|\\bmurine\\b|\\bC57BL",
      "rat": "\\brats\\b|\\brat\\b|\\bWistar\\b|\\bSprague[- ]Dawley\\b",
      "rodent_other": "\\bhamster\\b|\\bgerbil\\b|\\bguinea pig\\b|\\bvole\\b|\\brabbit\\b",
      "non_human_primate": "\\bmacaque\\b|\\brhesus\\b|\\bmonkey\\b|\\bbaboon\\b|\\bnon[- ]human primate",
      "zebrafish": "\\bzebrafish\\b|\\bDanio rerio\\b",
      "invertebrate": "\\bdrosophila\\b|\\bC\\. elegans\\b|\\bCaenorhabditis\\b",
      "vertebrate_non_mammal": "\\bfrog\\b|\\bXenopus\\b|\\bbird\\b|\\bavian\\b|\\bpigeon\\b",
      "other_mammal": "\\bdog\\b|\\bcat\\b|\\bpig\\b|\\bporcine\\b|\\bcanine\\b|\\bovine\\b"
    }
  },
  "routing": {
    "review_strong_cues": [
      "\\boverview paper\\b", "\\bsystematic review\\b", "\\bmeta-analysis\\b", "\\bmeta analysis\\b",
      "\\bnarrative synthesis\\b", "\\bnarrative review\\b", "\\bscoping review\\b", "\\bprogress report\\b",
      "\\bstate of the art\\b", "\\bmini-review\\b", "\\bminireview\\b", "\\bstudies reviewed here\\b",
      "\\bliterature review\\b", "\\beditorial\\b", "\\bcommentary\\b", "\\bletter to the editor\\b"
    ],
    "review_weak_title_cues": [
      "\\breview\\b", "\\bperspectives?\\b"
    ],
    "review_suppress_patterns": [
      "\\bchart review\\b", "\\bretrospective review\\b", "\\brecord review\\b",
      "\\breview of (?:patient|medical) records\\b", "\\bepidemiological overview\\b", "\\boverview and survey\\b"
    ],
    "case_cues": [
      "\\bcase report\\b", "\\bcase series\\b", "\\bwe report a case\\b", "\\bwe present a case\\b",
      "\\bsingle patient\\b", "\\bseries of patients\\b", "\\bpresent a series of\\b", "\\ba case of\\b",
      "\\bcase of a patient\\b"
    ],
    "original_negative_cues": [
      "\\bwe review\\b", "\\bliterature suggests\\b", "\\bprevious studies have shown\\b", "\\bsummarize evidence\\b"
    ],
    "animal_dosing_route_patterns": [
      "\\b(?:rat|rats|mouse|mice|rodent|rodents|zebrafish|rabbit|rabbits|primate|primates|canine|dog|dogs)\\b.{0,120}\\b\\d+(?:\\.\\d+)?\\s*mg/kg\\b",
      "\\b\\d+(?:\\.\\d+)?\\s*mg/kg\\b.{0,80}\\b(?:i\\.p\\.|i\\.v\\.|s\\.c\\.|i\\.m\\.|intraperitoneal|subcutaneous|intravenous|oral gavage)\\b",
      "\\b(?:received|administered|injected|treated with|dosed with)\\b.{0,80}\\b(?:thc|cbd|cannabidiol|tetrahydrocannabinol|cannabinoid|cannabinoids)\\b.{0,80}\\b\\d+(?:\\.\\d+)?\\s*mg/kg\\b"
    ],
    "clinical_primary_data_patterns": [
      "\\bparticipants (?:were|completed|recruited|enrolled|surveyed)\\b",
      "\\bpatients (?:were|completed|recruited|enrolled)\\b",
      "\\bwe (?:surveyed|recruited|enrolled|conducted a (?:prospective|cross-sectional|cohort))\\b",
      "\\bn\\s*=\\s*\\d+\\s+(?:participants|patients|respondents|subjects|adults)\\b",
      "\\bexploratory study\\b",
      "\\bcross-sectional (?:study|survey)\\b",
      "\\bquestionnaire (?:was|were) (?:administered|completed|distributed)\\b",
      "\\brandomized (?:controlled )?trial\\b",
      "\\bplacebo[- ]controlled\\b",
      "\\bclinical trial\\b"
    ],
    "study_design_exempt_patterns": [
      "\\bwe conducted\\b", "\\bwe analyzed\\b", "\\bwe surveyed\\b", "\\bhere we present\\b",
      "\\bparticipants\\b", "\\bpatients\\b", "\\bn\\s*=\\s*\\d", "\\bsample of\\b",
      "\\bcross[- ]sectional\\b", "\\bcohort\\b", "\\bsurvey\\b", "\\bretrospective\\b",
      "\\bprospective\\b", "\\brandomized\\b", "\\bclinical trial\\b", "\\bobservational\\b",
      "\\bquestionnaire\\b", "\\bprevalence of\\b"
    ],
    "administration_cue_patterns": [
      "\\badministered\\b", "\\btreated with\\b", "\\breceived (cbd|thc|cannabidiol|tetrahydrocannabinol)\\b",
      "\\bdose\\b", "\\bmg/kg\\b", "\\binjected\\b", "\\bgavage\\b", "\\binhaled\\b", "\\bsmoked\\b",
      "\\bWIN 55\\b", "\\bCP 55\\b", "\\banandamide\\b", "\\b2-AG\\b", "\\bcb receptor agonist\\b"
    ],
    "in_vivo_animal_terms": [
      "rat", "rats", "mouse", "mice", "rodent", "rodents", "rabbit", "rabbits",
      "monkey", "monkeys", "primate", "primates", "zebrafish", "canine", "dog", "dogs"
    ],
    "in_vivo_override_terms": [
      "in vivo", "in-vivo"
    ],
    "narrative_review_study_cues": [
      "consensus recommendations", "pharmacological foundations", "receptor mechanisms underlying",
      "endocannabinoid signaling", "exploiting the multifaceted", "cannabis use: neurobiological",
      "terpenes/terpenoids", "medical cannabis and driving", "life cycle assessment",
      "comprehensive cannabinoid profiling", "high-resolution ion mobility", "selective preparation and high dynamic-range"
    ],
    "metadata_routing": [
      {
        "id": "pubmed_meta_analysis_prefix",
        "match": "publication type: meta-analysis",
        "match_field": "abstract",
        "node_id": "node1b_reviews",
        "publication_type": "review",
        "study_type": "meta-analysis",
        "extra_nodes": ["node3b"],
        "score": 0.55,
        "source": "harvest.py PubMed PublicationTypeList",
        "priority": 100
      },
      {
        "id": "pubmed_review_prefix",
        "match": "publication type: review",
        "match_field": "abstract",
        "node_id": "node1b_reviews",
        "publication_type": "review",
        "study_type": "review",
        "extra_nodes": [],
        "score": 0.5,
        "source": "harvest.py PubMed PublicationTypeList",
        "priority": 90
      }
    ]
  },
  "publication_types": {
    "meta-analysis": [
      "publication type: meta-analysis", "meta-analysis", "meta-analyses", "pooled analysis", "systematic overview"
    ],
    "systematic_review": [
      "systematic review", "systematic reviews", "scoping review", "scoping reviews"
    ],
    "review": [
      "publication type: review", "literature review", "overview of reviews", "narrative review", "critical review",
      "mini-review", "minireview", "review article", "review paper", "this review", "the present review",
      "in this review", "we review", "current review", "comprehensive review", "review of the literature",
      "this mini-review", "this minireview", "article reviews", "reviews the current", "reviews the literature"
    ],
    "case_study": [
      "case study", "case studies", "case report", "case reports", "case series", "clinical case", "case-report", "case-series"
    ],
    "editorial": [
      "editorial", "editorials"
    ],
    "comment": [
      "commentary", "commentaries", "comment", "opinion", "viewpoint"
    ],
    "letter_to_the_editor": [
      "letter to the editor", "letters to the editor"
    ],
    "perspectives_paper": [
      "perspective", "perspectives"
    ]
  },
  "study_types": {
    "Clinical (RCT)": [
      "double-blind", "randomized controlled", "placebo-controlled", "rct", "randomised controlled", "clinical trial"
    ],
    "Clinical (prospective)": [
      "prospective", "prospectively", "prospective cohort"
    ],
    "Clinical (retrospective)": [
      "retrospective", "retrospectively", "chart review", "historical cohort"
    ],
    "Clinical (observational)": [
      "observational", "cross-sectional", "survey", "surveys", "registry", "registries",
      "longitudinal", "case-control", "epidemiological", "cohort", "cohorts", "gwas",
      "genome-wide", "genomewide", "quasi-experimental", "quasi-experiment", "pre-post",
      "school-based intervention", "educational intervention"
    ],
    "Animal Models (Mouse)": [
      "mouse", "mice", "murine", "c57bl/6"
    ],
    "Animal Models (Rat)": [
      "rat", "rats", "wistar", "sprague-dawley"
    ],
    "Animal Models (Other Rodents)": [
      "hamster", "hamsters", "gerbil", "gerbils", "guinea pig", "guinea pigs", "voles", "vole"
    ],
    "Animal Models (Non-Human Primates)": [
      "macaque", "rhesus", "monkey", "monkeys", "primate", "primates", "baboon", "chimpanzee"
    ],
    "Animal Models (Other)": [
      "dog", "dogs", "cat", "cats", "pig", "pigs", "rabbit", "rabbits", "zebrafish", "drosophila"
    ],
    "Cell Culture (Primary Cells)": [
      "primary cell", "primary cells", "primary culture", "primary neuronal", "primary microglia", "splenocytes", "primary hepatocytes", "primary cortical cell", "cortical cell culture", "neuron-enriched"
    ],
    "Cell Culture (Cell Lines)": [
      "cell line", "cell lines", "hela", "hepg2", "pc12", "raw 264.7", "sh-sy5y", "jurkat", "cho cells"
    ],
    "Cell Culture (Organoids)": [
      "organoid", "organoids", "spheroid", "spheroids", "3d culture", "3d cultures"
    ],
    "Cell Culture (Co-Culture)": [
      "co-culture", "co-cultures", "coculture", "cocultures"
    ],
    "Cell Culture (PCLS)": [
      "precision-cut lung slices", "pcls", "precision cut lung slices", "lung slice", "lung slices"
    ],
    "Cell Culture (Other In Vitro)": [
      "in vitro", "cultured cells", "culture assay", "cell culture", "cell cultures", "epithelial cells", "epithelial cell", "airway epithelial"
    ]
  },
  "extraction": {
    "population_age": {
      "professional_surveys": [
        "\\b(?:survey of|interviewed|questionnaire to)\\s+(?:elected officials|officials|healthcare professionals|professionals|providers|parents|teachers|retailers|staff|clinicians|physicians|pediatricians|policymakers)\\b",
        "\\b(?:healthcare professionals|providers|parents|teachers|retailers|policymakers|pediatricians)\\s+(?:completed|were surveyed|were interviewed)\\b"
      ],
      "pediatric_indicators": [
        "\\b(?:pediatric|child|adolescent|infant|teenager|youth|pediatrics)\\s+(?:patients|subjects|participants|cohort|population|group|users)\\b",
        "\\b(?:enrolled|recruited|included|studied)\\s+(?:children|adolescents|infants|teens|youths)\\b",
        "\\bchildren\\s+(?:aged|ranging from|with)\\b",
        "\\badolescents\\s+(?:aged|ranging from|with)\\b",
        "\\bunder\\s+18\\s*(?:years|yo)?\\b",
        "\\bage\\s+<\\s*18\\b",
        "\\bpediatric\\s+onset\\b",
        "\\bchildren\\b",
        "\\badolescents\\b",
        "\\byouth\\b",
        "\\byouths\\b"
      ],
      "geriatric_indicators": [
        "\\bgeriatric\\w*\\b",
        "\\belderly\\b",
        "\\bolder\\s+(?:adults|patients|subjects|participants|people|population)\\b",
        "\\baged\\s+(?:≥|>=|greater than or equal to|over)?\\s*65\\s*(?:years|yo)?\\b",
        "\\boctogenarian\\w*\\b",
        "\\bsenile\\b"
      ]
    },
    "population_sex": {
      "both_indicators": [
        "\\bboth\\s+(?:sexes|genders)\\b",
        "\\bmen\\s+and\\s+women\\b",
        "\\bwomen\\s+and\\s+men\\b",
        "\\bmale\\s+and\\s+female\\b",
        "\\bfemale\\s+and\\s+male\\b",
        "\\bmales\\s+and\\s+females\\b",
        "\\bfemales\\s+and\\s+males\\b",
        "\\bmixed[- ]sex\\b"
      ],
      "male_indicators": [
        "\\bmale\\s+(?:subjects|patients|participants|volunteers|cohort|population|group)\\b",
        "\\b(?:subjects|patients|participants|volunteers)\\s+(?:were|consisted of|included)\\s+[^.]{0,40}?\\s*(?:males|men)\\b",
        "\\bonly\\s+male\\b",
        "\\bmen\\s+(?:aged|were|diagnosed|\\([^)]+\\))\\b"
      ],
      "female_indicators": [
        "\\bfemale\\s+(?:subjects|patients|participants|volunteers|cohort|population|group)\\b",
        "\\b(?:subjects|patients|participants|volunteers)\\s+(?:were|consisted of|included)\\s+[^.]{0,40}?\\s*(?:females|women)\\b",
        "\\bonly\\s+female\\b",
        "\\bwomen\\s+(?:aged|were|diagnosed|\\([^)]+\\))\\b"
      ]
    }
  }
}

# --- Dynamic Rule Compilers & Reloaders ---

class PatternCompiler:
    """Compiles and stores compiled regex patterns for instant thread-safe reuse."""
    def __init__(self):
        self.professional_surveys = []
        self.pediatric_indicators = []
        self.geriatric_indicators = []
        self.both_indicators = []
        self.male_indicators = []
        self.female_indicators = []
        self.men_women_cooccurrence = re.compile(
            r"\b(\d+)\s*(?:men|male|males)\b[^.]{1,45}?\b(\d+)\s*(?:women|female|females)\b|"
            r"\b(\d+)\s*(?:women|female|females)\b[^.]{1,45}?\b(\d+)\s*(?:men|male|males)\b",
            re.IGNORECASE
        )
        # New compiled patterns
        self.species_patterns = {}
        self.review_strong_cues = []
        self.review_weak_title_cues = []
        self.review_suppress_patterns = []
        self.case_cues = []
        self.original_negative_cues = []
        self.animal_dosing_route_patterns = []
        self.clinical_primary_data_patterns = []
        self.study_design_exempt_patterns = []
        self.administration_cue_patterns = []

    def compile_rules(self, config_dict: Dict[str, Any]) -> None:
        try:
            # 1. Compile age/sex extraction rules
            age_cfg = config_dict.get("extraction", {}).get("population_age", FALLBACK_CONFIG["extraction"]["population_age"])
            sex_cfg = config_dict.get("extraction", {}).get("population_sex", FALLBACK_CONFIG["extraction"]["population_sex"])
            
            self.professional_surveys = [re.compile(p, re.IGNORECASE) for p in age_cfg.get("professional_surveys", [])]
            self.pediatric_indicators = [re.compile(p, re.IGNORECASE) for p in age_cfg.get("pediatric_indicators", [])]
            self.geriatric_indicators = [re.compile(p, re.IGNORECASE) for p in age_cfg.get("geriatric_indicators", [])]
            
            self.both_indicators = [re.compile(p, re.IGNORECASE) for p in sex_cfg.get("both_indicators", [])]
            self.male_indicators = [re.compile(p, re.IGNORECASE) for p in sex_cfg.get("male_indicators", [])]
            self.female_indicators = [re.compile(p, re.IGNORECASE) for p in sex_cfg.get("female_indicators", [])]
            
            # 2. Compile species patterns
            species_cfg = config_dict.get("constants", {}).get("species_patterns", FALLBACK_CONFIG["constants"]["species_patterns"])
            self.species_patterns = {
                k: re.compile(v, re.IGNORECASE) for k, v in species_cfg.items()
            }
            
            # 3. Compile routing patterns
            routing_cfg = config_dict.get("routing", FALLBACK_CONFIG["routing"])
            self.review_strong_cues = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("review_strong_cues", [])]
            self.review_weak_title_cues = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("review_weak_title_cues", [])]
            self.review_suppress_patterns = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("review_suppress_patterns", [])]
            self.case_cues = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("case_cues", [])]
            self.original_negative_cues = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("original_negative_cues", [])]
            self.animal_dosing_route_patterns = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("animal_dosing_route_patterns", [])]
            self.clinical_primary_data_patterns = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("clinical_primary_data_patterns", [])]
            self.study_design_exempt_patterns = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("study_design_exempt_patterns", [])]
            self.administration_cue_patterns = [re.compile(p, re.IGNORECASE) for p in routing_cfg.get("administration_cue_patterns", [])]
            
            logger.info("Compiled all regex patterns successfully.")
        except Exception as e:
            logger.error(f"Failed to compile regex patterns: {e}")

# Global instances
_config = FALLBACK_CONFIG
_rules_config = None
patterns = PatternCompiler()

def load_rules_from_file() -> Optional[Dict[str, Any]]:
    """Loads rules from heuristics_config.json on disk."""
    config_path = os.path.join(os.path.dirname(__file__), "heuristics_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and "version" in loaded:
                    return loaded
        except Exception as e:
            logger.warning(f"Failed to load heuristics_config.json: {e}")
    return None

def load_rules_from_db() -> Optional[Dict[str, Any]]:
    """Loads rules from the heuristics_rules database table."""
    try:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT rule_value FROM heuristics_rules WHERE rule_key = 'maude_heuristics'")
            row = cursor.fetchone()
            if row:
                val = row[0] if isinstance(row, tuple) else row.get("rule_value") if isinstance(row, dict) else row["rule_value"]
                return json.loads(val)
        except Exception:
            # Table might not exist during initial migration run
            pass
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to load rules from database: {e}")
    return None

def seed_rules_to_db(config_dict: Dict[str, Any]) -> None:
    """Seeds the heuristics_rules table with our version-controlled rules."""
    try:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            val_str = json.dumps(config_dict)
            
            is_postgres = "DATABASE_URL" in os.environ
            param = "%s" if is_postgres else "?"
            
            cursor.execute(f"SELECT 1 FROM heuristics_rules WHERE rule_key = {param}", ("maude_heuristics",))
            if cursor.fetchone():
                cursor.execute(
                    f"UPDATE heuristics_rules SET rule_value = {param}, updated_at = CURRENT_TIMESTAMP WHERE rule_key = {param}",
                    (val_str, "maude_heuristics")
                )
            else:
                cursor.execute(
                    f"INSERT INTO heuristics_rules (rule_key, rule_value) VALUES ({param}, {param})",
                    ("maude_heuristics", val_str)
                )
            conn.commit()
            logger.info("Successfully seeded/synced heuristics rules to the database.")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to seed heuristics rules to database: {e}")

def load_rules_config_from_file() -> Optional[Dict[str, Any]]:
    """Loads rules from rules_config.json on disk."""
    config_path = os.path.join(os.path.dirname(__file__), "rules_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and "version" in loaded:
                    return loaded
        except Exception as e:
            logger.warning(f"Failed to load rules_config.json: {e}")
    return None

def load_rules_config_from_db() -> Optional[Dict[str, Any]]:
    """Loads rules_config from the heuristics_rules database table."""
    try:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT rule_value FROM heuristics_rules WHERE rule_key = 'rules_config'")
            row = cursor.fetchone()
            if row:
                val = row[0] if isinstance(row, tuple) else row.get("rule_value") if isinstance(row, dict) else row["rule_value"]
                return json.loads(val)
        except Exception:
            # Table might not exist during initial migration run
            pass
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to load rules_config from database: {e}")
    return None

def seed_rules_config_to_db(config_dict: Dict[str, Any]) -> None:
    """Seeds the heuristics_rules table with our version-controlled rules_config."""
    try:
        from db_manager import DatabaseManager
        db = DatabaseManager()
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            val_str = json.dumps(config_dict)
            
            is_postgres = "DATABASE_URL" in os.environ
            param = "%s" if is_postgres else "?"
            
            cursor.execute(f"SELECT 1 FROM heuristics_rules WHERE rule_key = {param}", ("rules_config",))
            if cursor.fetchone():
                cursor.execute(
                    f"UPDATE heuristics_rules SET rule_value = {param}, updated_at = CURRENT_TIMESTAMP WHERE rule_key = {param}",
                    (val_str, "rules_config")
                )
            else:
                cursor.execute(
                    f"INSERT INTO heuristics_rules (rule_key, rule_value) VALUES ({param}, {param})",
                    ("rules_config", val_str)
                )
            conn.commit()
            logger.info("Successfully seeded/synced rules_config to the database.")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"Failed to seed rules_config to database: {e}")

def reload_rules_config() -> None:
    """Reloads rules_config dynamically from DB -> file -> fallback."""
    global _rules_config
    db_config = load_rules_config_from_db()
    if db_config:
        _rules_config = db_config
        logger.info("Loaded rules_config from database.")
    else:
        file_config = load_rules_config_from_file()
        if file_config:
            _rules_config = file_config
            logger.info("Loaded rules_config from file. Seeding database...")
            seed_rules_config_to_db(_rules_config)
        else:
            # Build default fallback
            _rules_config = {
                "version": "1.0.0",
                "confidence_thresholds": {
                    "auto_accept": 0.85,
                    "review_recommended": 0.60
                },
                "weights": {
                    "self_consistency": 0.5,
                    "retrieval_similarity": 0.3,
                    "model_agreement": 0.2
                },
                "self_consistency_runs": 1,
                "system_prompt": "Classify the attached cannabis/cannabinoid research paper..."
            }
            logger.warning("Using fallback rules_config configuration.")

def load_rules_config() -> Dict[str, Any]:
    """Exposes the active rules_config."""
    global _rules_config
    if _rules_config is None:
        reload_rules_config()
    return _rules_config

def reload_rules() -> None:
    """Public method to reload rules dynamically from DB -> file -> fallback, and re-compile regexes."""
    global _config
    db_config = load_rules_from_db()
    if db_config:
        _config = db_config
        logger.info("Loaded heuristics configuration from database.")
    else:
        file_config = load_rules_from_file()
        if file_config:
            _config = file_config
            logger.info("Loaded heuristics configuration from file. Seeding database...")
            seed_rules_to_db(_config)
        else:
            _config = FALLBACK_CONFIG
            logger.warning("Using fallback heuristics configuration.")
            
    # Re-compile patterns dynamically
    patterns.compile_rules(_config)
    
    # Reload rules_config
    reload_rules_config()

# Initialize rules at startup
reload_rules()

# Helper function to get constant lists
def get_constant(key: str) -> List[str]:
    return _config.get("constants", {}).get(key, FALLBACK_CONFIG["constants"][key])

def get_routing_list(key: str) -> List[str]:
    return _config.get("routing", {}).get(key, FALLBACK_CONFIG["routing"][key])

# --- Core Helper Functions ---

def keyword_match(text: str, keywords: List[str]) -> bool:
    """Returns True if any keyword is found in the text (case-insensitive)."""
    if not text or not keywords:
        return False
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)

# --- Age & Sex Heuristic Extraction ---

def extract_population_age(text: str) -> str:
    """Heuristic extraction for population age (pediatric, adult, geriatric, both)."""
    if not text:
        return "adult"
    text_lower = text.lower()
    
    # 1. Professional Survey Check
    for pattern in patterns.professional_surveys:
        if pattern.search(text_lower):
            return "adult"
            
    # 2. Check Pediatric and Geriatric Indicators
    has_ped = any(pattern.search(text_lower) for pattern in patterns.pediatric_indicators)
    has_ger = any(pattern.search(text_lower) for pattern in patterns.geriatric_indicators)
    
    if has_ped and has_ger:
        return "both"
    elif has_ped:
        # Check for minor exclusion
        exclusion_minor = False
        if any(kw in text_lower for kw in ["under 18", "under-18", "age < 18", "age<18"]):
            for keyword in ["under 18", "under-18", "age < 18", "age<18"]:
                idx = text_lower.find(keyword)
                if idx != -1:
                    window = text_lower[max(0, idx - 100):min(len(text_lower), idx + 100)]
                    if "exclu" in window:
                        exclusion_minor = True
                        break
        if exclusion_minor and not re.search(r"\bchildren\b", text_lower):
            return "adult"
        return "pediatric"
    elif has_ger:
        return "geriatric"
        
    return "adult"

def extract_population_sex(text: str) -> str:
    """Heuristic extraction for population sex (male, female, both) using co-occurrence logic."""
    if not text:
        return "both"
    text_lower = text.lower()[:8000]

    if re.search(
        r"(?i)\b(?:male or female|female or male|men or women|women or men|"
        r"both sexes|mixed-sex|mixed sex)\b(?:\s+(?:subjects|participants|volunteers|patients))?\b",
        text_lower,
    ):
        return "both"

    if re.search(r"\b(?:only )?(?:women|females)\s+with\b", text_lower):
        if not re.search(r"\b(?:men|male|males)\s+with\b", text_lower):
            return "female"
    if re.search(r"\b(?:only )?women\b(?![^.]{0,40}\b(?:and men|and male)\b)", text_lower):
        if re.search(r"\b(?:female participants only|only female|all female|all-female)\b", text_lower):
            return "female"
    women_with = re.search(r"\b(\d+)\s+women\s+with\b", text_lower)
    if women_with and not re.search(r"\b(?:\d+\s+)?men\s+with\b", text_lower):
        return "female"

    # 1. Check explicit "both" indicators
    for pattern in patterns.both_indicators:
        if pattern.search(text_lower):
            return "both"
            
    # 2. Count occurrences of gender keywords
    men_count = len(re.findall(r"\b(?:men|male|males)\b", text_lower))
    women_count = len(re.findall(r"\b(?:women|female|females)\b", text_lower))
    
    # Ratio check
    if women_count > 3 and men_count <= 1:
        return "female"
    if men_count > 3 and women_count <= 1:
        return "male"
        
    # 3. Look for explicit co-occurrence in participant description
    if patterns.men_women_cooccurrence.search(text_lower):
        return "both"
        
    # 4. Check exclusive pattern matches
    has_male = any(pattern.search(text_lower) for pattern in patterns.male_indicators)
    has_female = any(pattern.search(text_lower) for pattern in patterns.female_indicators)
    
    if has_male and not has_female and women_count < 4:
        return "male"
    if has_female and not has_male and men_count < 4:
        return "female"
        
    if men_count > 1 and women_count > 1:
        return "both"
        
    return "both"

# --- Config-Driven Matching and Routing ---

def matches_review_route(title: str, abstract: str) -> bool:
    """True when title/abstract cues justify review routing."""
    text = f"{title or ''} {abstract or ''}"
    title_text = title or ""
    
    # 1. Check strong review cues (title or abstract)
    for pattern in patterns.review_strong_cues:
        if pattern.search(text):
            return True
            
    # 2. Check suppresses
    suppressed = any(pattern.search(text) for pattern in patterns.review_suppress_patterns)
    if suppressed:
        return False
        
    # 3. Check weak review cues (title only)
    for pattern in patterns.review_weak_title_cues:
        if pattern.search(title_text):
            return True
            
    return False

def should_route_animal_before_review(title: str, text: str) -> bool:
    """Returns True when PDF/abstract signals primary animal dosing despite review-like PDF noise."""
    blob = f"{title or ''} {text or ''}".lower()
    
    # Check animal terms
    in_vivo_animal_terms = get_routing_list("in_vivo_animal_terms")
    if not any(term in blob for term in in_vivo_animal_terms):
        return False
        
    # Check overrides
    in_vivo_override_terms = get_routing_list("in_vivo_override_terms")
    if any(term in blob for term in in_vivo_override_terms):
        return True
        
    # Check animal dosing patterns
    return any(pattern.search(blob) for pattern in patterns.animal_dosing_route_patterns)

def should_route_clinical_before_review(title: str, text: str) -> bool:
    """Returns True when full text signals primary human-subjects data despite review-like cues."""
    blob = f"{title or ''} {text or ''}".lower()
    
    # Check human keywords
    from extractor import HUMAN_SUBJECT_KEYWORDS
    from extractor import keyword_match as extractor_kw_match
    if not extractor_kw_match(blob, list(HUMAN_SUBJECT_KEYWORDS)):
        return False
        
    # Check clinical patterns
    if any(pattern.search(blob) for pattern in patterns.clinical_primary_data_patterns):
        return True
        
    # Check methods/results section structure
    if re.search(r"\b(?:methods|results)\b", blob) and re.search(r"\b(?:survey|questionnaire|interviews?|participants)\b", blob):
        return True
        
    return False

def infer_species(text: str) -> Optional[str]:
    """Infers species label from routing/extraction text using compiled species patterns."""
    if not text:
        return None
    for label, pattern in patterns.species_patterns.items():
        if pattern.search(text):
            return label
    return None

def detect_review_subtype(text: str) -> str:
    """Returns the review study_type subtype implied by title/abstract cues."""
    text_lower = text.lower()
    
    # Check systematic review and meta-analysis explicitly
    for p in patterns.review_strong_cues:
        if "systematic review" in p.pattern and p.search(text_lower):
            return "systematic review"
        if ("meta-analysis" in p.pattern or "meta analysis" in p.pattern) and p.search(text_lower):
            return "meta-analysis"
        
    # Fallback to searches
    if "systematic review" in text_lower:
        return "systematic review"
    if "meta-analysis" in text_lower or "meta analysis" in text_lower:
        return "meta-analysis"
        
    if "editorial" in text_lower:
        return "editorial"
    if "commentary" in text_lower or "comment" in text_lower:
        return "comment"
    if "letter to the editor" in text_lower:
        return "letter to the editor"
    if "perspective" in text_lower or "perspectives" in text_lower:
        return "perspectives paper"
        
    return "review"
