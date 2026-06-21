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

SPECIES_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"\bmice\b|\bmouse\b|\bmurine\b|\bC57BL", "mouse"),
    (r"\brats\b|\brat\b|\bWistar\b|\bSprague[- ]Dawley\b", "rat"),
    (r"\bhamster\b|\bgerbil\b|\bguinea pig\b|\bvole\b|\brabbit\b", "rodent_other"),
    (r"\bmacaque\b|\brhesus\b|\bmonkey\b|\bbaboon\b|\bnon[- ]human primate", "non_human_primate"),
    (r"\bzebrafish\b|\bDanio rerio\b", "zebrafish"),
    (r"\bdrosophila\b|\bC\. elegans\b|\bCaenorhabditis\b", "invertebrate"),
    (r"\bfrog\b|\bXenopus\b|\bbird\b|\bavian\b|\bpigeon\b", "vertebrate_non_mammal"),
    (r"\bdog\b|\bcat\b|\bpig\b|\bporcine\b|\bcanine\b|\bovine\b", "other_mammal"),
)

# Strong review cues match in title or abstract (high precision).
REVIEW_STRONG_CUES = (
    r"\boverview paper\b",
    r"\bsystematic review\b",
    r"\bmeta-analysis\b",
    r"\bmeta analysis\b",
    r"\bnarrative synthesis\b",
    r"\bnarrative review\b",
    r"\bscoping review\b",
    r"\bprogress report\b",
    r"\bstate of the art\b",
    r"\bmini-review\b",
    r"\bminireview\b",
    r"\bstudies reviewed here\b",
    r"\bliterature review\b",
    r"\beditorial\b",
    r"\bcommentary\b",
    r"\bletter to the editor\b",
)

# Weak review cues match title only (avoid methods-section "chart review", etc.).
REVIEW_WEAK_TITLE_CUES = (
    r"\breview\b",
    r"\bperspectives?\b",
)

# Methods-context "review" phrases that should not route to Node 1B.
REVIEW_SUPPRESS_PATTERNS = (
    r"\bchart review\b",
    r"\bretrospective review\b",
    r"\brecord review\b",
    r"\breview of (?:patient|medical) records\b",
    r"\bepidemiological overview\b",
    r"\boverview and survey\b",
)

REVIEW_TITLE_CUES = REVIEW_STRONG_CUES + REVIEW_WEAK_TITLE_CUES

CASE_CUES = (
    r"\bcase report\b",
    r"\bcase series\b",
    r"\bwe report a case\b",
    r"\bwe present a case\b",
    r"\bsingle patient\b",
    r"\bseries of patients\b",
    r"\bpresent a series of\b",
    r"\ba case of\b",
    r"\bcase of a patient\b",
)

ORIGINAL_NEGATIVE_CUES = (
    r"\bwe review\b",
    r"\bliterature suggests\b",
    r"\bprevious studies have shown\b",
    r"\bsummarize evidence\b",
)


def _load_rules_config() -> Dict[str, Any]:
    """Loads rules_config.json for Maude routing cues."""
    if not RULES_CONFIG_FILE.exists():
        return {}
    with open(RULES_CONFIG_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def get_routing_cue_patterns(node_id: str, fallback: Sequence[str]) -> Tuple[str, ...]:
    """Builds routing regex patterns from the unified Maude cue store plus fallbacks."""
    return maude_cues.get_routing_patterns(node_id, fallback)


def get_review_routing_patterns() -> Tuple[str, ...]:
    """Returns review-route regex patterns from config + learned cues."""
    return get_routing_cue_patterns("node1b_reviews", REVIEW_TITLE_CUES)


def get_case_routing_patterns() -> Tuple[str, ...]:
    """Returns case-report regex patterns from config + learned cues."""
    return get_routing_cue_patterns("node1c_case_report", CASE_CUES)


def get_original_negative_patterns() -> Tuple[str, ...]:
    """Returns review-negative regex patterns from the unified Maude cue store."""
    patterns = maude_cues.get_negative_patterns("node1a_original", ORIGINAL_NEGATIVE_CUES)
    return patterns or ORIGINAL_NEGATIVE_CUES


WEAK_REVIEW_PHRASES = frozenset({"review", "overview", "perspective", "perspectives"})


def get_review_strong_patterns() -> Tuple[str, ...]:
    """Returns high-precision review patterns (title or abstract) from cue store + fallbacks."""
    store = maude_cues.load_cue_store()
    patterns: List[str] = list(REVIEW_STRONG_CUES)
    seen = set(patterns)
    for phrase in maude_cues.get_positive_phrases("node1b_reviews", store):
        normalized = phrase.strip().lower()
        if not normalized or normalized in WEAK_REVIEW_PHRASES:
            continue
        pattern = rf"\b{re.escape(normalized)}\b"
        if pattern not in seen:
            patterns.append(pattern)
            seen.add(pattern)
    for row in maude_cues.get_node_config("node1b_reviews", store).get("learned_cues") or []:
        cue = (row.get("cue") or "").strip().lower()
        if not cue or cue in WEAK_REVIEW_PHRASES or not maude_cues.is_valid_review_learned_cue(cue):
            continue
        pattern = rf"\b{re.escape(cue)}\b"
        if pattern not in seen:
            patterns.append(pattern)
            seen.add(pattern)
    return tuple(patterns)


def get_review_weak_title_patterns() -> Tuple[str, ...]:
    """Returns title-only review patterns (base weak cues + learned weak cues)."""
    store = maude_cues.load_cue_store()
    patterns: List[str] = list(REVIEW_WEAK_TITLE_CUES)
    seen = set(patterns)
    for phrase in maude_cues.get_positive_phrases("node1b_reviews", store):
        if phrase.strip().lower() in WEAK_REVIEW_PHRASES:
            pattern = rf"\b{re.escape(phrase.strip().lower())}\b"
            if pattern not in seen:
                patterns.append(pattern)
                seen.add(pattern)
    for row in maude_cues.get_node_config("node1b_reviews", store).get("learned_cues") or []:
        cue = (row.get("cue") or "").strip().lower()
        if cue in WEAK_REVIEW_PHRASES and maude_cues.is_valid_review_learned_cue(cue):
            pattern = rf"\b{re.escape(cue)}\b"
            if pattern not in seen:
                patterns.append(pattern)
                seen.add(pattern)
    return tuple(patterns)


def get_review_suppress_patterns() -> Tuple[str, ...]:
    """Returns patterns that block weak title review routing (methods-context review)."""
    patterns = maude_cues.get_negative_patterns("node1b_reviews", REVIEW_SUPPRESS_PATTERNS)
    return patterns or REVIEW_SUPPRESS_PATTERNS


def matches_review_route(title: str, abstract: str) -> bool:
    """True when title/abstract cues justify Node 1B review routing."""
    text = f"{title} {abstract}"
    title_text = title or ""
    suppressed = any(re.search(pattern, text, re.IGNORECASE) for pattern in get_review_suppress_patterns())

    for pattern in get_review_strong_patterns():
        if re.search(pattern, text, re.IGNORECASE):
            return True

    if suppressed:
        return False

    for pattern in get_review_weak_title_patterns():
        if re.search(pattern, title_text, re.IGNORECASE):
            return True
    return False


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
) -> List[str]:
    """Resolves study_type using Node 1 publication route plus Node 2 branch fallbacks."""
    study_type = extractor.infer_study_type_for_publication(title, abstract, publication_type)
    if publication_type == "review" and review_subtype and review_subtype not in study_type:
        return [review_subtype]
    if study_type:
        full_blob = f"{title} {abstract}".lower()
        for label in extractor._collect_study_type_hits(full_blob):
            if label not in study_type:
                study_type.append(label)
        return study_type
    if publication_type != "original research":
        return study_type

    narrative_review = infer_narrative_review_study_type(title, abstract)
    if narrative_review:
        return narrative_review

    full_blob = f"{title} {abstract}".lower()
    branch_set = set(node2_branches)
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
    return ["Clinical (observational)"]


def apply_abstract_only_extraction_policy(result: Dict[str, Any]) -> Dict[str, Any]:
    """Clears downstream extraction fields for abstract-only classification (matches llm-reclassify)."""
    result["exposure_method"] = []
    result["cannabis_type"] = []
    result["outcome_domain"] = []
    result["species"] = None
    for field in (
        "thc_pct", "cbd_pct", "dose_mg", "strain_reported", "strain_normalized",
        "duration_days", "inhaled_exposure_duration", "administration_frequency",
        "treatment_duration", "repeat_exposure_count", "exposure_regimen_bin",
        "sample_size",
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


def should_extract_downstream_fields(
    full_text: Optional[str],
    abstract_only_extraction: Optional[bool],
) -> bool:
    """Returns whether Node 2+ extraction fields should be populated."""
    if abstract_only_extraction is not None:
        return not abstract_only_extraction
    return bool(full_text and full_text.strip())


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


def infer_species(text: str) -> Optional[str]:
    """Infers species label from routing/extraction text."""
    for pattern, label in SPECIES_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return None


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
    """Routes using PubMed publication-type prefixes and other metadata rules from maude_cues.json."""
    abstract_lower = (abstract or "").lower()
    title_lower = (title or "").lower()
    text = f"{title} {abstract}"
    for rule in maude_cues.get_metadata_routing_rules():
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


def route_publication_type(title: str, abstract: str) -> Tuple[str, Optional[str], List[str], float]:
    """Routes Node 1B/1C/1A and returns coarse publication_type, review subtype, nodes, cue score."""
    metadata_route = route_from_metadata(title, abstract)
    if metadata_route is not None:
        return metadata_route

    text = f"{title} {abstract}"
    nodes: List[str] = []
    score = 0.2
    case_patterns = get_case_routing_patterns()
    original_negative_patterns = get_original_negative_patterns()

    if matches_review_route(title, abstract):
        nodes.append("node1b_reviews")
        score += 0.35
        subtype = _detect_review_subtype(text)
        if subtype == "systematic review":
            return "review", subtype, nodes + ["node3a"], score + 0.15
        if subtype == "meta-analysis":
            return "review", subtype, nodes + ["node3b"], score + 0.15
        if subtype in {"editorial", "comment", "letter to the editor", "perspectives paper"}:
            return "review", subtype, nodes + ["node3c"], score + 0.1
        return "review", "review", nodes, score + 0.1

    for pattern in case_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            nodes.append("node1c_case_report")
            return "case study", "case study", nodes, score + 0.35

    if any(re.search(p, text, re.IGNORECASE) for p in original_negative_patterns):
        nodes.append("node1b_reviews")
        return "review", "review", nodes, score + 0.2

    nodes.append("node1a_original")
    return "original research", None, nodes, score + 0.25


def compute_maude_confidence(cue_score: float, nodes: Sequence[str]) -> float:
    """Confidence from cue strength and decision-tree depth (v1 default)."""
    depth_bonus = min(0.35, 0.04 * max(0, len(nodes) - 1))
    return round(min(0.95, max(0.35, cue_score + depth_bonus)), 3)


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
    extract_downstream = should_extract_downstream_fields(full_text, abstract_only_extraction)
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

    publication_type, review_subtype, route_nodes, route_score = route_publication_type(title, abstract)
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
        )
        if extract_downstream:
            heuristics = extractor.extract_all_heuristics(title, extraction_text)
            species = infer_species(extraction_text)
            exposure_method = heuristics.get("exposure_method") or []
            cannabis_type = heuristics.get("cannabis_type") or []
            outcome_domain = heuristics.get("outcome_domain") or []
            if not any(item.startswith("Animal Models") for item in study_type):
                species = None
    else:
        study_type = resolve_study_type_for_routing(
            title,
            abstract,
            publication_type,
            review_subtype,
            route_nodes,
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
        "thc_pct": heuristics.get("thc_pct"),
        "cbd_pct": heuristics.get("cbd_pct"),
        "dose_mg": heuristics.get("dose_mg"),
        "strain_reported": heuristics.get("strain_reported"),
        "strain_normalized": heuristics.get("strain_normalized"),
        "duration_days": heuristics.get("duration_days"),
        "inhaled_exposure_duration": heuristics.get("inhaled_exposure_duration"),
        "administration_frequency": heuristics.get("administration_frequency"),
        "treatment_duration": heuristics.get("treatment_duration"),
        "repeat_exposure_count": heuristics.get("repeat_exposure_count"),
        "exposure_regimen_bin": heuristics.get("exposure_regimen_bin"),
        "sample_size": heuristics.get("sample_size"),
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
