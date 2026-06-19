# maude_classifier.py
"""Rule/cue-based Maude classifier parallel to the LLM pipeline."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import extractor
import classification_schema

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

REVIEW_TITLE_CUES = (
    r"\breview\b",
    r"\boverview paper\b",
    r"\boverview\b",
    r"\bsystematic review\b",
    r"\bmeta-analysis\b",
    r"\bmeta analysis\b",
    r"\bnarrative synthesis\b",
    r"\bscoping review\b",
    r"\beditorial\b",
    r"\bcommentary\b",
    r"\bletter to the editor\b",
    r"\bperspectives?\b",
)

CASE_CUES = (
    r"\bcase report\b",
    r"\bcase series\b",
    r"\bwe report a case\b",
    r"\bsingle patient\b",
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


def _load_learned_cue_phrases(node_id: str) -> List[str]:
    """Loads learned positive cue phrases for a decision node."""
    learned_path = Path(
        os.getenv("CALIBRATION_OUTPUT_DIR")
        or ("/data/calibration_runs" if Path("/data/calibration_runs").exists() else "scratch/calibration_runs")
    ) / "maude_learned_cues.json"
    if not learned_path.exists():
        return []
    try:
        with open(learned_path, encoding="utf-8") as handle:
            store = json.load(handle)
    except Exception:
        return []
    phrases: List[str] = []
    for update in store.get("cue_updates") or []:
        if update.get("node_id") == node_id and update.get("cue"):
            phrases.append(str(update["cue"]))
    return phrases


def _phrases_to_patterns(phrases: Sequence[str]) -> Tuple[str, ...]:
    """Converts plain-text cue phrases into word-boundary regex patterns."""
    patterns: List[str] = []
    seen: set = set()
    for phrase in phrases:
        normalized = str(phrase).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        patterns.append(rf"\b{re.escape(normalized)}\b")
    return tuple(patterns)


def get_routing_cue_patterns(node_id: str, fallback: Sequence[str]) -> Tuple[str, ...]:
    """Builds routing regex patterns from rules_config, learned cues, and fallbacks."""
    config = _load_rules_config()
    node = (config.get("decision_nodes") or {}).get(node_id) or {}
    phrases = [str(item) for item in (node.get("positive_cues") or [])]
    phrases.extend(_load_learned_cue_phrases(node_id))
    patterns = list(_phrases_to_patterns(phrases))
    for pattern in fallback:
        if pattern not in patterns:
            patterns.append(pattern)
    return tuple(patterns)


def get_review_routing_patterns() -> Tuple[str, ...]:
    """Returns review-route regex patterns from config + learned cues."""
    return get_routing_cue_patterns("node1b_reviews", REVIEW_TITLE_CUES)


def get_case_routing_patterns() -> Tuple[str, ...]:
    """Returns case-report regex patterns from config + learned cues."""
    return get_routing_cue_patterns("node1c_case_report", CASE_CUES)


def get_original_negative_patterns() -> Tuple[str, ...]:
    """Returns review-negative regex patterns from config + learned cues."""
    config = _load_rules_config()
    node = (config.get("decision_nodes") or {}).get("node1a_original") or {}
    phrases = [str(item) for item in (node.get("negative_cues") or [])]
    return _phrases_to_patterns(phrases) or ORIGINAL_NEGATIVE_CUES


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
    if re.search(r"\bsystematic review\b", text, re.IGNORECASE):
        return "systematic review"
    if re.search(r"\bmeta-analysis\b|\bmeta analysis\b", text, re.IGNORECASE):
        return "meta-analysis"
    if re.search(r"\beditorial\b", text, re.IGNORECASE):
        return "editorial"
    if re.search(r"\bcommentary\b|\bcomment\b", text, re.IGNORECASE):
        return "comment"
    if re.search(r"\bletter to the editor\b", text, re.IGNORECASE):
        return "letter to the editor"
    if re.search(r"\bperspectives?\b", text, re.IGNORECASE):
        return "perspectives paper"
    return "review"


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


def route_publication_type(title: str, abstract: str) -> Tuple[str, Optional[str], List[str], float]:
    """Routes Node 1B/1C/1A and returns coarse publication_type, review subtype, nodes, cue score."""
    text = f"{title} {abstract}"
    nodes: List[str] = []
    score = 0.2
    review_patterns = get_review_routing_patterns()
    case_patterns = get_case_routing_patterns()
    original_negative_patterns = get_original_negative_patterns()

    for pattern in review_patterns:
        if re.search(pattern, text, re.IGNORECASE):
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
) -> Dict[str, Any]:
    """Runs the Maude rule/cue classifier and returns extraction fields plus metadata."""
    maude_cfg = load_maude_config()
    tree = load_maude_tree()
    routing_text = f"{title} {abstract}"
    methods_text = extract_methods_section(full_text)
    extraction_text = methods_text or routing_text

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
            },
        }, title, abstract)

    publication_type, review_subtype, route_nodes, route_score = route_publication_type(title, abstract)
    nodes = _dedupe_nodes(nodes + route_nodes)
    cue_score += route_score

    heuristics = extractor.extract_all_heuristics(title, extraction_text)
    species = infer_species(extraction_text)
    if publication_type == "review":
        study_type = [review_subtype or "review"]
    elif publication_type == "case study":
        study_type = ["case study"]
    else:
        study_type = _map_study_type_to_tree(heuristics.get("study_type") or [], species)

    if publication_type == "original research":
        blob = extraction_text.lower()
        if any(token in blob for token in ("participants", "patients", "randomized", "clinical trial", "cohort")):
            nodes.append("node2a_clinical")
            cue_score += 0.15
        if any(token in blob for token in ("mouse", "mice", "rat", "hamster", "in vivo", "gavage", "sprague")):
            nodes.append("node2b_in_vivo")
            cue_score += 0.15
        if any(token in blob for token in ("cell line", "in vitro", "cultured", "organoid", "primary cells")):
            nodes.append("node2c_in_vitro")
            cue_score += 0.15
        if len({n for n in nodes if n.startswith("node2")}) > 1:
            nodes.append("node2d_mixed")

    exposure_method = heuristics.get("exposure_method") or []
    cannabis_type = heuristics.get("cannabis_type") or []
    if publication_type in {"review", "case study"}:
        exposure_method = []
        cannabis_type = []

    confidence = compute_maude_confidence(cue_score, nodes)
    partial_result = {
        "ingestion_status": ingestion_status,
        "publication_type": publication_type,
        "study_type": study_type,
        "exposure_method": exposure_method,
        "cannabis_type": cannabis_type,
        "outcome_domain": heuristics.get("outcome_domain") or [],
        "species": species,
        "dose_mg": heuristics.get("dose_mg"),
        "thc_pct": heuristics.get("thc_pct"),
        "cbd_pct": heuristics.get("cbd_pct"),
        "sample_size": heuristics.get("sample_size"),
        "strain_reported": heuristics.get("strain_reported"),
    }
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
        "outcome_domain": heuristics.get("outcome_domain") or [],
        "species": species,
        "thc_pct": heuristics.get("thc_pct"),
        "cbd_pct": heuristics.get("cbd_pct"),
        "dose_mg": heuristics.get("dose_mg"),
        "strain_reported": heuristics.get("strain_reported"),
        "strain_normalized": heuristics.get("strain_normalized"),
        "duration_days": heuristics.get("duration_days"),
        "sample_size": heuristics.get("sample_size"),
        "classification_confidence": confidence,
        "_maude_meta": {
            "classifier": "maude",
            "rules_version": rules_version,
            "tree_version": tree.get("version"),
            "nodes_visited": nodes,
            "cue_score": round(cue_score, 3),
            "methods_used": bool(methods_text),
        },
    }
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
