"""Dashboard UI tab and filter profile configuration.

Builds tab-specific sidebar filter visibility from ``rules_config.json`` so future
paper-scraper deployments can clone and customize filter profiles without
hard-coding taxonomy in ``templates/index.html``.

Filter tier policy (see ``docs/projects/project-1/architecture-design-document.md`` §5.4):
  - **Global bar** (top horizontal row): §5.1 core bibliographic fields + ``has_pdf`` / ``has_full_text`` only.
  - **Sidebar** (per tab): §5.2 routing metadata + §5.3 branch extraction fields via ``FILTER_PROFILES``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

RULES_CONFIG_PATH = os.getenv("RULES_CONFIG_PATH", "rules_config.json")

STUDY_TYPE_CLINICAL = [
    "Clinical (RCT)",
    "Clinical (prospective)",
    "Clinical (observational)",
    "Clinical (retrospective)",
]

STUDY_TYPE_ANIMAL = [
    "Animal Models (Mouse)",
    "Animal Models (Rat)",
    "Animal Models (Other Rodents)",
    "Animal Models (Non-Human Primates)",
    "Animal Models (Other)",
]

STUDY_TYPE_CELL = [
    "Cell Culture (Primary Cells)",
    "Cell Culture (Cell Lines)",
    "Cell Culture (Organoids)",
    "Cell Culture (Co-Culture)",
    "Cell Culture (PCLS)",
    "Cell Culture (Other In Vitro)",
]

EXPOSURE_CLINICAL = ["inhaled", "oral", "sublingual", "injected"]

EXPOSURE_IN_VITRO = [
    "exposure of cells to smoke/vapor",
    "cannabinoids dissolved in media",
    "smoke/vapor conditioned media",
]

EXPOSURE_IN_VIVO = [
    "whole body. smoke/vapor",
    "nose only smoke/vapor",
    "injection cannabinoids",
    "oral administration",
    "sub-lingual",
    "intranasal",
    "intratracheal",
]

CANNABIS_TYPES = [
    "dried flower",
    "concentrates",
    "vape pen",
    "pure cannabinoid",
    "edibles",
    "hashish/kief",
    "CB receptor agonist",
    "CB receptor antagonist",
]

OUTCOME_DOMAINS = [
    "pain",
    "anxiety",
    "cognition",
    "inflammation",
    "addiction",
    "oncology",
    "neuroprotection",
    "sleep",
]

SPECIES_OPTIONS = [
    "mouse",
    "rat",
    "rodent",
    "hamster",
    "guinea pig",
    "non-human primate",
    "rabbit",
    "dog",
    "pig",
    "zebrafish",
]

EXPOSURE_REGIMEN_OPTIONS = ["acute", "subchronic", "chronic"]

PUBLICATION_TYPE_OPTIONS = [
    "review",
    "systematic review",
    "meta-analysis",
    "editorial",
    "comment",
    "letter to the editor",
    "perspectives paper",
    "case study",
]

# §5.1 core bibliographic record — only these (+ derived has_pdf/has_full_text) belong in the global bar.
SCHEMA_TIER_51_FIELDS: Tuple[str, ...] = (
    "pmid",
    "doi",
    "title",
    "authors",
    "journal",
    "year",
    "abstract",
    "full_text_link",
    "date_harvested",
    "publication_date",
)

# Global filter controls: param keys allowed in #global-filters-bar (not sidebar).
GLOBAL_FILTER_CONTROLS: Tuple[Dict[str, Any], ...] = (
    {
        "element_id": "filter-query",
        "label": "Search Articles",
        "param": "query",
        "schema_tier": "5.1",
        "fields": ["title", "abstract", "authors", "journal", "pmid", "doi"],
    },
    {
        "element_id": "filter-recent-range",
        "label": "Recently Harvested",
        "param": "recent_range",
        "schema_tier": "5.1",
        "fields": ["date_harvested"],
    },
    {
        "element_id": "filter-has-pdf",
        "label": "PDF",
        "param": "has_pdf",
        "schema_tier": "5.1",
        "fields": ["has_pdf"],
        "control_type": "checkbox",
    },
    {
        "element_id": "filter-has-full-text",
        "label": "Full Text",
        "param": "has_full_text",
        "schema_tier": "5.1",
        "fields": ["full_text_link"],
        "control_type": "checkbox",
    },
)

GLOBAL_FILTER_PARAMS: Set[str] = {item["param"] for item in GLOBAL_FILTER_CONTROLS}

# Sidebar filter sections: tier 5.2 (routing) vs 5.3 (branch extraction).
FILTER_SECTION_REGISTRY: Dict[str, Dict[str, Any]] = {
    "classification_details": {
        "tier": "5.2",
        "fields": ["classification_source", "classifier_version", "classification_confidence"],
        "param": "classification_level",
    },
    "publication_type": {
        "tier": "5.2",
        "fields": ["publication_type"],
        "param": "publication_type",
    },
    "study_type_all": {"tier": "5.2", "fields": ["study_type"], "param": "study_type"},
    "study_type_clinical": {"tier": "5.2", "fields": ["study_type"], "param": "study_type"},
    "study_type_animal": {"tier": "5.2", "fields": ["study_type"], "param": "study_type"},
    "study_type_cell": {"tier": "5.2", "fields": ["study_type"], "param": "study_type"},
    "exposure_all": {"tier": "5.3", "fields": ["exposure_method"], "param": "exposure_method"},
    "exposure_clinical": {"tier": "5.3", "fields": ["exposure_method"], "param": "exposure_method"},
    "exposure_in_vivo": {"tier": "5.3", "fields": ["exposure_method"], "param": "exposure_method"},
    "exposure_in_vitro": {"tier": "5.3", "fields": ["exposure_method"], "param": "exposure_method"},
    "species": {"tier": "5.3", "fields": ["species"], "param": "species"},
    "cannabis_type": {"tier": "5.3", "fields": ["cannabis_type"], "param": "cannabis_type"},
    "thc_pct": {"tier": "5.3", "fields": ["thc_pct"], "param": "thc_min"},
    "cbd_pct": {"tier": "5.3", "fields": ["cbd_pct"], "param": "cbd_min"},
    "year": {"tier": "5.1", "fields": ["year"], "param": "year_min", "sidebar_only": True},
    "outcomes": {"tier": "5.3", "fields": ["outcome_domain"], "param": "outcome"},
    "sample_size": {"tier": "5.3", "fields": ["sample_size"], "param": "sample_size_min"},
    "dose_clinical": {"tier": "5.3", "fields": ["dose_mg"], "param": "dose_mg_min"},
    "duration_clinical": {"tier": "5.3", "fields": ["duration_days"], "param": "duration_days_min"},
    "study_population": {
        "tier": "5.3",
        "fields": ["population_age", "population_sex"],
        "param": "population_age",
    },
    "dose_in_vivo": {"tier": "5.3", "fields": ["thc_mg_kg", "cbd_mg_kg"], "param": "thc_mg_kg_min"},
    "dose_in_vitro": {"tier": "5.3", "fields": ["thc_mg_ml", "cbd_mg_ml", "thc_uM", "cbd_uM"], "param": "thc_mg_ml_min"},
    "exposure_regimen": {"tier": "5.3", "fields": ["exposure_regimen_bin"], "param": "exposure_regimen_bin"},
    "puff_count": {"tier": "5.3", "fields": ["puff_count"], "param": "puff_count_min"},
}

DASHBOARD_TABS: List[Dict[str, str]] = [
    {"key": "all_original", "label": "All Original Research"},
    {"key": "preclinical", "label": "Pre-Clinical"},
    {"key": "clinical", "label": "Clinical"},
    {"key": "review", "label": "Reviews & Meta-Analyses"},
    {"key": "unclassified", "label": "Unclassified"},
]

FILTER_PROFILES: Dict[str, Dict[str, Any]] = {
    "all_original": {
        "sections": [
            "classification_details",
            "study_type_all",
            "exposure_all",
            "cannabis_type",
            "thc_pct",
            "year",
            "outcomes",
        ],
    },
    "preclinical": {
        "node": "node2b+node2c",
        "sections": [
            "classification_details",
            "study_type_animal",
            "study_type_cell",
            "exposure_in_vivo",
            "exposure_in_vitro",
            "species",
            "cannabis_type",
            "thc_pct",
            "cbd_pct",
            "year",
            "outcomes",
            "dose_in_vivo",
            "dose_in_vitro",
            "exposure_regimen",
            "puff_count",
        ],
    },
    "clinical": {
        "node": "node2a",
        "sections": [
            "classification_details",
            "study_type_clinical",
            "exposure_clinical",
            "cannabis_type",
            "thc_pct",
            "cbd_pct",
            "year",
            "outcomes",
            "sample_size",
            "dose_clinical",
            "duration_clinical",
            "study_population",
            "puff_count",
        ],
    },
    "review": {
        "node": "node1b",
        "sections": [
            "classification_details",
            "publication_type",
            "year",
        ],
    },
    "unclassified": {
        "sections": [
            "classification_details",
            "year",
        ],
    },
}

ANALYZE_SUBSET_TABS = ["all_original", "preclinical", "clinical"]

DEFAULT_TAB = "all_original"


def _load_rules_config() -> Dict[str, Any]:
    """Load rules_config.json when present; return empty dict on failure."""
    try:
        with open(RULES_CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _study_types_from_rules(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract study_type lists from node2a/b/c sub_branches when available."""
    nodes = config.get("decision_nodes", {})
    clinical: List[str] = []
    animal: List[str] = []
    cell: List[str] = []

    node2a = nodes.get("node2a_clinical", {}).get("sub_branches", {})
    for values in node2a.values():
        clinical.extend(values)

    node2b = nodes.get("node2b_in_vivo", {}).get("sub_branches", {})
    for values in node2b.values():
        animal.extend(values)

    node2c = nodes.get("node2c_in_vitro", {}).get("sub_branches", {})
    for values in node2c.values():
        cell.extend(values)

    return {
        "clinical": clinical or STUDY_TYPE_CLINICAL,
        "animal": animal or STUDY_TYPE_ANIMAL,
        "cell": cell or STUDY_TYPE_CELL,
    }


def allowed_global_filter_params() -> Set[str]:
    """Return API param keys permitted in the global filter bar."""
    return set(GLOBAL_FILTER_PARAMS)


def sections_for_tab(tab_key: str) -> List[str]:
    """Return sidebar filter section ids enabled for a dashboard tab."""
    profile = FILTER_PROFILES.get(tab_key, {})
    return list(profile.get("sections") or [])


def validate_filter_config() -> List[str]:
    """Return human-readable violations of the global vs tab filter tier policy."""
    errors: List[str] = []

    for section_id, meta in FILTER_SECTION_REGISTRY.items():
        param_key = meta.get("param")
        if meta.get("tier") in ("5.2", "5.3") and param_key in GLOBAL_FILTER_PARAMS:
            errors.append(
                f"Section {section_id!r} (tier {meta.get('tier')}) param {param_key!r} must not be global"
            )

    for tab_key, profile in FILTER_PROFILES.items():
        for section_id in profile.get("sections") or []:
            if section_id not in FILTER_SECTION_REGISTRY:
                errors.append(f"Tab {tab_key!r} references unknown filter section {section_id!r}")
            elif section_id == "classification_details":
                continue
            elif FILTER_SECTION_REGISTRY[section_id].get("tier") not in ("5.2", "5.3", "5.1"):
                errors.append(f"Tab {tab_key!r} section {section_id!r} has invalid tier")

    index_path = Path(__file__).resolve().parent / "templates" / "index.html"
    if index_path.is_file():
        errors.extend(_validate_index_html_filters(index_path.read_text(encoding="utf-8")))

    return errors


def _validate_index_html_filters(html: str) -> List[str]:
    """Parse index.html and flag global-bar controls outside the allowlist."""
    errors: List[str] = []
    global_match = re.search(
        r'id="global-filters-bar"[^>]*>(.*?)</div>\s*\n\s*<!-- FULL PAGE WIDTH TABS -->',
        html,
        re.DOTALL,
    )
    if not global_match:
        errors.append("Could not locate #global-filters-bar in templates/index.html")
        return errors

    global_html = global_match.group(1)
    allowed_ids = {item["element_id"] for item in GLOBAL_FILTER_CONTROLS}
    for element_id in re.findall(r'id="(filter-[^"]+)"', global_html):
        if element_id not in allowed_ids:
            errors.append(
                f"Global filter bar contains disallowed control id={element_id!r}; "
                f"allowed: {sorted(allowed_ids)}"
            )

    if "filter-classification-level" in global_html:
        errors.append(
            "Classification Details (filter-classification-level) must be in the sidebar, not the global bar"
        )

    return errors


def build_dashboard_ui_config() -> Dict[str, Any]:
    """Return the full dashboard UI config object for Jinja/JSON injection."""
    rules = _load_rules_config()
    study_types = _study_types_from_rules(rules)

    return {
        "default_tab": DEFAULT_TAB,
        "tabs": DASHBOARD_TABS,
        "filter_profiles": FILTER_PROFILES,
        "global_filters": list(GLOBAL_FILTER_CONTROLS),
        "global_filter_params": sorted(GLOBAL_FILTER_PARAMS),
        "filter_section_registry": FILTER_SECTION_REGISTRY,
        "schema_tier_51_fields": list(SCHEMA_TIER_51_FIELDS),
        "analyze_subset_tabs": ANALYZE_SUBSET_TABS,
        "taxonomy": {
            "study_type_clinical": study_types["clinical"],
            "study_type_animal": study_types["animal"],
            "study_type_cell": study_types["cell"],
            "study_type_all": (
                study_types["clinical"] + study_types["animal"] + study_types["cell"]
            ),
            "exposure_clinical": EXPOSURE_CLINICAL,
            "exposure_in_vivo": EXPOSURE_IN_VIVO,
            "exposure_in_vitro": EXPOSURE_IN_VITRO,
            "exposure_all": EXPOSURE_CLINICAL + EXPOSURE_IN_VIVO + EXPOSURE_IN_VITRO + ["unknown"],
            "cannabis_type": CANNABIS_TYPES,
            "outcome_domain": OUTCOME_DOMAINS,
            "species": SPECIES_OPTIONS,
            "exposure_regimen_bin": EXPOSURE_REGIMEN_OPTIONS,
            "publication_type": PUBLICATION_TYPE_OPTIONS,
        },
        "rules_config_version": rules.get("version"),
    }
