"""Full-text resolution and Maude classification helpers (PDF → article text → abstract)."""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, MutableMapping, Optional, Tuple
from xml.etree import ElementTree

import maude_classifier
import maude_confidence
import requests

logger = logging.getLogger(__name__)

CLASSIFICATION_SOURCE_PDF = "pdf"
CLASSIFICATION_SOURCE_FULLTEXT = "fulltext"
CLASSIFICATION_SOURCE_ABSTRACT = "abstract"

_PUBMED_LANDING_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/\d+/?$", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_STRIP_RE = re.compile(r"<[^>]+>")


def load_pdf_full_text(
    full_text_link: Optional[str],
    *,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Optional[str]:
    """Downloads and extracts PDF text from a paper link, with optional per-run cache."""
    link = (full_text_link or "").strip()
    if not link or _PUBMED_LANDING_RE.search(link):
        return None
    cache_key = f"pdf:{link}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    from reclassify_with_llm import download_and_extract_pdf_text

    try:
        text = download_and_extract_pdf_text(link)
    except Exception:
        text = None

    normalized = text if text else None
    if cache is not None:
        cache[cache_key] = normalized
    return normalized


def _xml_text_content(element: ElementTree.Element) -> str:
    """Recursively collects visible text from an XML element tree."""
    parts = [element.text or ""]
    for child in element:
        parts.append(_xml_text_content(child))
        parts.append(child.tail or "")
    return " ".join(part.strip() for part in parts if part and part.strip())


def fetch_pmc_full_text(
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    *,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Optional[str]:
    """Fetches open-access full text from Europe PMC when a PMCID is available."""
    pmid = (pmid or "").strip()
    doi = (doi or "").strip()
    if not pmid and not doi:
        return None

    cache_key = f"pmc:{pmid or doi}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    query = f"EXT_ID:{pmid}" if pmid else f"DOI:{doi}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CannabisPaperScraper/1.0; +https://cannabis-paper-scraper.fly.dev/)",
    }
    text: Optional[str] = None
    try:
        search_resp = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": query, "format": "json", "resultType": "core", "pageSize": 1},
            headers=headers,
            timeout=20,
        )
        if search_resp.status_code != 200:
            if cache is not None:
                cache[cache_key] = None
            return None

        payload = search_resp.json()
        results = payload.get("resultList", {}).get("result") or []
        if not results:
            if cache is not None:
                cache[cache_key] = None
            return None

        pmcid = (results[0].get("pmcid") or "").strip()
        if not pmcid:
            if cache is not None:
                cache[cache_key] = None
            return None

        xml_resp = requests.get(
            f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
            headers=headers,
            timeout=30,
        )
        if xml_resp.status_code != 200 or not xml_resp.text.strip():
            if cache is not None:
                cache[cache_key] = None
            return None

        root = ElementTree.fromstring(xml_resp.text)
        text = _xml_text_content(root).strip() or None
    except Exception as exc:
        logger.debug("Europe PMC full-text fetch failed for %s: %s", query, exc)
        text = None

    if cache is not None:
        cache[cache_key] = text
    return text


def fetch_html_article_text(
    url: Optional[str],
    *,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Optional[str]:
    """Fetches and strips HTML article text from a publisher landing page."""
    link = (url or "").strip()
    if not link or not link.startswith("http") or _PUBMED_LANDING_RE.search(link):
        return None

    cache_key = f"html:{link}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    text: Optional[str] = None
    try:
        response = requests.get(link, headers=headers, timeout=20)
        if response.status_code != 200:
            if cache is not None:
                cache[cache_key] = None
            return None

        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type and "text/plain" not in content_type:
            if cache is not None:
                cache[cache_key] = None
            return None

        html = response.text
        html = _HTML_TAG_RE.sub(" ", html)
        html = _HTML_STRIP_RE.sub(" ", html)
        text = unescape(re.sub(r"\s+", " ", html)).strip()
        if not text or len(text) < 500:
            text = None
    except Exception as exc:
        logger.debug("HTML article fetch failed for %s: %s", link, exc)
        text = None

    if cache is not None:
        cache[cache_key] = text
    return text


def resolve_classification_full_text(
    *,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
) -> Tuple[Optional[str], str]:
    """Resolves classification text with priority PDF → article full text → abstract-only.

    Returns:
        Tuple of (full_text_or_none, source) where source is ``pdf``, ``fulltext``, or ``abstract``.
    """
    pdf_text = load_pdf_full_text(full_text_link, cache=cache)
    if pdf_text:
        return pdf_text, CLASSIFICATION_SOURCE_PDF

    pmc_text = fetch_pmc_full_text(pmid, doi, cache=cache)
    if pmc_text:
        return pmc_text, CLASSIFICATION_SOURCE_FULLTEXT

    html_text = fetch_html_article_text(full_text_link, cache=cache)
    if html_text:
        return html_text, CLASSIFICATION_SOURCE_FULLTEXT

    return None, CLASSIFICATION_SOURCE_ABSTRACT


def maude_classifier_version(source: str, rules_version: str) -> str:
    """Builds a classifier_version label that records which text tier Maude used."""
    if source == CLASSIFICATION_SOURCE_PDF:
        return f"maude-pdf-{rules_version}"
    if source == CLASSIFICATION_SOURCE_FULLTEXT:
        return f"maude-fulltext-{rules_version}"
    return f"maude-{rules_version}"


def classify_maude_for_calibration(
    title: str,
    abstract: str,
    *,
    full_text_link: Optional[str] = None,
    full_text: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    paper_id: Optional[int] = None,
    rules_version: str,
    cache: Optional[MutableMapping[str, Optional[str]]] = None,
    use_disk_cache: bool = True,
) -> Tuple[Dict[str, Any], bool]:
    """Runs Maude using PDF/article full text when available, else abstract-only."""
    import paper_text_cache

    resolved_text, source = paper_text_cache.resolve_paper_text(
        paper_id=paper_id,
        full_text=full_text,
        full_text_link=full_text_link,
        pmid=pmid,
        doi=doi,
        memory_cache=cache,
        use_disk_cache=use_disk_cache,
    )

    pdf_used = source == CLASSIFICATION_SOURCE_PDF
    maude_out = maude_classifier.classify_paper(
        title,
        abstract,
        full_text=resolved_text,
        rules_version=rules_version,
        abstract_only_extraction=resolved_text is None,
    )
    maude_confidence.apply_maude_confidence(maude_out)
    return maude_out, pdf_used
