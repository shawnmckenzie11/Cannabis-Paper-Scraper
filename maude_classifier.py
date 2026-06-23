# maude_classifier.py
"""Rule/cue-based Maude classifier parallel to the LLM pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import extractor
import classification_schema
import maude_cues
import heuristics_engine

BASE_DIR = Path(__file__).resolve().parent
MAUDE_TREE_FILE = BASE_DIR / "maude_tree.json"
RULES_CONFIG_FILE = BASE_DIR / "rules_config.json"

MAUDE_HIGH_LEVEL_FIELDS: Tuple[str, ...] = (
    "ingestion_status",
    "publication_type",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "species",
)

def infer_species(text: str) -> Optional[str]:
    """Infers species label from routing/extraction text using heuristics_engine."""
    return heuristics_engine.infer_species(text)

def _load_rules_config() -> Dict[str, Any]:
    """Loads rules_config.json for Maude routing cues."""
    if not RULES_CONFIG_FILE.exists():
        return {}
    with open(RULES_CONFIG_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def get_routing_cue_patterns(node_id: str, fallback: Sequence[str]) -> Tuple[str, ...]:
    """Builds routing regex patterns from the dynamic configuration."""
    if node_id == "node1b_reviews":
        return tuple(p.pattern for p in heuristics_engine.patterns.review_strong_cues)
    elif node_id == "node1c_case_report":
        return tuple(p.pattern for p in heuristics_engine.patterns.case_cues)
    return tuple(fallback)


def get_review_routing_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.review_strong_cues)


def get_case_routing_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.case_cues)


def get_original_negative_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.original_negative_cues)


def get_review_strong_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.review_strong_cues)


def get_review_weak_title_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.review_weak_title_cues)


def get_review_suppress_patterns() -> Tuple[str, ...]:
    return tuple(p.pattern for p in heuristics_engine.patterns.review_suppress_patterns)


def should_route_in_vivo_before_review(title: str, abstract: str) -> bool:
    """Returns True when abstract signals in vivo animal work despite review-like title cues."""
    text = f"{title or ''} {abstract or ''}".lower()
    in_vivo_override_terms = heuristics_engine.get_routing_list("in_vivo_override_terms")
    if not any(term in text for term in in_vivo_override_terms):
        return False
    in_vivo_animal_terms = heuristics_engine.get_routing_list("in_vivo_animal_terms")
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in in_vivo_animal_terms)


def should_route_animal_before_review(title: str, text: str) -> bool:
    """Returns True when PDF/abstract signals primary animal dosing despite review-like PDF noise."""
    return heuristics_engine.should_route_animal_before_review(title, text)


def should_route_clinical_before_review(title: str, text: str) -> bool:
    """Returns True when full text signals primary human-subjects data despite review-like cues."""
    return heuristics_engine.should_route_clinical_before_review(title, text)


def matches_review_route(title: str, abstract: str) -> bool:
    """True when title/abstract cues justify review routing."""
    return heuristics_engine.matches_review_route(title, abstract)


def load_maude_tree(path: Path = MAUDE_TREE_FILE) -> Dict[str, Any]:
    """Loads compiled Maude decision tree JSON."""
    if path.exists():
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def load_maude_config() -> Dict[str, Any]:
    """Loads Maude settings from rules_config.json."""
    if not RULES_CONFIG_FILE.exists():
        return {}
    with open(RULES_CONFIG_FILE, encoding="utf-8") as handle:
        config = json.load(handle)
    return config.get("maude") or {}


def extract_methods_section(full_text: Optional[str]) -> str:
    """Returns Methods-like section text from full paper text when available."""
    if not full_text:
        return ""
    match = re.search(
        r"(?is)\b(methods|materials and methods|experimental procedures)\b(.*?)(?=\b(results|discussion|references)\b|$)",
        full_text,
    )
    return match.group(0) if match else ""


def _cue_regex(cue: str, variant: bool = False) -> re.Pattern:
    """Builds a regex for exact or variant cue strings."""
    cue = cue.strip().strip('"')
    if variant:
        tokens = [re.escape(part) for part in re.split(r"[\s/]+", cue) if part]
        body = r".{0,12}".join(tokens) if tokens else re.escape(cue)
    else:
        body = re.escape(cue)
    return re.compile(body, re.IGNORECASE)


def match_cues(text: str, cues: Sequence[str], variant: bool = False) -> List[str]:
    """Returns cue strings that match the given text."""
    hits: List[str] = []
    for cue in cues:
        if _cue_regex(cue, variant=variant).search(text or ""):
            hits.append(cue)
    return hits


def infer_ingestion_status(title: str, abstract: str) -> str:
    """Assigns Node 0 ingestion_status."""
    return classification_schema.infer_ingestion_status(title, abstract)


CANNABIS_MENTION_PATTERN = re.compile(
    r"\b(cannabis|cannabinoid|marijuana|cannabidiol|tetrahydrocannabinol|\bcbd\b|\bthc\b)\b",
    re.IGNORECASE,
)

STUDY_DESIGN_EXEMPT_PATTERNS: Tuple[str, ...] = (
    r"\bwe conducted\b",
    r"\bwe analyzed\b",
    r"\bwe surveyed\b",
    r"\bhere we present\b",
    r"\bparticipants\b",
    r"\bpatients\b",
    r"\bn\s*=\s*\d",
    r"\bsample of\b",
    r"\bcross[- ]sectional\b",
    r"\bcohort\b",
    r"\bsurvey\b",
    r"\bretrospective\b",
    r"\bprospective\b",
    r"\brandomized\b",
    r"\bclinical trial\b",
    r"\bobservational\b",
    r"\bquestionnaire\b",
    r"\bprevalence of\b",
)

ADMINISTRATION_CUE_PATTERNS: Tuple[str, ...] = (
    r"\badministered\b",
    r"\btreated with\b",
    r"\breceived (cbd|thc|cannabidiol|tetrahydrocannabinol)\b",
    r"\bdose\b",
    r"\bmg/kg\b",
    r"\binjected\b",
    r"\bgavage\b",
    r"\binhaled\b",
    r"\bsmoked\b",
    r"\bWIN 55",
    r"\bCP 55",
    r"\banandamide\b",
    r"\b2-AG\b",
    r"\bcb receptor agonist\b",
)

REVIEW_PUBLICATION_TYPES = {"review", "case study"}


def _detect_review_subtype(text: str) -> str:
    """Returns the review study_type subtype implied by title/abstract cues."""
    if maude_cues.node_cue_matches(text, "node3a_systematic_review"):
        return "systematic review"
    if maude_cues.node_cue_matches(text, "node3b_meta_analysis"):
        return "meta-analysis"
    if re.search(r"\bsystematic review\b", text, re.IGNORECASE):
        return "systematic review"
    if re.search(r"\bmeta-analysis\b|\bmeta analysis\b", text, re.IGNORECASE):
        return "meta-analysis"
    if maude_cues.node_cue_matches(text, "node3c_narrative_editorial"):
        if re.search(r"\beditorial\b", text, re.IGNORECASE):
            return "editorial"
        if re.search(r"\bcommentary\b|\bcomment\b", text, re.IGNORECASE):
            return "comment"
        if re.search(r"\bperspectives?\b", text, re.IGNORECASE):
            return "perspectives paper"
        return "review"
    if re.search(r"\beditorial\b", text, re.IGNORECASE):
        return "editorial"
    if re.search(r"\bcommentary\b|\bcomment\b", text, re.IGNORECASE):
        return "comment"
    if re.search(r"\bletter to the editor\b", text, re.IGNORECASE):
        return "letter to the editor"
    if re.search(r"\bperspectives?\b", text, re.IGNORECASE):
        return "perspectives paper"
    return "review"


NODE2_BRANCH_IDS: Tuple[str, ...] = (
    "node2a_clinical",
    "node2b_in_vivo",
    "node2c_in_vitro",
)

NARRATIVE_REVIEW_STUDY_CUES: Tuple[str, ...] = (
    "consensus recommendations",
    "pharmacological foundations",
    "receptor mechanisms underlying",
    "endocannabinoid signaling",
    "exploiting the multifaceted",
    "cannabis use: neurobiological",
    "terpenes/terpenoids",
    "medical cannabis and driving",
    "life cycle assessment",
    "comprehensive cannabinoid profiling",
    "high-resolution ion mobility",
    "selective preparation and high dynamic-range",
)


def infer_narrative_review_study_type(title: str, abstract: str) -> Optional[List[str]]:
    """Detects narrative-review study_type when Node 1 is original but content is review-like."""
    text = f"{title} {abstract}".lower()
    if re.search(
        r"\bwe (conducted|performed|randomized|randomised|evaluated|analyzed|analysed|surveyed)\b",
        text,
        re.IGNORECASE,
    ):
        return None
    if any(cue in text for cue in NARRATIVE_REVIEW_STUDY_CUES):
        return ["review"]
    return None


def route_node2_branches(routing_text: str, methods_text: str) -> Tuple[List[str], float]:
    """Routes Node 2A/2B/2C/2D using expert cue catalog from maude_cues.json."""
    combined = f"{routing_text} {methods_text}".strip()
    matched: List[str] = []
    cue_score = 0.0
    for node_id in NODE2_BRANCH_IDS:
        score = maude_cues.score_node_cues(combined, node_id)
        if score > 0:
            matched.append(node_id)
            cue_score += min(0.2, 0.05 * score)
    if len(matched) > 1:
        matched.append("node2d_mixed")
    return matched, cue_score


def resolve_study_type_for_routing(
    title: str,
    abstract: str,
    publication_type: Optional[str],
    review_subtype: Optional[str],
    node2_branches: Sequence[str],
    methods_text: str = "",
) -> List[str]:
    """Resolves study_type using Node 1 publication route plus Node 2 branch fallbacks."""
    routing_blob = f"{title} {abstract} {methods_text}"
    full_blob = f"{title} {abstract}"
    has_human_subjects = extractor.keyword_match(
        routing_blob.lower(),
        list(extractor.HUMAN_SUBJECT_KEYWORDS),
    )
    if extractor.is_plant_cultivation_study(routing_blob) and not has_human_subjects:
        if re.search(
            r'(?i)\b(?:hemp variety|hemp plants?|growing season|greenhouse|field trial|'
            r'harvested|immature flowers?|flowering stage)\b',
            routing_blob,
        ):
            return ["Animal Models (Other)"]
        return ["Cell Culture (Other In Vitro)"]
    if extractor._extract_detected_substance_strain(routing_blob):
        return ["Clinical (observational)"]
    if re.search(
        r"(?i)\b(?:substances detected|drug checking|toxicology screening|forensic toxicology|designer drug)\b",
        routing_blob,
    ) and has_human_subjects:
        return ["Clinical (observational)"]
    if publication_type == "original research" and extractor.is_analytical_or_computational(full_blob):
        if extractor.is_plant_cultivation_study(routing_blob):
            return ["Cell Culture (Other In Vitro)"]
        if not (
            has_human_subjects
            and (
                should_route_clinical_before_review(title, routing_blob)
                or re.search(r"\b(?:participants|patients|subjects|volunteers|survey)\b", routing_blob, re.I)
            )
        ):
            return ["Cell Culture (Other In Vitro)"]

    study_type = extractor.infer_study_type_for_publication(title, abstract, publication_type)
    if publication_type == "review" and review_subtype and review_subtype not in study_type:
        return [review_subtype]
    if study_type:
        routing_blob = f"{title} {abstract} {methods_text}".lower()
        for label in extractor._collect_study_type_hits(routing_blob):
            if label not in study_type:
                study_type.append(label)
        study_type = extractor._refine_study_type_list(
            study_type,
            routing_blob,
            title,
            abstract,
        )
        if extractor._is_ecb_measurement_clinical_treatment(
            f"{title} {abstract} {methods_text}", study_type,
        ):
            if "Clinical (RCT)" not in study_type:
                study_type.append("Clinical (RCT)")
            if "Clinical (observational)" in study_type:
                study_type.remove("Clinical (observational)")
            if not any(item.startswith("Cell Culture (") for item in study_type):
                if extractor.keyword_match(
                    routing_blob,
                    ["cell line", "cell lines", "biopsy", "biopsies", "immunohistochemistry"],
                ):
                    study_type.append("Cell Culture (Cell Lines)")
        branch_set = set(node2_branches)
        if "node2a_clinical" in branch_set and extractor.keyword_match(
            routing_blob, list(extractor.HUMAN_SUBJECT_KEYWORDS)
        ):
            study_type = [item for item in study_type if not item.startswith("Animal Models")]
        return study_type
    if publication_type != "original research":
        return study_type

    narrative_review = infer_narrative_review_study_type(title, abstract)
    if narrative_review:
        return narrative_review

    full_blob = f"{title} {abstract}".lower()
    branch_set = set(node2_branches)
    if "node2a_clinical" in branch_set and "node2c_in_vitro" in branch_set and has_human_subjects:
        clinical = extractor.infer_study_type_for_publication(title, abstract, publication_type)
        if clinical:
            return clinical
        return ["Clinical (observational)"]
    if "node2c_in_vitro" in branch_set and "node2a_clinical" not in branch_set:
        return ["Cell Culture (Other In Vitro)"]
    if "node2b_in_vivo" in branch_set and "node2a_clinical" not in branch_set:
        blob = f"{title} {abstract}".lower()
        if re.search(r"\bmice\b|\bmouse\b|\bmurine\b", blob, re.IGNORECASE):
            return ["Animal Models (Mouse)"]
        if re.search(r"\brats\b|\brat\b|\bwistar\b", blob, re.IGNORECASE):
            return ["Animal Models (Rat)"]
        return ["Animal Models (Other)"]
    if extractor._looks_like_lab_in_vitro_study(title, abstract):
        return ["Cell Culture (Other In Vitro)"]
    blob = f"{title} {abstract}".lower()
    if re.search(r"\bprospective\b|\bprospectively\b|\blongitudinal cohort\b", blob, re.IGNORECASE):
        return ["Clinical (prospective)"]
    if re.search(r"\bretrospective\b|\bretrospectively\b", blob, re.IGNORECASE):
        types = ["Clinical (retrospective)"]
        if re.search(r"\bobservational\b|\bcross-sectional\b|\bcohort\b", blob, re.IGNORECASE):
            types.append("Clinical (observational)")
        return types
    if re.search(r"\bmouse\b|\bmice\b|\brat\b|\brats\b|\bin vivo\b|\banimal model\b", blob, re.IGNORECASE):
        if re.search(r"\bmice\b|\bmouse\b", blob, re.IGNORECASE):
            return ["Animal Models (Mouse)"]
        if re.search(r"\brats\b|\brat\b", blob, re.IGNORECASE):
            return ["Animal Models (Rat)"]
        return ["Animal Models (Other)"]
    animal_blob = f"{title} {abstract} {methods_text}"
    if "node2b_in_vivo" in branch_set and should_route_animal_before_review(title, animal_blob):
        lowered = animal_blob.lower()
        if re.search(r"\bzebrafish\b|\bdanio rerio\b", lowered, re.IGNORECASE):
            return ["Animal Models (Other)"]
        if re.search(r"\bmice\b|\bmouse\b|\bmurine\b", lowered, re.IGNORECASE):
            return ["Animal Models (Mouse)"]
        if re.search(r"\brats\b|\brat\b|\bwistar\b|\bsprague[- ]dawley\b", lowered, re.IGNORECASE):
            return ["Animal Models (Rat)"]
        return ["Animal Models (Other)"]
    human_blob = f"{title} {abstract} {methods_text}".lower()
    invitro_blob = f"{title} {abstract} {methods_text}"
    if not branch_set or "node2c_in_vitro" in branch_set:
        if (
            not has_human_subjects
            and (
                extractor.is_analytical_or_computational(invitro_blob)
                or re.search(
                    r"(?i)\b(?:molecular dynamics|in silico|gromacs|autodock|liposome|mof|"
                    r"metal.?organic|e-cigarette|electronic cigarette|pyrolysis|in vitro|"
                    r"cell culture|organoid|incubated with|drug delivery|transfection|"
                    r"lipofectamine|primary cells|hepatocytes|microglial|neurons?)\b",
                    invitro_blob,
                )
            )
        ):
            hits = extractor._collect_study_type_hits(invitro_blob.lower())
            invitro_hits = [item for item in hits if item.startswith("Cell Culture")]
            if invitro_hits:
                return invitro_hits
            return ["Cell Culture (Other In Vitro)"]
    if extractor.keyword_match(human_blob, list(extractor.HUMAN_SUBJECT_KEYWORDS)):
        return ["Clinical (observational)"]
    return ["Clinical (observational)"]


def apply_abstract_only_extraction_policy(result: Dict[str, Any]) -> Dict[str, Any]:
    """Clears downstream extraction fields for abstract-only classification (matches llm-reclassify)."""
    result["exposure_method"] = []
    result["cannabis_type"] = []
    result["outcome_domain"] = []
    result["species"] = None
    for field in (
        "thc_pct", "cbd_pct", "dose_mg", "strain_reported", "strain_normalized",
        "duration_days", "sample_size",
    ):
        result[field] = None
    return result


def should_run_sparse_fallback(
    full_text: Optional[str],
    enable_sparse_fallback: Optional[bool],
) -> bool:
    """Returns whether sparse-extraction fallback is enabled for this classification run."""
    if enable_sparse_fallback is not None:
        return enable_sparse_fallback
    return bool(full_text and full_text.strip())


def _abstract_allows_downstream_extraction(title: str, abstract: str) -> bool:
    """True when title/abstract alone carry enough signal for clinical downstream fields."""
    blob = f"{title} {abstract or ''}"
    if extractor._extract_detected_substance_strain(blob):
        return True
    if extractor._is_delta8_product_survey(blob):
        return True
    clinical_types = ["Clinical (observational)", "Clinical (prospective)", "Clinical (RCT)"]
    if extractor._is_endocannabinoid_biomarker_study(blob, clinical_types):
        return True
    if re.search(
        r"(?i)\b(?:cerebrospinal fluid|\bcsf\b).{0,80}(?:anandamide|endocannabinoid|2-ag)\s+levels?\b",
        blob,
    ):
        return True
    if extractor.keyword_match(
        blob.lower(),
        list(extractor.HUMAN_SUBJECT_KEYWORDS),
    ) and re.search(
        r"(?i)\b(?:cannabis use|marijuana use|used cannabis|cannabis users|participants|patients|volunteers)\b",
        blob,
    ):
        return True
    return False


def should_extract_downstream_fields(
    full_text: Optional[str],
    abstract_only_extraction: Optional[bool],
    title: str = "",
    abstract: str = "",
) -> bool:
    """Returns whether Node 2+ extraction fields should be populated."""
    if abstract_only_extraction is not None:
        return not abstract_only_extraction
    if full_text and full_text.strip():
        return True
    return _abstract_allows_downstream_extraction(title, abstract)


def _has_cannabinoid_administration_cues(text: str) -> bool:
    """True when text suggests active cannabinoid administration or pharmacology testing."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in ADMINISTRATION_CUE_PATTERNS)


def _count_meaningful_extraction_fields(result: Dict[str, Any]) -> int:
    """Counts populated downstream extraction fields after Node 1 routing."""
    count = 0
    study_type = result.get("study_type") or []
    if study_type and not all(str(item).lower() in {"unknown", ""} for item in study_type):
        count += 1

    exposure_method = result.get("exposure_method") or []
    if exposure_method and not all(str(item).lower() in {"unknown", ""} for item in exposure_method):
        count += 1

    cannabis_type = result.get("cannabis_type") or []
    if cannabis_type and not all(str(item).lower() in {"unknown", ""} for item in cannabis_type):
        count += 1

    outcome_domain = result.get("outcome_domain") or []
    if outcome_domain:
        count += 1

    if result.get("species"):
        count += 1

    for key in ("dose_mg", "thc_pct", "cbd_pct", "sample_size", "strain_reported"):
        if result.get(key) not in (None, "", 0):
            count += 1
    return count


def apply_sparse_extraction_fallback(
    title: str,
    abstract: str,
    publication_type: str,
    partial_result: Dict[str, Any],
    nodes: List[str],
    cue_score: float,
    rules_version: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Flags original-research papers with cannabis mentions but sparse downstream detail as not cannabis-related."""
    if publication_type != "original research":
        return None
    text = f"{title} {abstract}"
    if not CANNABIS_MENTION_PATTERN.search(text):
        return None
    if _has_cannabinoid_administration_cues(text):
        return None
    if _count_meaningful_extraction_fields(partial_result) >= 3:
        return None
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in STUDY_DESIGN_EXEMPT_PATTERNS):
        return None

    fallback_nodes = _dedupe_nodes(nodes + ["node0_ingestion"])
    return {
        "ingestion_status": "not_cannabis_related",
        "publication_type": None,
        "study_type": [],
        "exposure_method": [],
        "cannabis_type": [],
        "outcome_domain": [],
        "species": None,
        "classification_confidence": round(min(0.9, cue_score + 0.25), 3),
        "_maude_meta": {
            "classifier": "maude",
            "rules_version": rules_version,
            "nodes_visited": fallback_nodes,
            "cue_score": round(cue_score, 3),
            "sparse_extraction_fallback": True,
            "meaningful_field_count": _count_meaningful_extraction_fields(partial_result),
        },
    }





def _map_study_type_to_tree(study_types: Sequence[str], species: Optional[str]) -> List[str]:
    """Maps heuristic/LLM study types toward tree draft in vivo labels where applicable."""
    mapped: List[str] = []
    blob = " ".join(study_types).lower()
    if any(token in blob for token in ("clinical", "rct", "prospective", "retrospective", "observational")):
        mapped.extend(st for st in study_types if st not in mapped)
        return mapped
    if species == "non_human_primate":
        mapped.append("Non-Human Primates")
    elif species in {"mouse", "rat", "rodent_other"}:
        mapped.append("Rodents")
    elif species == "invertebrate":
        mapped.append("Invertebrates")
    elif species == "vertebrate_non_mammal":
        mapped.append("Vertebrates Non-Mammal")
    elif species == "other_mammal":
        mapped.append("Other Mammals")
    elif "animal models" in blob or "animal" in blob:
        mapped.append("Rodents")
    elif "cell culture" in blob or "vitro" in blob:
        mapped.extend(st for st in study_types if st not in mapped)
    else:
        mapped.extend(st for st in study_types if st not in mapped)
    return mapped or list(study_types)


def _dedupe_nodes(nodes: Sequence[str]) -> List[str]:
    """Returns node ids in first-seen order without duplicates."""
    seen: set = set()
    ordered: List[str] = []
    for node in nodes:
        if node not in seen:
            seen.add(node)
            ordered.append(node)
    return ordered


def route_from_metadata(title: str, abstract: str) -> Optional[Tuple[str, Optional[str], List[str], float]]:
    """Routes using PubMed publication-type prefixes and other metadata rules from heuristics_engine."""
    abstract_lower = (abstract or "").lower()
    title_lower = (title or "").lower()
    text = f"{title or ''} {abstract or ''}"
    
    metadata_rules = heuristics_engine.get_routing_list("metadata_routing")
    
    for rule in metadata_rules:
        match_field = rule.get("match_field", "abstract")
        haystack = abstract_lower if match_field == "abstract" else f"{title_lower} {abstract_lower}"
        match_text = (rule.get("match") or "").lower()
        if not match_text or match_text not in haystack:
            continue
        node_id = rule.get("node_id", "node1b_reviews")
        nodes = _dedupe_nodes([node_id, *(rule.get("extra_nodes") or [])])
        publication_type = rule.get("publication_type", "review")
        study_type = rule.get("study_type", "review")
        score = float(rule.get("score") or 0.5)
        if publication_type == "review":
            detected = _detect_review_subtype(text)
            if detected != "review":
                study_type = detected
            if study_type == "systematic review":
                nodes = _dedupe_nodes(nodes + ["node3a"])
            elif study_type == "meta-analysis":
                nodes = _dedupe_nodes(nodes + ["node3b"])
            elif study_type in {"editorial", "comment", "letter to the editor", "perspectives paper"}:
                nodes = _dedupe_nodes(nodes + ["node3c"])
        return publication_type, study_type, nodes, score
    return None


def route_publication_type(
    title: str,
    abstract: str,
    *,
    routing_blob: Optional[str] = None,
) -> Tuple[str, Optional[str], List[str], float]:
    """Routes Node 1B/1C/1A and returns coarse publication_type, review subtype, nodes, cue score."""
    metadata_route = route_from_metadata(title, abstract)
    if metadata_route is not None:
        return metadata_route

    title_abstract = f"{title} {abstract}"
    text = routing_blob or title_abstract
    nodes: List[str] = []
    score = 0.2
    case_patterns = get_case_routing_patterns()
    original_negative_patterns = get_original_negative_patterns()

    if should_route_in_vivo_before_review(title, text):
        nodes.append("node1a_original")
        return "original research", None, nodes, score + 0.25

    if should_route_animal_before_review(title, text):
        nodes.append("node1a_original")
        return "original research", None, nodes, score + 0.25

    if should_route_clinical_before_review(title, text):
        nodes.append("node1a_original")
        return "original research", None, nodes, score + 0.25

    if matches_review_route(title, abstract):
        nodes.append("node1b_reviews")
        score += 0.35
        subtype = _detect_review_subtype(title_abstract)
        if subtype == "systematic review":
            return "review", subtype, nodes + ["node3a"], score + 0.15
        if subtype == "meta-analysis":
            return "review", subtype, nodes + ["node3b"], score + 0.15
        if subtype in {"editorial", "comment", "letter to the editor", "perspectives paper"}:
            return "review", subtype, nodes + ["node3c"], score + 0.1
        return "review", "review", nodes, score + 0.1

    for pattern in case_patterns:
        if re.search(pattern, title_abstract, re.IGNORECASE):
            nodes.append("node1c_case_report")
            return "case study", "case study", nodes, score + 0.35

    if any(re.search(p, title_abstract, re.IGNORECASE) for p in original_negative_patterns):
        nodes.append("node1b_reviews")
        return "review", "review", nodes, score + 0.2

    nodes.append("node1a_original")
    return "original research", None, nodes, score + 0.25


def compute_maude_confidence(cue_score: float, nodes: Sequence[str]) -> float:
    """Confidence from cue strength and decision-tree depth (v1 default)."""
    depth_bonus = min(0.35, 0.04 * max(0, len(nodes) - 1))
    return round(min(0.95, max(0.35, cue_score + depth_bonus)), 3)


MAUDE_DOWNSTREAM_EXTRACTION_FIELDS: Tuple[str, ...] = (
    "thc_pct", "cbd_pct", "dose_mg", "strain_reported", "strain_normalized",
    "duration_days", "sample_size", "administration_frequency", "inhaled_exposure_duration",
    "thc_mg_kg", "cbd_mg_kg", "thc_mg_ml", "cbd_mg_ml", "thc_mg_g", "cbd_mg_g",
    "thc_uM", "cbd_uM", "puff_count", "treatment_duration",
    "multiple_doses", "multiple_time_intervals", "repeat_exposure_count",
    "exposure_regimen_bin",
    "population_age", "population_sex", "inclusion_criteria", "exclusion_criteria",
)


def classify_paper(
    title: str,
    abstract: str,
    full_text: Optional[str] = None,
    rules_version: Optional[str] = None,
    *,
    abstract_only_extraction: Optional[bool] = None,
    enable_sparse_fallback: Optional[bool] = None,
) -> Dict[str, Any]:
    """Runs the Maude rule/cue classifier and returns extraction fields plus metadata."""
    maude_cfg = load_maude_config()
    tree = load_maude_tree()
    routing_text = f"{title} {abstract}"
    methods_text = extract_methods_section(full_text)
    extraction_text = methods_text or routing_text
    publication_routing_text = routing_text
    if full_text:
        publication_routing_text = f"{title} {full_text[:15000]}"
    extract_downstream = should_extract_downstream_fields(
        full_text, abstract_only_extraction, title=title, abstract=abstract,
    )
    run_sparse_fallback = should_run_sparse_fallback(full_text, enable_sparse_fallback)

    ingestion_status = infer_ingestion_status(title, abstract)
    nodes: List[str] = ["node0_ingestion"]
    cue_score = 0.15

    if ingestion_status == "not_cannabis_related":
        return classification_schema.normalize_classification_record({
            "ingestion_status": ingestion_status,
            "publication_type": None,
            "study_type": [],
            "exposure_method": [],
            "cannabis_type": [],
            "outcome_domain": [],
            "species": None,
            "classification_confidence": 0.85,
            "_maude_meta": {
                "classifier": "maude",
                "rules_version": rules_version,
                "nodes_visited": nodes,
                "cue_score": cue_score,
                "abstract_only_extraction": not extract_downstream,
            },
        }, title, abstract)

    publication_type, review_subtype, route_nodes, route_score = route_publication_type(
        title,
        abstract,
        routing_blob=publication_routing_text if full_text else None,
    )
    nodes = _dedupe_nodes(nodes + route_nodes)
    cue_score += route_score

    study_type: List[str] = []
    heuristics: Dict[str, Any] = {}
    species: Optional[str] = None
    exposure_method: List[str] = []
    cannabis_type: List[str] = []
    outcome_domain: List[str] = []

    if publication_type == "original research":
        node2_branches, node2_score = route_node2_branches(routing_text, methods_text)
        nodes = _dedupe_nodes(nodes + node2_branches)
        cue_score += node2_score
        study_type = resolve_study_type_for_routing(
            title,
            abstract,
            publication_type,
            review_subtype,
            node2_branches,
            methods_text or "",
        )
        if extract_downstream:
            heuristics = extractor.extract_all_heuristics(
                title,
                extraction_text,
                full_text=full_text,
                study_type_override=study_type,
            )
            species = infer_species(extraction_text)
            exposure_method = heuristics.get("exposure_method") or []
            cannabis_type = heuristics.get("cannabis_type") or []
            outcome_domain = heuristics.get("outcome_domain") or []
            if not any(item.startswith("Animal Models") for item in study_type):
                species = None
            elif (
                any(item.startswith("Cell Culture (") for item in study_type)
                and not extractor._looks_like_invivo_primary(extraction_text)
            ):
                species = None
    else:
        study_type = resolve_study_type_for_routing(
            title,
            abstract,
            publication_type,
            review_subtype,
            route_nodes,
            methods_text or "",
        )
        exposure_method = []
        cannabis_type = []

    confidence = compute_maude_confidence(cue_score, nodes)
    partial_result = {
        "ingestion_status": ingestion_status,
        "publication_type": publication_type,
        "study_type": study_type,
        "exposure_method": exposure_method,
        "cannabis_type": cannabis_type,
        "outcome_domain": outcome_domain,
        "species": species,
        "dose_mg": heuristics.get("dose_mg"),
        "thc_pct": heuristics.get("thc_pct"),
        "cbd_pct": heuristics.get("cbd_pct"),
        "sample_size": heuristics.get("sample_size"),
        "strain_reported": heuristics.get("strain_reported"),
    }
    fallback = None
    if run_sparse_fallback:
        fallback = apply_sparse_extraction_fallback(
            title,
            abstract,
            publication_type,
            partial_result,
            nodes,
            cue_score,
            rules_version,
        )
    if fallback is not None:
        return classification_schema.normalize_classification_record(fallback, title, abstract)

    result = {
        "ingestion_status": ingestion_status,
        "publication_type": publication_type,
        "study_type": study_type,
        "exposure_method": exposure_method,
        "cannabis_type": cannabis_type,
        "outcome_domain": outcome_domain,
        "species": species,
        "classification_confidence": confidence,
        "_maude_meta": {
            "classifier": "maude",
            "rules_version": rules_version,
            "tree_version": tree.get("version"),
            "nodes_visited": nodes,
            "cue_score": round(cue_score, 3),
            "methods_used": bool(methods_text),
            "abstract_only_extraction": not extract_downstream,
        },
    }
    for field in MAUDE_DOWNSTREAM_EXTRACTION_FIELDS:
        if field in heuristics:
            result[field] = heuristics.get(field)
    if not extract_downstream:
        apply_abstract_only_extraction_policy(result)
    return classification_schema.normalize_classification_record(result, title, abstract)


def normalize_compare_value(value: Any) -> Any:
    """Normalizes values for Maude vs LLM disagreement checks."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return sorted(str(item).lower() for item in parsed)
            except Exception:
                pass
        return stripped.lower()
    if isinstance(value, list):
        return sorted(str(item).lower() for item in value)
    if value is None:
        return None
    return value


def compare_maude_llm(
    maude: Dict[str, Any],
    llm: Dict[str, Any],
    title: str = "",
    abstract: str = "",
) -> Dict[str, Any]:
    """Compares high-level expert-tree fields and returns disagreement details."""
    return classification_schema.compare_classifiers(maude, llm, title, abstract)
