"""Database UI tab membership helpers.

Tab routing is denormalized into indexed integer columns on ``papers`` so list
queries avoid expensive ``LIKE``/``json_each`` filters at request time.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional

TAB_FLAG_FIELDS: Dict[str, str] = {
    "preclinical": "tab_preclinical",
    "clinical": "tab_clinical",
    "unclassified_preclinical": "tab_unclassified_preclinical",
    "tangential": "tab_tangential",
    "review": "tab_review",
}

REVIEW_STUDY_TYPES = frozenset({"review", "meta-analysis", "case study", "editorial"})
INGESTION_ROUTED_VALUES = frozenset(
    {"not_cannabis_related", "not cannabis-related", "irrelevant", "tangential"}
)
CLINICAL_KEYWORDS = ("clinical", "rct", "prospective", "retrospective", "observational")
PRECLINICAL_KEYWORDS = (
    "animal",
    "mouse",
    "rat",
    "rodent",
    "in vivo",
    "cell culture",
    "vitro",
    "organoid",
)


def _normalize_ingestion(ingestion_status: Optional[str]) -> str:
    """Return a lower-case ingestion label."""
    return (ingestion_status or "").strip().lower()


def _study_type_blob(study_type: Any) -> str:
    """Flatten study_type JSON/string values into a searchable blob."""
    if study_type is None:
        return ""
    if isinstance(study_type, list):
        return " ".join(str(item) for item in study_type).lower()
    if isinstance(study_type, str):
        raw = study_type.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return raw.lower()
            if isinstance(parsed, list):
                return " ".join(str(item) for item in parsed).lower()
        return raw.lower()
    return str(study_type).lower()


def _study_type_values(study_type: Any) -> Iterable[str]:
    """Yield normalized study_type tokens."""
    blob = _study_type_blob(study_type)
    if not blob:
        return ()
    if isinstance(study_type, list):
        return tuple(str(item).strip().lower() for item in study_type if str(item).strip())
    if isinstance(study_type, str):
        raw = study_type.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return (blob,)
            if isinstance(parsed, list):
                return tuple(str(item).strip().lower() for item in parsed if str(item).strip())
        return (raw.lower(),)
    return (blob,)


def is_ingestion_routed(ingestion_status: Optional[str]) -> bool:
    """True when a paper is routed out of primary study/review tabs."""
    return _normalize_ingestion(ingestion_status) in INGESTION_ROUTED_VALUES


def is_original_research(publication_type: Optional[str], study_type: Any = None) -> bool:
    """True when a paper belongs to the original-research bucket."""
    if publication_type:
        return publication_type == "original research"
    values = set(_study_type_values(study_type))
    return not values.intersection(REVIEW_STUDY_TYPES)


def is_review_publication(publication_type: Optional[str], study_type: Any = None) -> bool:
    """True when a paper belongs to the review/meta bucket."""
    if publication_type:
        return publication_type != "original research"
    values = set(_study_type_values(study_type))
    return bool(values.intersection(REVIEW_STUDY_TYPES))


def is_clinical_study(study_type: Any) -> bool:
    """True when study_type indicates a clinical design."""
    blob = _study_type_blob(study_type)
    return any(keyword in blob for keyword in CLINICAL_KEYWORDS)


def is_preclinical_study(study_type: Any) -> bool:
    """True when study_type indicates an in vivo or in vitro design."""
    blob = _study_type_blob(study_type)
    return any(keyword in blob for keyword in PRECLINICAL_KEYWORDS)


def compute_tab_flags(
    publication_type: Optional[str] = None,
    study_type: Any = None,
    ingestion_status: Optional[str] = None,
) -> Dict[str, int]:
    """Return indexed tab membership flags for a paper."""
    routed = is_ingestion_routed(ingestion_status)
    tangential = _normalize_ingestion(ingestion_status) == "tangential"
    original = is_original_research(publication_type, study_type)
    review = is_review_publication(publication_type, study_type)
    clinical = is_clinical_study(study_type)
    preclinical = is_preclinical_study(study_type)

    return {
        "tab_preclinical": int(original and not routed and preclinical),
        "tab_clinical": int(original and not routed and clinical),
        "tab_unclassified_preclinical": int(original and not routed and not clinical and not preclinical),
        "tab_tangential": int(tangential),
        "tab_review": int(review and not routed),
    }


def apply_tab_flags_to_record(record: Mapping[str, Any]) -> Dict[str, int]:
    """Compute tab flags from a paper-like mapping and return them."""
    return compute_tab_flags(
        publication_type=record.get("publication_type"),
        study_type=record.get("study_type"),
        ingestion_status=record.get("ingestion_status"),
    )


def tab_sql_for(tab: str) -> str:
    """Return the indexed WHERE fragment for a database UI tab."""
    column = TAB_FLAG_FIELDS.get(tab)
    if not column:
        return ""
    return f"papers.{column} = 1"


BACKFILL_TAB_FLAGS_SQL_FAST = """
UPDATE papers SET
  tab_tangential = CASE
    WHEN LOWER(COALESCE(ingestion_status, '')) = 'tangential' THEN 1 ELSE 0 END,
  tab_preclinical = CASE
    WHEN publication_type = 'original research'
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND (
        LOWER(COALESCE(study_type, '')) LIKE '%animal%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%mouse%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rat%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rodent%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%in vivo%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%cell culture%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%vitro%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%organoid%'
      )
    THEN 1 ELSE 0 END,
  tab_clinical = CASE
    WHEN publication_type = 'original research'
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND (
        LOWER(COALESCE(study_type, '')) LIKE '%clinical%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rct%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%prospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%retrospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%observational%'
      )
    THEN 1 ELSE 0 END,
  tab_unclassified_preclinical = CASE
    WHEN publication_type = 'original research'
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND NOT (
        LOWER(COALESCE(study_type, '')) LIKE '%clinical%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rct%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%prospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%retrospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%observational%'
      )
      AND NOT (
        LOWER(COALESCE(study_type, '')) LIKE '%animal%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%mouse%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rat%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rodent%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%in vivo%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%cell culture%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%vitro%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%organoid%'
      )
    THEN 1 ELSE 0 END,
  tab_review = CASE
    WHEN publication_type != 'original research'
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
    THEN 1 ELSE 0 END
"""

_LEGACY_SQL_ORIGINAL_RESEARCH = (
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

_LEGACY_SQL_REVIEW_PUBLICATION = (
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

_LEGACY_SQL_INGESTION_ROUTED = (
    "("
    "  LOWER(COALESCE(papers.ingestion_status, '')) IN ('not_cannabis_related', 'not cannabis-related')"
    "  OR LOWER(COALESCE(papers.ingestion_status, '')) = 'irrelevant'"
    "  OR LOWER(COALESCE(papers.ingestion_status, '')) = 'tangential'"
    ")"
)

_LEGACY_SQL_CLINICAL_STUDY = (
    "("
    "  LOWER(COALESCE(papers.study_type, '')) LIKE '%clinical%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%rct%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%prospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%retrospective%'"
    "  OR LOWER(COALESCE(papers.study_type, '')) LIKE '%observational%'"
    ")"
)

_LEGACY_SQL_PRECLINICAL_STUDY = (
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

LEGACY_TAB_SQL = {
    "preclinical": (
        f"({_LEGACY_SQL_ORIGINAL_RESEARCH} AND NOT {_LEGACY_SQL_INGESTION_ROUTED} AND {_LEGACY_SQL_PRECLINICAL_STUDY})"
    ),
    "clinical": (
        f"({_LEGACY_SQL_ORIGINAL_RESEARCH} AND NOT {_LEGACY_SQL_INGESTION_ROUTED} AND {_LEGACY_SQL_CLINICAL_STUDY})"
    ),
    "unclassified_preclinical": (
        f"({_LEGACY_SQL_ORIGINAL_RESEARCH} AND NOT {_LEGACY_SQL_INGESTION_ROUTED}"
        f" AND NOT {_LEGACY_SQL_CLINICAL_STUDY} AND NOT {_LEGACY_SQL_PRECLINICAL_STUDY})"
    ),
    "tangential": "LOWER(COALESCE(papers.ingestion_status, '')) = 'tangential'",
    "review": f"({_LEGACY_SQL_REVIEW_PUBLICATION} AND NOT {_LEGACY_SQL_INGESTION_ROUTED})",
}


def legacy_tab_sql_for(tab: str) -> str:
    """Return the legacy runtime tab filter used before indexed flags are ready."""
    return LEGACY_TAB_SQL.get(tab, "")


BACKFILL_TAB_FLAGS_SQL = """
UPDATE papers SET
  tab_tangential = CASE
    WHEN LOWER(COALESCE(ingestion_status, '')) = 'tangential' THEN 1 ELSE 0 END,
  tab_preclinical = CASE
    WHEN (
      (publication_type = 'original research'
       OR (
         publication_type IS NULL
         AND (
           (json_valid(study_type) AND json_type(study_type) = 'array' AND NOT EXISTS (
             SELECT 1 FROM json_each(study_type)
             WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')
           ))
           OR (
             (NOT json_valid(study_type) OR json_type(study_type) != 'array')
             AND (study_type IS NULL OR study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial'))
           )
         )
       ))
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND (
        LOWER(COALESCE(study_type, '')) LIKE '%animal%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%mouse%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rat%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rodent%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%in vivo%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%cell culture%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%vitro%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%organoid%'
      )
    ) THEN 1 ELSE 0 END,
  tab_clinical = CASE
    WHEN (
      (publication_type = 'original research'
       OR (
         publication_type IS NULL
         AND (
           (json_valid(study_type) AND json_type(study_type) = 'array' AND NOT EXISTS (
             SELECT 1 FROM json_each(study_type)
             WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')
           ))
           OR (
             (NOT json_valid(study_type) OR json_type(study_type) != 'array')
             AND (study_type IS NULL OR study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial'))
           )
         )
       ))
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND (
        LOWER(COALESCE(study_type, '')) LIKE '%clinical%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rct%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%prospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%retrospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%observational%'
      )
    ) THEN 1 ELSE 0 END,
  tab_unclassified_preclinical = CASE
    WHEN (
      (publication_type = 'original research'
       OR (
         publication_type IS NULL
         AND (
           (json_valid(study_type) AND json_type(study_type) = 'array' AND NOT EXISTS (
             SELECT 1 FROM json_each(study_type)
             WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')
           ))
           OR (
             (NOT json_valid(study_type) OR json_type(study_type) != 'array')
             AND (study_type IS NULL OR study_type NOT IN ('review', 'meta-analysis', 'case study', 'editorial'))
           )
         )
       ))
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
      AND NOT (
        LOWER(COALESCE(study_type, '')) LIKE '%clinical%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rct%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%prospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%retrospective%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%observational%'
      )
      AND NOT (
        LOWER(COALESCE(study_type, '')) LIKE '%animal%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%mouse%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rat%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%rodent%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%in vivo%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%cell culture%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%vitro%'
        OR LOWER(COALESCE(study_type, '')) LIKE '%organoid%'
      )
    ) THEN 1 ELSE 0 END,
  tab_review = CASE
    WHEN (
      (
        (publication_type IS NOT NULL AND publication_type != 'original research')
        OR (
          publication_type IS NULL
          AND (
            (json_valid(study_type) AND json_type(study_type) = 'array' AND EXISTS (
              SELECT 1 FROM json_each(study_type)
              WHERE json_each.value IN ('review', 'meta-analysis', 'case study', 'editorial')
            ))
            OR study_type IN ('review', 'meta-analysis', 'case study', 'editorial')
          )
        )
      )
      AND LOWER(COALESCE(ingestion_status, '')) NOT IN (
        'not_cannabis_related', 'not cannabis-related', 'irrelevant', 'tangential'
      )
    ) THEN 1 ELSE 0 END
"""
