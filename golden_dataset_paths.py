"""Decision-tree path endpoint definitions for golden dataset construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import subnode_field_scopes

# Canonical labels from Cannabis_Classification_Decision_Tree / rules_config.json
CLINICAL_STUDY_TYPES: Tuple[str, ...] = (
    "Clinical (RCT)",
    "Clinical (prospective)",
    "Clinical (retrospective)",
    "Clinical (observational)",
    "case study",
)

CLINICAL_EXPOSURE_METHODS: Tuple[str, ...] = (
    "inhaled",
    "oral",
    "sublingual",
    "injected",
)

IN_VIVO_STUDY_TYPES: Tuple[str, ...] = (
    "Animal Models (Mouse)",
    "Animal Models (Rat)",
    "Animal Models (Other Rodents)",
    "Animal Models (Non-Human Primates)",
    "Animal Models (Other)",
)

IN_VIVO_EXPOSURE_METHODS: Tuple[str, ...] = tuple(subnode_field_scopes.IN_VIVO_EXPOSURE_TO_NODE7.keys())

IN_VITRO_STUDY_TYPES: Tuple[str, ...] = (
    "Cell Culture (Primary Cells)",
    "Cell Culture (Cell Lines)",
    "Cell Culture (Co-Culture)",
    "Cell Culture (Organoids)",
    "Cell Culture (PCLS)",
    "Cell Culture (Other In Vitro)",
)

IN_VITRO_EXPOSURE_METHODS: Tuple[str, ...] = tuple(subnode_field_scopes.IN_VITRO_EXPOSURE_TO_NODE7.keys())

# Aliases for matching DB exposure_method values to canonical tree labels.
EXPOSURE_MATCH_ALIASES: Dict[str, Tuple[str, ...]] = {
    "inhaled": ("inhaled", "whole body. smoke/vapor", "nose only smoke/vapor", "smoking", "vaping"),
    "oral": ("oral", "oral administration", "oral gavage", "edible"),
    "sublingual": ("sublingual", "sub-lingual"),
    "injected": ("injected", "injection cannabinoids", "intravenous", "intramuscular"),
    "whole body. smoke/vapor": (
        "whole body. smoke/vapor",
        "whole body smoke",
        "whole-body",
        "whole body exposure",
        "whole-body exposure",
        "smoke chamber",
        "inhalation chamber",
        "whole-body chamber",
    ),
    "nose only smoke/vapor": ("nose only smoke/vapor", "nose-only", "nose only"),
    "injection cannabinoids": ("injection cannabinoids", "intraperitoneal", "intravenous", "subcutaneous"),
    "oral administration": ("oral administration", "oral gavage", "gavage"),
    "sub-lingual": ("sub-lingual", "sublingual"),
    "intranasal": ("intranasal", "nasal"),
    "intratracheal": ("intratracheal", "tracheal instillation", "intratracheal instillation"),
    "cannabinoids dissolved in media": ("cannabinoids dissolved in media", "dissolved in media"),
    "smoke/vapor conditioned media": ("smoke/vapor conditioned media", "conditioned media", "cse"),
    "exposure of cells to smoke/vapor": (
        "exposure of cells to smoke/vapor",
        "direct smoke",
        "direct vapor",
        "air-liquid interface",
        "ali",
    ),
}

# Title/abstract keyword cues for endpoint fallback matching (decision-tree cues).
STUDY_TYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "Clinical (RCT)": (
        "randomized",
        "double-blind",
        "placebo-controlled",
        "placebo controlled",
        "rct",
        "clinical trial",
    ),
    "Clinical (prospective)": (
        "prospective",
        "longitudinal",
        "followed for",
        "enrolled and followed",
        "cohort",
    ),
    "Clinical (retrospective)": (
        "retrospective",
        "chart review",
        "historical cohort",
        "medical records",
    ),
    "Clinical (observational)": (
        "cross-sectional",
        "survey",
        "registry",
        "case-control",
        "epidemiological",
        "gwas",
        "observational",
    ),
    "case study": ("case report", "case series", "single patient", "we report a case"),
    "Animal Models (Mouse)": ("mouse", "mice", "murine", "c57bl"),
    "Animal Models (Rat)": ("rat", "wistar", "sprague-dawley", "sprague dawley"),
    "Animal Models (Other Rodents)": ("hamster", "gerbil", "guinea pig", "vole"),
    "Animal Models (Non-Human Primates)": (
        "macaque",
        "rhesus",
        "monkey",
        "baboon",
        "non-human primate",
    ),
    "Animal Models (Other)": (
        "dog",
        "canine",
        "cat",
        "pig",
        "porcine",
        "zebrafish",
        "drosophila",
        "rabbit",
        "frog",
        "xenopus",
        "avian",
    ),
    "Cell Culture (Primary Cells)": (
        "primary cells",
        "primary microglia",
        "primary hepatocytes",
        "splenocytes",
    ),
    "Cell Culture (Cell Lines)": (
        "cell line",
        "hela",
        "hepg2",
        "raw 264.7",
        "a549",
        "hek293",
        "thp-1",
    ),
    "Cell Culture (Co-Culture)": ("co-culture", "co culture", "mixed culture"),
    "Cell Culture (Organoids)": ("organoid", "spheroid", "3d culture"),
    "Cell Culture (PCLS)": (
        "precision-cut lung",
        "precision cut lung",
        "pcls",
        "lung slices",
        "lung slice",
        "ex vivo lung",
    ),
    "Cell Culture (Other In Vitro)": (
        "in vitro",
        "cell culture",
        "cultured",
        "incubated",
        "assay",
    ),
}

REVIEW_PUBLICATION_TYPES: frozenset[str] = frozenset(
    {
        "review",
        "systematic review",
        "meta-analysis",
        "editorial",
        "comment",
        "letter to the editor",
        "perspectives paper",
    }
)

REVIEW_STUDY_TYPES: frozenset[str] = frozenset(
    {
        "review",
        "systematic review",
        "meta-analysis",
        "editorial",
        "comment",
        "letter to the editor",
        "perspectives paper",
    }
)


@dataclass(frozen=True)
class TreePathEndpoint:
    """One leaf path in the classification decision tree."""

    id: str
    label: str
    branch: str
    study_types: Tuple[str, ...] = ()
    exposure_methods: Tuple[str, ...] = ()
    publication_types: Tuple[str, ...] = ()
    review_study_types: Tuple[str, ...] = ()
    scope_subnode: str = ""
    scope_key: str = ""


def _clinical_endpoints() -> List[TreePathEndpoint]:
    """Builds Node 2A × Node 4 study-type × clinical exposure endpoints."""
    endpoints: List[TreePathEndpoint] = []
    for study in CLINICAL_STUDY_TYPES:
        slug_study = study.lower().replace(" ", "_").replace("(", "").replace(")", "")
        for exposure in CLINICAL_EXPOSURE_METHODS:
            slug_exp = exposure.replace(" ", "_").replace("-", "_")
            endpoints.append(
                TreePathEndpoint(
                    id=f"node2a.{slug_study}.{slug_exp}",
                    label=f"Node 2A · {study} · {exposure}",
                    branch="clinical",
                    study_types=(study,),
                    exposure_methods=(exposure,),
                    scope_subnode="node2a",
                    scope_key="node2a",
                )
            )
    return endpoints


def _in_vivo_endpoints() -> List[TreePathEndpoint]:
    """Builds Node 2B × Node 5 species branch × Node 7 in vivo exposure endpoints."""
    endpoints: List[TreePathEndpoint] = []
    for study in IN_VIVO_STUDY_TYPES:
        slug_study = study.lower().replace(" ", "_").replace("(", "").replace(")", "")
        for exposure in IN_VIVO_EXPOSURE_METHODS:
            path_id = subnode_field_scopes.IN_VIVO_EXPOSURE_TO_NODE7[exposure]
            slug_exp = exposure.replace(" ", "_").replace(".", "").replace("-", "_").replace("/", "_")
            endpoints.append(
                TreePathEndpoint(
                    id=f"node2b.{slug_study}.{slug_exp}",
                    label=f"Node 2B · {study} · {exposure}",
                    branch="in_vivo",
                    study_types=(study,),
                    exposure_methods=(exposure,),
                    scope_subnode="node2b",
                    scope_key=f"node7_in_vivo.{path_id}",
                )
            )
    return endpoints


def _in_vitro_endpoints() -> List[TreePathEndpoint]:
    """Builds Node 2C × Node 6 cell-culture type × Node 7 in vitro exposure endpoints."""
    endpoints: List[TreePathEndpoint] = []
    for study in IN_VITRO_STUDY_TYPES:
        slug_study = study.lower().replace(" ", "_").replace("(", "").replace(")", "")
        for exposure in IN_VITRO_EXPOSURE_METHODS:
            path_id = subnode_field_scopes.IN_VITRO_EXPOSURE_TO_NODE7[exposure]
            slug_exp = exposure.replace(" ", "_").replace("/", "_")
            endpoints.append(
                TreePathEndpoint(
                    id=f"node2c.{slug_study}.{slug_exp}",
                    label=f"Node 2C · {study} · {exposure}",
                    branch="in_vitro",
                    study_types=(study,),
                    exposure_methods=(exposure,),
                    scope_subnode="node2c",
                    scope_key=f"node7_in_vitro.{path_id}",
                )
            )
    return endpoints


def _review_endpoints() -> List[TreePathEndpoint]:
    """Builds Node 3 review subtype endpoints."""
    return [
        TreePathEndpoint(
            id="node3a.systematic_review",
            label="Node 3A · Systematic Review",
            branch="review",
            publication_types=("systematic review", "review"),
            review_study_types=("systematic review",),
            scope_subnode="node3a",
            scope_key="node3a",
        ),
        TreePathEndpoint(
            id="node3b.meta_analysis",
            label="Node 3B · Meta-analysis",
            branch="review",
            publication_types=("meta-analysis", "review"),
            review_study_types=("meta-analysis",),
            scope_subnode="node3b",
            scope_key="node3b",
        ),
        TreePathEndpoint(
            id="node3c.narrative_editorial",
            label="Node 3C · Narrative / Editorial / Comment / Letter / Perspectives",
            branch="review",
            publication_types=(
                "review",
                "editorial",
                "comment",
                "letter to the editor",
                "perspectives paper",
            ),
            review_study_types=(
                "review",
                "editorial",
                "comment",
                "letter to the editor",
                "perspectives paper",
            ),
            scope_subnode="node3c",
            scope_key="node3c",
        ),
    ]


def all_tree_path_endpoints() -> List[TreePathEndpoint]:
    """Returns every decision-tree path endpoint including review branches."""
    return (
        _clinical_endpoints()
        + _in_vivo_endpoints()
        + _in_vitro_endpoints()
        + _review_endpoints()
    )


def non_review_tree_path_endpoints() -> List[TreePathEndpoint]:
    """Returns original-research path endpoints only (no Node 3 review branches)."""
    return [endpoint for endpoint in all_tree_path_endpoints() if endpoint.branch != "review"]


def endpoint_by_id(endpoint_id: str) -> Optional[TreePathEndpoint]:
    """Returns the TreePathEndpoint for a canonical endpoint id, if known."""
    target = str(endpoint_id or "").strip()
    if not target:
        return None
    for endpoint in all_tree_path_endpoints():
        if endpoint.id == target:
            return endpoint
    return None


def sorted_non_review_endpoints() -> List[TreePathEndpoint]:
    """Returns non-review endpoints sorted by PDF classification pool (desc) from golden JSON."""
    golden_path = Path("scratch/golden_dataset/tree_path_golden.json")
    endpoint_map = {ep.id: ep for ep in non_review_tree_path_endpoints()}
    if golden_path.exists():
        try:
            with open(golden_path, encoding="utf-8") as handle:
                golden = json.load(handle)
            blocks = sort_endpoints_by_pdf_class_pool(golden.get("endpoints") or [])
            ordered: List[TreePathEndpoint] = []
            for block in blocks:
                endpoint_id = str(block.get("endpoint_id") or "")
                if endpoint_id in endpoint_map:
                    ordered.append(endpoint_map[endpoint_id])
            if ordered:
                return ordered
        except Exception:
            pass
    return list(endpoint_map.values())


def _normalize_list_field(value: Any) -> List[str]:
    """Parses study_type / exposure_method / cannabis_type from DB row values."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
    return [text]


def _clean_label(value: str) -> str:
    """Lowercases and strips a label for substring comparisons."""
    return str(value or "").strip().lower()


def _study_type_matches(paper_study_types: Sequence[str], target: str) -> bool:
    """Returns True when the paper carries the canonical study_type label."""
    target_clean = _clean_label(target)
    for item in paper_study_types:
        if _clean_label(item) == target_clean:
            return True
    return False


def _exposure_matches(paper_exposures: Sequence[str], canonical: str) -> bool:
    """Returns True when paper exposure_method matches a canonical tree label."""
    if not paper_exposures:
        return False
    aliases = EXPOSURE_MATCH_ALIASES.get(canonical, (canonical,))
    paper_lower = [_clean_label(item) for item in paper_exposures]
    blob = " ".join(paper_lower)
    for alias in aliases:
        alias_clean = _clean_label(alias)
        if alias_clean in paper_lower:
            return True
        if alias_clean and alias_clean in blob:
            return True
    return False


def _review_matches(endpoint: TreePathEndpoint, paper: Dict[str, Any]) -> bool:
    """Returns True when a review paper belongs on a Node 3 endpoint."""
    pub = _clean_label(paper.get("publication_type"))
    study_types = [_clean_label(item) for item in _normalize_list_field(paper.get("study_type"))]
    study_blob = " ".join(study_types)

    if endpoint.id == "node3a.systematic_review":
        return "systematic review" in study_types or pub == "systematic review"

    if endpoint.id == "node3b.meta_analysis":
        return "meta-analysis" in study_types or pub == "meta-analysis"

    # node3c: narrative / editorial paths — exclude systematic review and meta-analysis
    if "systematic review" in study_types or pub == "systematic review":
        return False
    if "meta-analysis" in study_types or pub == "meta-analysis":
        return False

    review_labels = {_clean_label(item) for item in endpoint.review_study_types}
    if any(label in study_types for label in review_labels):
        return True
    return pub in {_clean_label(item) for item in endpoint.publication_types}


def paper_matches_endpoint(paper: Dict[str, Any], endpoint: TreePathEndpoint) -> bool:
    """Returns True when a classified paper sits on the given tree path endpoint."""
    if endpoint.branch == "review":
        return _review_matches(endpoint, paper)

    study_types = _normalize_list_field(paper.get("study_type"))
    exposures = _normalize_list_field(paper.get("exposure_method"))

    study_ok = any(_study_type_matches(study_types, target) for target in endpoint.study_types)
    exposure_ok = any(_exposure_matches(exposures, target) for target in endpoint.exposure_methods)
    return study_ok and exposure_ok


def scope_fields_for_endpoint(endpoint: TreePathEndpoint) -> List[str]:
    """Returns characteristic fields counted for papers on this endpoint."""
    import calibration_metrics

    high_level = calibration_metrics.NODE_CHARACTERISTICS.get(endpoint.scope_subnode) or []
    scoped = subnode_field_scopes.SUBNODE_FIELD_SCOPES.get(endpoint.scope_key) or []
    merged: List[str] = []
    seen: set = set()
    for field in list(high_level) + list(scoped):
        if field not in seen:
            seen.add(field)
            merged.append(field)
    return merged


def normalize_title_match_key(title: Optional[str]) -> str:
    """Normalize a title for deduplicating preprint/published versions of the same work."""
    import re

    text = str(title or "").lower()
    text = re.sub(r"[δΔ]\s*9", "delta9", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def canonical_bibliographic_key(paper: Dict[str, Any]) -> str:
    """Return a stable key for deduplicating duplicate PubMed/preprint records."""
    title_key = normalize_title_match_key(paper.get("title"))
    if len(title_key.split()) >= 8:
        return f"title:{title_key}"
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    pmid = str(paper.get("pmid") or "").strip()
    if pmid:
        return f"pmid:{pmid}"
    return f"title:{title_key}" if title_key else f"id:{paper.get('id')}"


def _preprint_penalty(paper: Dict[str, Any]) -> int:
    """Score penalty for bioRxiv/medRxiv preprints when a published version exists."""
    doi = str(paper.get("doi") or "").strip().lower()
    link = str(paper.get("full_text_link") or "").strip().lower()
    if doi.startswith("10.1101/") or doi.startswith("10.64898/"):
        return 1
    if "biorxiv" in link or "medrxiv" in link:
        return 1
    return 0


def prefer_golden_candidate(candidate: Dict[str, Any], incumbent: Dict[str, Any]) -> bool:
    """Return True when candidate should replace incumbent for the same bibliographic work."""
    cand_status = str(candidate.get("ingestion_status") or "").strip().lower()
    inc_status = str(incumbent.get("ingestion_status") or "").strip().lower()
    if cand_status == "relevant" and inc_status != "relevant":
        return True
    if inc_status == "relevant" and cand_status != "relevant":
        return False

    cand_preprint = _preprint_penalty(candidate)
    inc_preprint = _preprint_penalty(incumbent)
    if cand_preprint != inc_preprint:
        return cand_preprint < inc_preprint

    cand_year = int(candidate.get("year") or 0)
    inc_year = int(incumbent.get("year") or 0)
    if cand_year != inc_year:
        return cand_year > inc_year

    cand_citations = int(candidate.get("citation_count") or 0)
    inc_citations = int(incumbent.get("citation_count") or 0)
    if cand_citations != inc_citations:
        return cand_citations > inc_citations

    return int(candidate.get("id") or 0) > int(incumbent.get("id") or 0)


def is_review_paper(paper: Dict[str, Any]) -> bool:
    """Returns True when a paper is review / secondary literature (excluded from golden set)."""
    pub = _clean_label(paper.get("publication_type"))
    if pub in REVIEW_PUBLICATION_TYPES:
        return True
    for item in _normalize_list_field(paper.get("study_type")):
        if _clean_label(item) in REVIEW_STUDY_TYPES:
            return True
    return False


def _paper_search_blob(paper: Dict[str, Any]) -> str:
    """Concatenates searchable bibliographic text for keyword endpoint matching."""
    parts = [
        str(paper.get("title") or ""),
        str(paper.get("abstract") or ""),
        str(paper.get("summary") or ""),
    ]
    return " ".join(parts)


def endpoint_keyword_terms(endpoint: TreePathEndpoint) -> Tuple[List[str], List[str]]:
    """Returns study-type and exposure keyword lists for an endpoint."""
    study_terms: List[str] = []
    for study in endpoint.study_types:
        study_terms.extend(STUDY_TYPE_KEYWORDS.get(study, (study.lower(),)))

    exposure_terms: List[str] = []
    for exposure in endpoint.exposure_methods:
        exposure_terms.extend(EXPOSURE_MATCH_ALIASES.get(exposure, (exposure,)))

    dedupe_study: List[str] = []
    seen_study: set = set()
    for term in study_terms:
        key = term.lower()
        if key not in seen_study:
            seen_study.add(key)
            dedupe_study.append(term)

    dedupe_exposure: List[str] = []
    seen_exposure: set = set()
    for term in exposure_terms:
        key = term.lower()
        if key not in seen_exposure:
            seen_exposure.add(key)
            dedupe_exposure.append(term)

    return dedupe_study, dedupe_exposure


def paper_matches_endpoint_keywords(paper: Dict[str, Any], endpoint: TreePathEndpoint) -> bool:
    """Returns True when title/abstract contains endpoint study + exposure keyword cues."""
    if is_review_paper(paper):
        return False

    blob = _paper_search_blob(paper).lower()
    if not blob.strip():
        return False

    study_terms, exposure_terms = endpoint_keyword_terms(endpoint)
    study_ok = any(term.lower() in blob for term in study_terms) if study_terms else True
    exposure_ok = any(term.lower() in blob for term in exposure_terms) if exposure_terms else True
    return study_ok and exposure_ok


def _endpoint_full_text_pool_size(endpoint: Dict[str, Any]) -> int:
    """Returns the largest full-text match pool for an endpoint result dict."""
    return max(
        int(endpoint.get("pool_size_full_text_classification") or 0),
        int(endpoint.get("pool_size_full_text_keywords") or 0),
    )


def sort_endpoints_by_pdf_class_pool(
    endpoints: Sequence[Dict[str, Any]],
    pdf_class_target: int = 10,
) -> List[Dict[str, Any]]:
    """
    Sorts endpoint summaries by PDF classification pool (descending), then
    full-text pool (descending) as tiebreaker.
    """
    _ = pdf_class_target  # callers pass top_n_per_endpoint; sort is independent
    return sorted(
        endpoints,
        key=lambda ep: (
            -int(ep.get("pool_size_pdf_classification") or 0),
            -_endpoint_full_text_pool_size(ep),
            str(ep.get("endpoint_id") or ""),
        ),
    )

