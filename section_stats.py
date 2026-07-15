"""Section coverage stats for filtered paper subsets."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import paper_text_cache
from maude_classifier import (
    _abstract_has_isolated_methods_section,
    _abstract_has_isolated_results_section,
    _title_is_non_research,
    paper_classified_from_pdf_body,
    paper_has_direct_pdf_link,
)


def _infer_sections_without_body_text(paper: Dict[str, Any]) -> tuple[bool, bool]:
    """Fallback when no cached body text exists (e.g. Fly without local cache).

    Direct PDF links and PDF-tier classifier versions indicate full-text access;
    research papers in those tiers almost always include Methods and Results.
    """
    title = paper.get("title") or ""
    if _title_is_non_research(title):
        return False, False

    link = paper.get("full_text_link")
    classifier_version = paper.get("classifier_version")
    has_pdf_access = paper_has_direct_pdf_link(link) or paper_classified_from_pdf_body(classifier_version)
    if not has_pdf_access:
        return False, False
    return True, True


def _has_substantial_cached_body(meta: Optional[Dict[str, Any]], title: str) -> bool:
    """True when disk cache indicates reusable full text without reading the body."""
    if not meta:
        return False
    if _title_is_non_research(title):
        return False
    return int(meta.get("char_count") or 0) >= paper_text_cache.MIN_REUSABLE_FULLTEXT_CHARS


def paper_row_has_methods_section(paper: Dict[str, Any], abstract: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Methods coverage for one paper row using lightweight cache metadata."""
    title = paper.get("title") or ""
    if _abstract_has_isolated_methods_section(abstract):
        return True
    if _title_is_non_research(title):
        return False
    if paper_has_direct_pdf_link(paper.get("full_text_link")) or paper_classified_from_pdf_body(
        paper.get("classifier_version")
    ):
        return True
    if meta is None:
        paper_id = paper.get("id")
        if paper_id:
            meta = paper_text_cache.read_cached_meta_light(int(paper_id))
    if _has_substantial_cached_body(meta, title):
        return True
    return False


def paper_row_has_results_section(paper: Dict[str, Any], abstract: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    """Results coverage for one paper row using lightweight cache metadata."""
    title = paper.get("title") or ""
    if _abstract_has_isolated_results_section(abstract):
        return True
    if _title_is_non_research(title):
        return False
    if paper_has_direct_pdf_link(paper.get("full_text_link")) or paper_classified_from_pdf_body(
        paper.get("classifier_version")
    ):
        return True
    if meta is None:
        paper_id = paper.get("id")
        if paper_id:
            meta = paper_text_cache.read_cached_meta_light(int(paper_id))
    if _has_substantial_cached_body(meta, title):
        return True
    return False


def _section_flags_for_paper(paper: Dict[str, Any], abstract: str) -> tuple[bool, bool, bool]:
    """Return (has_methods, has_results, used_cache_meta) with at most one meta read."""
    title = paper.get("title") or ""
    if _title_is_non_research(title):
        return False, False, False

    has_methods = _abstract_has_isolated_methods_section(abstract)
    has_results = _abstract_has_isolated_results_section(abstract)
    if has_methods and has_results:
        return True, True, False

    if paper_has_direct_pdf_link(paper.get("full_text_link")) or paper_classified_from_pdf_body(
        paper.get("classifier_version")
    ):
        return True, True, False

    meta = None
    paper_id = paper.get("id")
    if paper_id:
        meta = paper_text_cache.read_cached_meta_light(int(paper_id))
    if _has_substantial_cached_body(meta, title):
        return True, True, bool(meta)

    if not has_methods:
        has_methods = False
    if not has_results:
        has_results = False
    return has_methods, has_results, bool(meta)


def compute_section_stats(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute methods/results section coverage for a paper list."""
    total = len(papers)
    if total == 0:
        return {
            "total_count": 0,
            "methods_count": 0,
            "results_count": 0,
            "methods_pct": 0.0,
            "results_pct": 0.0,
        }

    methods_count = 0
    results_count = 0
    for paper in papers:
        abstract = paper.get("abstract") or ""
        has_methods, has_results, _ = _section_flags_for_paper(paper, abstract)
        if has_methods:
            methods_count += 1
        if has_results:
            results_count += 1

    return {
        "total_count": total,
        "methods_count": methods_count,
        "results_count": results_count,
        "methods_pct": round(100.0 * methods_count / total, 1),
        "results_pct": round(100.0 * results_count / total, 1),
    }
