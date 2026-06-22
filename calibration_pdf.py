"""PDF text loading helpers for calibration Maude runs."""

from __future__ import annotations

from typing import Any, Dict, MutableMapping, Optional, Tuple

import maude_classifier


def load_pdf_full_text(
    full_text_link: Optional[str],
    *,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Optional[str]:
    """Downloads and extracts PDF text from a paper link, with optional per-run cache."""
    link = (full_text_link or "").strip()
    if not link:
        return None
    if cache is not None and link in cache:
        return cache[link]

    from reclassify_with_llm import download_and_extract_pdf_text

    try:
        text = download_and_extract_pdf_text(link)
    except Exception:
        text = None

    normalized = text if text else None
    if cache is not None:
        cache[link] = normalized
    return normalized


def classify_maude_for_calibration(
    title: str,
    abstract: str,
    *,
    full_text_link: Optional[str] = None,
    full_text: Optional[str] = None,
    rules_version: str,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Runs Maude classification using full PDF text when a link or text is available."""
    resolved_text = full_text
    if resolved_text is None and full_text_link:
        resolved_text = load_pdf_full_text(full_text_link, cache=cache)

    pdf_used = bool(resolved_text)
    maude_out = maude_classifier.classify_paper(
        title,
        abstract,
        full_text=resolved_text,
        rules_version=rules_version,
        abstract_only_extraction=not pdf_used,
    )
    return maude_out, pdf_used
