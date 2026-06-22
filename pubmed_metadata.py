# pubmed_metadata.py
"""PubMed publication-type prefix helpers for harvest and enrichment."""

from __future__ import annotations

from typing import Iterable, List

REVIEW_LIKE_MARKERS = (
    "review",
    "editorial",
    "comment",
    "letter",
    "perspective",
    "news",
)


def normalize_pub_types(pub_types: Iterable[str]) -> List[str]:
    """Normalizes PubMed PublicationType strings to lowercase stripped tokens."""
    return [str(pt).strip().lower() for pt in pub_types if pt and str(pt).strip()]


def build_publication_type_prefix(pub_types: Iterable[str]) -> str:
    """Builds the abstract prefix injected from PubMed PublicationTypeList values."""
    lowered = normalize_pub_types(pub_types)
    is_meta = any("meta-analysis" in pt for pt in lowered)
    is_review_like = any(any(marker in pt for marker in REVIEW_LIKE_MARKERS) for pt in lowered)
    prefix = ""
    if is_meta:
        prefix += "Publication Type: Meta-Analysis. "
    if is_review_like and "Meta-Analysis" not in prefix:
        prefix += "Publication Type: Review. "
    return prefix
