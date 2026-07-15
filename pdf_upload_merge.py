"""Fuzzy title matching and field review/merge helpers for PDF uploads."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

PDF_UPLOAD_REVIEW_FIELDS: Tuple[str, ...] = (
    "title",
    "abstract",
    "publication_type",
    "year",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "species",
    "outcome_domain",
    "sample_size",
    "population_age",
    "population_sex",
    "inclusion_criteria",
    "exclusion_criteria",
    "thc_pct",
    "cbd_pct",
    "dose_mg",
    "puff_count",
    "inhaled_exposure_duration",
    "administration_frequency",
    "duration_days",
    "treatment_duration",
    "thc_mg_ml",
    "cbd_mg_ml",
    "thc_mg_g",
    "cbd_mg_g",
    "thc_mg_kg",
    "cbd_mg_kg",
    "thc_uM",
    "cbd_uM",
    "strain_reported",
    "strain_normalized",
    "summary",
    "classifier_version",
    "classification_confidence",
)

PDF_UPLOAD_REVIEW_LABELS: Dict[str, str] = {
    "title": "Title",
    "abstract": "Abstract",
    "publication_type": "Publication Type",
    "year": "Year",
    "study_type": "Study Type",
    "exposure_method": "Exposure Method",
    "cannabis_type": "Cannabis Type",
    "species": "Species",
    "outcome_domain": "Outcome Domain",
    "sample_size": "Sample Size",
    "population_age": "Population Age",
    "population_sex": "Population Sex",
    "inclusion_criteria": "Inclusion Criteria",
    "exclusion_criteria": "Exclusion Criteria",
    "thc_pct": "THC %",
    "cbd_pct": "CBD %",
    "dose_mg": "Dose (mg)",
    "puff_count": "Puff Count",
    "inhaled_exposure_duration": "Inhaled Exposure Duration",
    "administration_frequency": "Admin Frequency",
    "duration_days": "Duration (days)",
    "treatment_duration": "Treatment Duration",
    "thc_mg_ml": "THC (mg/mL)",
    "cbd_mg_ml": "CBD (mg/mL)",
    "thc_mg_g": "THC (mg/g)",
    "cbd_mg_g": "CBD (mg/g)",
    "thc_mg_kg": "THC (mg/kg)",
    "cbd_mg_kg": "CBD (mg/kg)",
    "thc_uM": "THC (µM)",
    "cbd_uM": "CBD (µM)",
    "strain_reported": "Strain Reported",
    "strain_normalized": "Strain Normalized",
    "summary": "Summary",
    "classifier_version": "Classifier Version",
    "classification_confidence": "Classification Confidence",
}

PDF_UPLOAD_LIST_FIELDS = frozenset({
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "authors",
})

PDF_UPLOAD_NUMERIC_FIELDS = frozenset({
    "year",
    "sample_size",
    "puff_count",
    "thc_pct",
    "cbd_pct",
    "dose_mg",
    "duration_days",
    "thc_mg_ml",
    "cbd_mg_ml",
    "thc_mg_g",
    "cbd_mg_g",
    "thc_mg_kg",
    "cbd_mg_kg",
    "thc_uM",
    "cbd_uM",
    "classification_confidence",
})

PDF_UPLOAD_INTEGER_FIELDS = frozenset({
    "year",
    "sample_size",
    "puff_count",
})

# Closed-ended options aligned with the paper edit drawer / filter sidebar.
PDF_UPLOAD_ENUM_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "publication_type": (
        "original research",
        "review",
        "case study",
        "systematic review",
        "meta-analysis",
        "editorial",
        "comment",
        "letter to the editor",
        "perspectives paper",
    ),
    "study_type": (
        "Clinical (RCT)",
        "Clinical (prospective)",
        "Clinical (observational)",
        "Clinical (retrospective)",
        "Animal Models (Mouse)",
        "Animal Models (Rat)",
        "Animal Models (Other Rodents)",
        "Animal Models (Non-Human Primates)",
        "Animal Models (Other)",
        "Cell Culture (Primary Cells)",
        "Cell Culture (Cell Lines)",
        "Cell Culture (Organoids)",
        "Cell Culture (Co-Culture)",
        "Cell Culture (PCLS)",
        "Cell Culture (Other In Vitro)",
        "review",
        "meta-analysis",
        "case study",
        "editorial",
    ),
    "exposure_method": (
        "smoked",
        "vaporized",
        "inhaled",
        "oral/edible",
        "tincture",
        "sublingual",
        "injection",
        "forced inhalation",
        "nose only smoke/vapor",
        "whole body. smoke/vapor",
        "injection cannabinoids",
        "oral administration",
        "sub-lingual",
        "intranasal",
        "intratracheal",
        "exposure of cells to smoke/vapor",
        "smoke/vapor conditioned media",
        "cannabinoids dissolved in media",
        "in vitro",
        "unknown",
    ),
    "cannabis_type": (
        "dried flower",
        "concentrates",
        "vape pen",
        "pure cannabinoid",
        "edibles",
        "hashish/kief",
        "CB receptor agonist",
        "CB receptor antagonist",
        "unknown",
    ),
    "outcome_domain": (
        "pain",
        "anxiety",
        "cognition",
        "inflammation",
        "addiction",
        "oncology",
        "neuroprotection",
        "sleep",
        "other",
    ),
    "population_age": (
        "pediatric",
        "adult",
        "geriatric",
        "both",
    ),
    "population_sex": (
        "male",
        "female",
        "both",
    ),
    "strain_normalized": (
        "Chemotype I",
        "Chemotype II",
        "Chemotype III",
    ),
}

PDF_UPLOAD_MULTI_ENUM_FIELDS = frozenset({
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
})

# Backward-compatible alias used in older tests/callers.
PDF_UPLOAD_MERGE_FIELDS = PDF_UPLOAD_REVIEW_FIELDS
PDF_UPLOAD_MERGE_LABELS = PDF_UPLOAD_REVIEW_LABELS


def get_review_field_input_schema() -> Dict[str, Dict[str, Any]]:
    """Return per-field input metadata for the PDF review 'Other' controls."""
    schema: Dict[str, Dict[str, Any]] = {}
    for field in PDF_UPLOAD_REVIEW_FIELDS:
        if field in PDF_UPLOAD_ENUM_OPTIONS:
            schema[field] = {
                "input_type": "enum",
                "multiple": field in PDF_UPLOAD_MULTI_ENUM_FIELDS,
                "options": list(PDF_UPLOAD_ENUM_OPTIONS[field]),
            }
        elif field in PDF_UPLOAD_NUMERIC_FIELDS:
            schema[field] = {
                "input_type": "number",
                "integer": field in PDF_UPLOAD_INTEGER_FIELDS,
                "min": 0,
            }
        elif field in {"abstract", "summary", "inclusion_criteria", "exclusion_criteria"}:
            schema[field] = {"input_type": "textarea"}
        else:
            schema[field] = {"input_type": "text"}
    return schema


DEFAULT_TITLE_SIMILARITY_THRESHOLD = 0.82
TITLE_MATCH_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "that", "this", "study",
    "using", "among", "based", "after", "before", "between", "about",
    "findings", "effects", "effect", "role", "use", "used", "over",
})


def clean_title_for_matching(title: str) -> str:
    """Strip author/filename prefixes so titles compare more reliably."""
    text = (title or "").strip()
    if not text:
        return ""
    # "Milad - Dried Cannabis Use..." / "Smith: Some Long Enough Title..."
    # Require a letter-only name-like prefix (no digits) so "COVID-19 - ..." is kept.
    prefixed = re.match(
        r"^([A-Za-z][A-Za-z'.]{0,30}(?:\s+[A-Za-z][A-Za-z'.]{0,30}){0,2})"
        r"\s*[-–—:]\s+(.{20,})$",
        text,
    )
    if prefixed:
        text = prefixed.group(2).strip()
    return text


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy comparison."""
    cleaned = clean_title_for_matching(title)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def title_similarity(left: str, right: str) -> float:
    """Return a 0–1 similarity ratio between two titles.

    Exact normalized matches score 1.0. When one normalized title contains the
    other (common for truncated PDF filenames vs full catalog titles), score is
    boosted so near-duplicates rank above unrelated papers.
    """
    variants_left = {
        normalize_title(left),
        normalize_title(clean_title_for_matching(left)),
    }
    variants_right = {
        normalize_title(right),
        normalize_title(clean_title_for_matching(right)),
    }
    variants_left = {v for v in variants_left if v}
    variants_right = {v for v in variants_right if v}
    if not variants_left or not variants_right:
        return 0.0
    best = 0.0
    for a in variants_left:
        for b in variants_right:
            if a == b:
                return 1.0
            best = max(best, difflib.SequenceMatcher(None, a, b).ratio())
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) >= 24 and shorter in longer:
                # Truncated title contained in full title (or vice versa).
                containment = len(shorter) / max(len(longer), 1)
                best = max(best, 0.88 + 0.12 * containment)
    return best


def significant_title_tokens(title: str, *, limit: int = 8) -> List[str]:
    """Extract informative tokens for candidate retrieval."""
    tokens = [
        tok for tok in normalize_title(title).split()
        if len(tok) >= 4 and tok not in TITLE_MATCH_STOPWORDS
    ]
    tokens.sort(key=lambda t: (-len(t), t))
    return tokens[:limit]


def title_token_like_pattern(title: str, *, max_tokens: int = 10) -> str:
    """Build a punctuation-tolerant SQL LIKE pattern from title tokens.

    Example: "Dried Cannabis Use, COVID-19" -> "%dried%cannabis%use%covid%19%"
    so commas/hyphens in stored titles do not block retrieval.
    """
    tokens = [t for t in normalize_title(title).split() if t][:max_tokens]
    if not tokens:
        return ""
    return "%" + "%".join(tokens) + "%"


def paper_match_richness(row: Dict[str, Any]) -> tuple:
    """Rank key for preferring the best record among near-duplicate titles."""
    title = str(row.get("title") or "")
    link = str(row.get("full_text_link") or "")
    return (
        1 if row.get("doi") else 0,
        1 if row.get("pmid") else 0,
        1 if row.get("year") else 0,
        1 if link.startswith("http") else 0,
        1 if row.get("journal") else 0,
        len(title),
        -int(row.get("id") or 0),
    )


def collapse_title_match_rows(
    rows: List[Dict[str, Any]],
    *,
    query_title: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Collapse near-duplicate match rows so truncated clones do not fill the top N."""
    if not rows:
        return []

    query_norm = normalize_title(query_title)
    # First pass: identical normalized titles → keep richest.
    by_norm: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = normalize_title(row.get("title") or "")
        if not key:
            continue
        prev = by_norm.get(key)
        if prev is None or paper_match_richness(row) > paper_match_richness(prev):
            by_norm[key] = row

    collapsed = list(by_norm.values())

    # Second pass: if a shorter title is contained in a longer one and both match
    # the query well, keep the richer/longer catalog record (drops truncated clones).
    collapsed.sort(key=lambda r: (-float(r.get("similarity") or 0), -len(str(r.get("title") or ""))))
    kept: List[Dict[str, Any]] = []
    for row in collapsed:
        norm = normalize_title(row.get("title") or "")
        dominated = False
        for other in list(kept):
            other_norm = normalize_title(other.get("title") or "")
            if not norm or not other_norm:
                continue
            shorter, longer = (norm, other_norm) if len(norm) <= len(other_norm) else (other_norm, norm)
            row_sim = float(row.get("similarity") or 0)
            other_sim = float(other.get("similarity") or 0)
            if (
                len(shorter) >= 24
                and shorter in longer
                and min(row_sim, other_sim) >= 0.82
            ):
                winner = row if paper_match_richness(row) > paper_match_richness(other) else other
                if winner is other:
                    dominated = True
                    break
                kept.remove(other)
                break
        if not dominated:
            kept.append(row)

    # Prefer exact normalized query matches first, then similarity.
    def sort_key(r: Dict[str, Any]):
        norm = normalize_title(r.get("title") or "")
        exact = 1 if query_norm and norm == query_norm else 0
        return (-exact, -float(r.get("similarity") or 0), -paper_match_richness(r)[5])

    kept.sort(key=sort_key)
    return kept[: max(1, limit)]


def _display_value(value: Any) -> str:
    """Format a field value for review UI display."""
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if item is not None) or "—"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _raw_is_empty(value: Any) -> bool:
    """Return True when a stored field value should be treated as empty."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def _normalize_compare_value(value: Any) -> str:
    """Normalize values for equality checks in review rows."""
    if _raw_is_empty(value):
        return ""
    if isinstance(value, (list, tuple)):
        return "|".join(str(item).strip().lower() for item in value if item is not None)
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int):
        return str(value)
    return str(value).strip().lower()


def _values_equal(left: Any, right: Any) -> bool:
    """Return True when two field values are equivalent for review purposes."""
    return _normalize_compare_value(left) == _normalize_compare_value(right)


def snapshot_merge_fields(paper: Dict[str, Any]) -> Dict[str, str]:
    """Extract review-eligible fields from a paper row as display strings."""
    out: Dict[str, str] = {}
    for field in PDF_UPLOAD_REVIEW_FIELDS:
        out[field] = _display_value(paper.get(field))
    return out


def build_merge_field_rows(
    existing: Dict[str, Any],
    proposed: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build side-by-side merge rows for fields that differ (legacy helper)."""
    return [
        row for row in build_review_field_rows(existing, proposed, is_new_paper=False)
        if row["row_status"] == "conflict"
    ]


def build_review_field_rows(
    existing: Dict[str, Any],
    proposed: Dict[str, Any],
    *,
    is_new_paper: bool = False,
) -> List[Dict[str, Any]]:
    """Build full review rows for every classification field."""
    rows: List[Dict[str, Any]] = []
    for field in PDF_UPLOAD_REVIEW_FIELDS:
        existing_val = existing.get(field)
        proposed_val = proposed.get(field)

        if is_new_paper:
            row_status = "new" if not _raw_is_empty(proposed_val) else "empty"
        elif _raw_is_empty(existing_val) and not _raw_is_empty(proposed_val):
            row_status = "new"
        elif not _raw_is_empty(existing_val) and not _raw_is_empty(proposed_val) and not _values_equal(existing_val, proposed_val):
            row_status = "conflict"
        elif _raw_is_empty(existing_val) and _raw_is_empty(proposed_val):
            row_status = "empty"
        else:
            row_status = "unchanged"

        if is_new_paper or row_status in {"new", "conflict", "empty"}:
            default_pick = "uploaded"
        else:
            default_pick = "existing"

        rows.append(
            {
                "field": field,
                "label": PDF_UPLOAD_REVIEW_LABELS.get(field, field),
                "existing": _display_value(existing_val),
                "uploaded": _display_value(proposed_val),
                "row_status": row_status,
                "default_pick": default_pick,
            }
        )
    return rows


def parse_custom_field_value(field: str, raw: Any) -> Any:
    """Parse a manual review value into the correct paper field type."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        values = [str(item).strip() for item in raw if str(item).strip()]
        if field in PDF_UPLOAD_ENUM_OPTIONS:
            allowed = set(PDF_UPLOAD_ENUM_OPTIONS[field])
            values = [v for v in values if v in allowed]
        return values or None

    text = str(raw).strip()
    if not text or text == "—":
        return None

    if field in PDF_UPLOAD_ENUM_OPTIONS:
        allowed = set(PDF_UPLOAD_ENUM_OPTIONS[field])
        if field in PDF_UPLOAD_MULTI_ENUM_FIELDS:
            if text.startswith("[") and text.endswith("]"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip() in allowed] or None
                except json.JSONDecodeError:
                    pass
            parts = [part.strip() for part in text.split(",") if part.strip()]
            return [part for part in parts if part in allowed] or None
        return text if text in allowed else None

    if field in PDF_UPLOAD_LIST_FIELDS:
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]

    if field in PDF_UPLOAD_NUMERIC_FIELDS:
        try:
            number = float(text)
        except ValueError:
            return None
        if number < 0:
            return None
        if field in PDF_UPLOAD_INTEGER_FIELDS:
            return int(number)
        return number

    return text


def apply_merge_selections(
    existing: Dict[str, Any],
    proposed: Dict[str, Any],
    selections: Dict[str, str],
    custom_values: Optional[Dict[str, Any]] = None,
    *,
    is_new_paper: bool = False,
) -> Dict[str, Any]:
    """Apply user review picks (existing / uploaded / custom) onto a paper row."""
    return apply_review_selections(
        existing,
        proposed,
        selections,
        custom_values,
        is_new_paper=is_new_paper,
    )


def apply_review_selections(
    existing: Dict[str, Any],
    proposed: Dict[str, Any],
    selections: Dict[str, str],
    custom_values: Optional[Dict[str, Any]] = None,
    *,
    is_new_paper: bool = False,
) -> Dict[str, Any]:
    """Merge uploaded classification using per-field picks and optional custom values."""
    custom_values = custom_values or {}
    merged = dict(proposed if is_new_paper else existing)

    for field in PDF_UPLOAD_REVIEW_FIELDS:
        pick = (selections.get(field) or ("uploaded" if is_new_paper else "existing")).strip().lower()
        if pick == "custom":
            merged[field] = parse_custom_field_value(field, custom_values.get(field))
        elif pick == "uploaded":
            if field in proposed:
                merged[field] = proposed[field]
        elif pick == "existing" and not is_new_paper:
            if field in existing:
                merged[field] = existing[field]

    merged["title"] = merged.get("title") or proposed.get("title") or existing.get("title")
    merged["abstract"] = merged.get("abstract") if merged.get("abstract") is not None else proposed.get("abstract")
    if proposed.get("full_text_link"):
        merged["full_text_link"] = proposed["full_text_link"]
    if proposed.get("date_harvested"):
        merged["date_harvested"] = proposed["date_harvested"]
    if proposed.get("_harvest_batch_id"):
        merged["_harvest_batch_id"] = proposed["_harvest_batch_id"]
    if proposed.get("classifier_version"):
        merged["classifier_version"] = proposed.get("classifier_version")
    if proposed.get("classification_confidence") is not None:
        merged["classification_confidence"] = proposed.get("classification_confidence")
    return merged
