"""Dashboard UI tab and filter profile configuration.

Builds tab-specific sidebar filter visibility from ``rules_config.json`` so future
paper-scraper deployments can clone and customize filter profiles without
hard-coding taxonomy in ``templates/index.html``.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

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

# Global bar: Search Articles, Recently Harvested, has_pdf, has_full_text only (§5.1).
# See production dashboard_ui_config.py GLOBAL_FILTER_CONTROLS — do not customize tier rules.

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


def build_dashboard_ui_config() -> Dict[str, Any]:
    """Return the full dashboard UI config object for Jinja/JSON injection."""
    rules = _load_rules_config()
    study_types = _study_types_from_rules(rules)

    return {
        "default_tab": DEFAULT_TAB,
        "tabs": DASHBOARD_TABS,
        "filter_profiles": FILTER_PROFILES,
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
