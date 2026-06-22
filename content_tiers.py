# content_tiers.py
"""Content-tier classification for calibration paper pools and scoped RL metrics."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

CONTENT_TIER_PDF_EXTRACTED = "pdf_extracted"
CONTENT_TIER_ABSTRACT_RECLASSIFY = "abstract_reclassify"
CONTENT_TIER_PDF_LINK = "pdf_link"
CONTENT_TIER_ABSTRACT_ONLY = "abstract_only"

CONTENT_TIERS: Tuple[str, ...] = (
    CONTENT_TIER_PDF_EXTRACTED,
    CONTENT_TIER_ABSTRACT_RECLASSIFY,
    CONTENT_TIER_PDF_LINK,
    CONTENT_TIER_ABSTRACT_ONLY,
)

CONTENT_TIER_LABELS: Dict[str, str] = {
    CONTENT_TIER_PDF_EXTRACTED: "PDF extracted (llm-pdf-reclassify)",
    CONTENT_TIER_ABSTRACT_RECLASSIFY: "Abstract reclassify (llm-reclassify)",
    CONTENT_TIER_PDF_LINK: "PDF link (not yet PDF-reclassified)",
    CONTENT_TIER_ABSTRACT_ONLY: "Abstract only",
}

# Methods-heavy fields that should not gate alignment on abstract-only papers.
METHODS_HEAVY_FIELDS = frozenset({
    "dose_mg", "duration_days", "inhaled_exposure_duration", "administration_frequency",
    "treatment_duration", "puff_count", "sample_size", "repeat_exposure_count",
    "exposure_regimen_bin", "thc_mg_ml", "cbd_mg_ml", "thc_mg_kg", "cbd_mg_kg",
    "thc_mg_g", "cbd_mg_g", "thc_uM", "cbd_uM", "strain_reported", "strain_normalized",
    "thc_pct", "cbd_pct", "multiple_doses", "multiple_time_intervals",
})

# Optional fields tracked via recall sidecar, not RL alignment denominator.
ALIGNMENT_EXCLUDED_FIELDS = frozenset({
    "strain_reported",
    "strain_normalized",
})

OPTIONAL_RECALL_FIELDS = frozenset(ALIGNMENT_EXCLUDED_FIELDS)


def _has_text(value: Any) -> bool:
    """Returns True when a string field is non-empty."""
    return bool(value and str(value).strip())


def infer_content_tier(row: Dict[str, Any]) -> str:
    """Infers the content tier for a paper row using classifier_version and full_text_link."""
    version = str(row.get("classifier_version") or "")
    if version.startswith("llm-pdf-reclassify-"):
        return CONTENT_TIER_PDF_EXTRACTED
    if version.startswith("llm-reclassify-"):
        return CONTENT_TIER_ABSTRACT_RECLASSIFY
    if _has_text(row.get("full_text_link")):
        return CONTENT_TIER_PDF_LINK
    return CONTENT_TIER_ABSTRACT_ONLY


def content_tier_sql_clause(tier: str, *, table_alias: str = "papers") -> Tuple[str, List[Any]]:
    """Returns a SQL predicate and params for the requested content tier."""
    prefix = f"{table_alias}."
    if tier in ("", "any", "all"):
        return "", []
    if tier == CONTENT_TIER_PDF_EXTRACTED:
        return f"{prefix}classifier_version LIKE ?", ["llm-pdf-reclassify-%"]
    if tier == CONTENT_TIER_ABSTRACT_RECLASSIFY:
        return (
            f"({prefix}classifier_version LIKE ? AND {prefix}classifier_version NOT LIKE ?)",
            ["llm-reclassify-%", "llm-pdf-%"],
        )
    if tier == CONTENT_TIER_PDF_LINK:
        return (
            f"({prefix}full_text_link IS NOT NULL AND {prefix}full_text_link != ''"
            f" AND ({prefix}classifier_version IS NULL OR {prefix}classifier_version NOT LIKE 'llm-pdf-reclassify-%'))",
            [],
        )
    if tier == CONTENT_TIER_ABSTRACT_ONLY:
        return (
            f"(({prefix}full_text_link IS NULL OR {prefix}full_text_link = '')"
            f" AND ({prefix}classifier_version IS NULL"
            f" OR ({prefix}classifier_version NOT LIKE 'llm-pdf-reclassify-%'"
            f" AND {prefix}classifier_version NOT LIKE 'llm-reclassify-%')))",
            [],
        )
    raise ValueError(f"Unknown content tier: {tier}")


def fields_in_scope_for_tier(
    subnode: str,
    tier: str,
    llm_block: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Returns in-scope fields adjusted for content tier (abstract tiers drop Methods-heavy fields)."""
    import subnode_field_scopes

    scope_fields = subnode_field_scopes.fields_in_scope(subnode, llm_block)
    if tier in (CONTENT_TIER_PDF_EXTRACTED, CONTENT_TIER_PDF_LINK):
        return scope_fields
    return [field for field in scope_fields if field not in METHODS_HEAVY_FIELDS]


def alignment_fields_in_scope_for_tier(
    subnode: str,
    tier: str,
    llm_block: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Returns tier-scoped fields that gate RL alignment (excludes optional recall-only fields)."""
    return [
        field
        for field in fields_in_scope_for_tier(subnode, tier, llm_block)
        if field not in ALIGNMENT_EXCLUDED_FIELDS
    ]


def summarize_content_tiers(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Counts papers by inferred content tier."""
    counts = {tier: 0 for tier in CONTENT_TIERS}
    for row in rows:
        tier = infer_content_tier(row)
        counts[tier] = counts.get(tier, 0) + 1
    return counts
