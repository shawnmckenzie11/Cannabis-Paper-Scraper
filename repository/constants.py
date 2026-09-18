"""Shared paper-table SQL constants used by the repository and db_manager re-exports."""

from __future__ import annotations

from typing import Any, Dict

SQL_ORIGINAL_RESEARCH = (
    "("
    "  papers.publication_type = 'original research'"
    "  OR"
    "  (papers.publication_type IS NULL AND ("
    "    (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND NOT EXISTS ("
    "        SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
    "    ))"
    "    OR"
    "    ((NOT json_valid(papers.study_type) OR json_type(papers.study_type) != 'array') AND (papers.study_type IS NULL OR papers.study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial')))"
    "  ))"
    ")"
)

SQL_REVIEW_PUBLICATION = (
    "("
    "  (papers.publication_type IS NOT NULL AND papers.publication_type != 'original research')"
    "  OR"
    "  (papers.publication_type IS NULL AND ("
    "    (json_valid(papers.study_type) AND json_type(papers.study_type) = 'array' AND EXISTS ("
    "        SELECT 1 FROM json_each(papers.study_type) WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')"
    "    ))"
    "    OR"
    "    (papers.study_type IN ('review', 'meta-analysis', 'case study', 'editorial'))"
    "  ))"
    ")"
)

SQL_INGESTION_NOT_CANNABIS = (
    "LOWER(COALESCE(papers.ingestion_status, '')) IN ('not_cannabis_related', 'not cannabis-related')"
)

SQL_INGESTION_IRRELEVANT = "LOWER(COALESCE(papers.ingestion_status, '')) = 'irrelevant'"

SQL_INGESTION_TANGENTIAL = "LOWER(COALESCE(papers.ingestion_status, '')) = 'tangential'"

SQL_HAS_PDF_LINK = (
    "(papers.full_text_link IS NOT NULL AND TRIM(papers.full_text_link) != ''"
    " AND LOWER(papers.full_text_link) LIKE '%.pdf')"
)

SQL_HAS_FULL_TEXT_LINK = (
    "(papers.full_text_link IS NOT NULL AND TRIM(papers.full_text_link) != ''"
    " AND LOWER(papers.full_text_link) NOT LIKE '%pubmed.ncbi.nlm.nih.gov/%')"
)

SQL_INGESTION_ROUTED = (
    "("
    f"  {SQL_INGESTION_NOT_CANNABIS}"
    "  OR "
    f"  {SQL_INGESTION_IRRELEVANT}"
    "  OR "
    f"  {SQL_INGESTION_TANGENTIAL}"
    ")"
)

SQL_CLINICAL_STUDY = (
    "("
    "  LOWER(COALESCE(papers.study_type, '')) LIKE '%clinical%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rct%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%prospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%retrospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%observational%'"
    ")"
)

SQL_PRECLINICAL_STUDY = (
    "("
    "  LOWER(COALESCE(papers.study_type, '')) LIKE '%animal%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%mouse%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rat%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rodent%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%in vivo%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%cell culture%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%vitro%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%organoid%'"
    ")"
)

# LIKE-only tab predicates used on Postgres when indexed tab_* columns are absent.
# Avoids json_each (SQLite-only) without the deprecated dialect rewriter.
SQL_ORIGINAL_RESEARCH_LIKE = (
    "("
    "  papers.publication_type = 'original research'"
    "  OR ("
    "    papers.publication_type IS NULL"
    "    AND (papers.study_type IS NULL OR papers.study_type NOT IN "
    "      ('review', 'meta-analysis', 'case study', 'editorial'))"
    "  )"
    ")"
)

SQL_REVIEW_PUBLICATION_LIKE = (
    "("
    "  (papers.publication_type IS NOT NULL AND papers.publication_type != 'original research')"
    "  OR (papers.publication_type IS NULL AND papers.study_type IN "
    "    ('review', 'meta-analysis', 'case study', 'editorial'))"
    ")"
)

TAB_SQL = {
    "all_original": f"({SQL_ORIGINAL_RESEARCH} AND NOT {SQL_INGESTION_ROUTED})",
    "preclinical": (
        f"({SQL_ORIGINAL_RESEARCH} AND NOT {SQL_INGESTION_ROUTED} AND {SQL_PRECLINICAL_STUDY})"
    ),
    "clinical": (
        f"({SQL_ORIGINAL_RESEARCH} AND NOT {SQL_INGESTION_ROUTED} AND {SQL_CLINICAL_STUDY})"
    ),
    "unclassified_preclinical": (
        f"({SQL_ORIGINAL_RESEARCH} AND NOT {SQL_INGESTION_ROUTED}"
        f" AND NOT {SQL_CLINICAL_STUDY} AND NOT {SQL_PRECLINICAL_STUDY})"
    ),
    "unclassified": (
        f"({SQL_INGESTION_TANGENTIAL}) OR "
        f"({SQL_ORIGINAL_RESEARCH} AND NOT {SQL_INGESTION_ROUTED}"
        f" AND NOT {SQL_CLINICAL_STUDY} AND NOT {SQL_PRECLINICAL_STUDY})"
    ),
    "tangential": f"({SQL_INGESTION_TANGENTIAL})",
    "review": f"({SQL_REVIEW_PUBLICATION} AND NOT {SQL_INGESTION_ROUTED})",
}

TAB_SQL_LIKE = {
    "all_original": f"({SQL_ORIGINAL_RESEARCH_LIKE} AND NOT {SQL_INGESTION_ROUTED})",
    "preclinical": (
        f"({SQL_ORIGINAL_RESEARCH_LIKE} AND NOT {SQL_INGESTION_ROUTED} AND {SQL_PRECLINICAL_STUDY})"
    ),
    "clinical": (
        f"({SQL_ORIGINAL_RESEARCH_LIKE} AND NOT {SQL_INGESTION_ROUTED} AND {SQL_CLINICAL_STUDY})"
    ),
    "unclassified_preclinical": (
        f"({SQL_ORIGINAL_RESEARCH_LIKE} AND NOT {SQL_INGESTION_ROUTED}"
        f" AND NOT {SQL_CLINICAL_STUDY} AND NOT {SQL_PRECLINICAL_STUDY})"
    ),
    "unclassified": (
        f"({SQL_INGESTION_TANGENTIAL}) OR "
        f"({SQL_ORIGINAL_RESEARCH_LIKE} AND NOT {SQL_INGESTION_ROUTED}"
        f" AND NOT {SQL_CLINICAL_STUDY} AND NOT {SQL_PRECLINICAL_STUDY})"
    ),
    "tangential": f"({SQL_INGESTION_TANGENTIAL})",
    "review": f"({SQL_REVIEW_PUBLICATION_LIKE} AND NOT {SQL_INGESTION_ROUTED})",
}

DASHBOARD_TAB_KEYS = (
    "all_original",
    "preclinical",
    "clinical",
    "review",
    "unclassified",
)

TABLE_LIST_COLUMNS = (
    "papers.id",
    "papers.pmid",
    "papers.doi",
    "papers.title",
    "papers.authors",
    "papers.journal",
    "papers.year",
    "papers.full_text_link",
    "papers.study_type",
    "papers.publication_type",
    "papers.exposure_method",
    "papers.cannabis_type",
    "papers.thc_pct",
    "papers.cbd_pct",
    "papers.dose_mg",
    "papers.puff_count",
    "papers.thc_mg_ml",
    "papers.thc_mg_g",
    "papers.thc_mg_kg",
    "papers.cbd_mg_ml",
    "papers.cbd_mg_g",
    "papers.cbd_mg_kg",
    "papers.thc_uM",
    "papers.cbd_uM",
    "papers.strain_reported",
    "papers.strain_normalized",
    "papers.duration_days",
    "papers.inhaled_exposure_duration",
    "papers.administration_frequency",
    "papers.treatment_duration",
    "papers.repeat_exposure_count",
    "papers.exposure_regimen_bin",
    "papers.sample_size",
    "papers.outcome_domain",
    "papers.open_access",
    "papers.citation_count",
    "papers.date_harvested",
    "papers.expert_locked_fields",
    "papers.classification_confidence",
    "papers.classifier_version",
    "papers.ingestion_status",
    "papers.species",
    "papers.population_age",
    "papers.population_sex",
)

PAPER_WRITE_FIELDS = (
    "pmid",
    "doi",
    "semantic_scholar_id",
    "title",
    "authors",
    "journal",
    "year",
    "abstract",
    "full_text_link",
    "study_type",
    "exposure_method",
    "thc_pct",
    "cbd_pct",
    "dose_mg",
    "puff_count",
    "thc_mg_ml",
    "thc_mg_g",
    "thc_mg_kg",
    "cbd_mg_ml",
    "cbd_mg_g",
    "cbd_mg_kg",
    "thc_uM",
    "cbd_uM",
    "strain_reported",
    "strain_normalized",
    "duration_days",
    "inhaled_exposure_duration",
    "administration_frequency",
    "treatment_duration",
    "sample_size",
    "outcome_domain",
    "open_access",
    "citation_count",
    "date_harvested",
    "publication_date",
    "cannabis_type",
    "summary",
    "publication_type",
    "ingestion_status",
    "species",
    "population_age",
    "population_sex",
    "inclusion_criteria",
    "exclusion_criteria",
    "expert_locked_fields",
    "classification_confidence",
    "classification_timestamp",
    "classifier_version",
)

JSON_LIST_FIELDS = (
    "authors",
    "outcome_domain",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "expert_locked_fields",
)

SPECIES_FILTER_TARGETS: Dict[str, Dict[str, Any]] = {
    "mouse": {
        "study_types": ["Animal Models (Mouse)"],
        "species_like": ["%mouse%"],
    },
    "rat": {
        "study_types": ["Animal Models (Rat)"],
        "species_like": ["%rat%"],
    },
    "rodent": {
        "study_types": [
            "Animal Models (Mouse)",
            "Animal Models (Rat)",
            "Animal Models (Other Rodents)",
        ],
        "species_like": ["%rodent%", "%rodent_other%", "%mouse%", "%rat%"],
    },
    "hamster": {
        "study_types": ["Animal Models (Other Rodents)"],
        "species_like": ["%hamster%", "%rodent_other%"],
    },
    "guinea pig": {
        "study_types": ["Animal Models (Other Rodents)"],
        "species_like": ["%guinea%", "%rodent_other%"],
    },
    "non-human primate": {
        "study_types": ["Animal Models (Non-Human Primates)"],
        "species_like": ["%non_human_primate%", "%non-human primate%"],
    },
    "rabbit": {
        "study_types": ["Animal Models (Other Rodents)", "Animal Models (Other)"],
        "species_like": ["%rabbit%", "%rodent_other%", "%other_mammal%"],
    },
    "dog": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%dog%", "%other_mammal%"],
    },
    "pig": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%pig%", "%porcine%", "%other_mammal%"],
    },
    "zebrafish": {
        "study_types": ["Animal Models (Other)"],
        "species_like": ["%zebrafish%", "%vertebrate_non_mammal%"],
    },
}

NUMERIC_RANGE_FILTERS = (
    ("sample_size_min", "papers.sample_size", ">="),
    ("sample_size_max", "papers.sample_size", "<="),
    ("dose_mg_min", "papers.dose_mg", ">="),
    ("dose_mg_max", "papers.dose_mg", "<="),
    ("duration_days_min", "papers.duration_days", ">="),
    ("duration_days_max", "papers.duration_days", "<="),
    ("thc_mg_kg_min", "papers.thc_mg_kg", ">="),
    ("thc_mg_kg_max", "papers.thc_mg_kg", "<="),
    ("cbd_mg_kg_min", "papers.cbd_mg_kg", ">="),
    ("cbd_mg_kg_max", "papers.cbd_mg_kg", "<="),
    ("thc_mg_ml_min", "papers.thc_mg_ml", ">="),
    ("thc_mg_ml_max", "papers.thc_mg_ml", "<="),
    ("cbd_mg_ml_min", "papers.cbd_mg_ml", ">="),
    ("cbd_mg_ml_max", "papers.cbd_mg_ml", "<="),
    ("thc_uM_min", "papers.thc_uM", ">="),
    ("thc_uM_max", "papers.thc_uM", "<="),
    ("cbd_uM_min", "papers.cbd_uM", ">="),
    ("cbd_uM_max", "papers.cbd_uM", "<="),
    ("puff_count_min", "papers.puff_count", ">="),
)
