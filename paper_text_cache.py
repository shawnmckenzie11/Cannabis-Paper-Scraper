# paper_text_cache.py
"""Local on-disk cache for paper PDFs and resolved full text (not committed to git)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("scratch/paper_cache")
MANIFEST_FILENAME = "manifest.json"


def resolve_cache_dir(explicit: Optional[Path] = None) -> Path:
    """Returns the root directory for local paper text/PDF cache."""
    if explicit is not None:
        return explicit
    import os

    env_dir = os.getenv("PAPER_TEXT_CACHE_DIR")
    if env_dir:
        return Path(env_dir)
    return DEFAULT_CACHE_DIR


def _pdf_path(cache_dir: Path, paper_id: int) -> Path:
    """Returns the cached PDF file path for a paper id."""
    return cache_dir / "pdfs" / f"{paper_id}.pdf"


def _text_path(cache_dir: Path, paper_id: int) -> Path:
    """Returns the cached full-text file path for a paper id."""
    return cache_dir / "text" / f"{paper_id}.txt"


def _meta_path(cache_dir: Path, paper_id: int) -> Path:
    """Returns the cached metadata JSON path for a paper id."""
    return cache_dir / "meta" / f"{paper_id}.json"


def _manifest_path(cache_dir: Path) -> Path:
    """Returns the manifest JSON path for the cache directory."""
    return cache_dir / MANIFEST_FILENAME


def load_manifest(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Loads the cache manifest, or an empty shell when missing."""
    cache_dir = resolve_cache_dir(cache_dir)
    path = _manifest_path(cache_dir)
    if not path.exists():
        return {"papers": {}, "updated_at": None}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_manifest(manifest: Dict[str, Any], cache_dir: Optional[Path] = None) -> Path:
    """Persists the cache manifest."""
    cache_dir = resolve_cache_dir(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now().isoformat()
    path = _manifest_path(cache_dir)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, default=str)
    return path


def read_cached_entry(
    paper_id: int,
    cache_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Returns cached metadata and text when present on disk."""
    cache_dir = resolve_cache_dir(cache_dir)
    meta_path = _meta_path(cache_dir, paper_id)
    text_path = _text_path(cache_dir, paper_id)
    if not meta_path.exists() or not text_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    text = text_path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    meta["text"] = text
    meta["has_pdf"] = _pdf_path(cache_dir, paper_id).exists()
    return meta


def write_cached_entry(
    paper_id: int,
    *,
    text: str,
    source: str,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Writes full text, optional PDF bytes, and metadata for a paper id."""
    cache_dir = resolve_cache_dir(cache_dir)
    for sub in ("pdfs", "text", "meta"):
        (cache_dir / sub).mkdir(parents=True, exist_ok=True)

    text_path = _text_path(cache_dir, paper_id)
    text_path.write_text(text, encoding="utf-8")

    pdf_saved = False
    if pdf_bytes:
        pdf_path = _pdf_path(cache_dir, paper_id)
        pdf_path.write_bytes(pdf_bytes)
        pdf_saved = True

    entry = {
        "paper_id": paper_id,
        "source": source,
        "full_text_link": full_text_link or "",
        "pmid": pmid or "",
        "doi": doi or "",
        "char_count": len(text),
        "has_pdf": pdf_saved,
        "cached_at": datetime.now().isoformat(),
    }
    with open(_meta_path(cache_dir, paper_id), "w", encoding="utf-8") as handle:
        json.dump(entry, handle, indent=2, default=str)

    manifest = load_manifest(cache_dir)
    manifest.setdefault("papers", {})[str(paper_id)] = {
        **entry,
        "text_path": str(text_path.relative_to(cache_dir)),
        "pdf_path": str(_pdf_path(cache_dir, paper_id).relative_to(cache_dir)) if pdf_saved else None,
    }
    save_manifest(manifest, cache_dir)
    entry["text"] = text
    return entry


def download_pdf_bytes(url: str, *, timeout: int = 30) -> Optional[bytes]:
    """Downloads raw PDF bytes from a URL when the response is a PDF."""
    import requests

    link = (url or "").strip()
    if not link.startswith("http"):
        return None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        response = requests.get(link, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        content_type = response.headers.get("Content-Type", "").lower()
        is_pdf = content_type.startswith("application/pdf") or response.content.startswith(b"%PDF")
        return response.content if is_pdf else None
    except Exception as exc:
        logger.debug("PDF download failed for %s: %s", link, exc)
        return None


def _extract_pdf_text_from_bytes(pdf_bytes: bytes) -> Optional[str]:
    """Extracts text from in-memory PDF bytes."""
    import io

    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        parts = [page.extract_text() for page in reader.pages if page.extract_text()]
        text = "\n".join(parts).strip()
        return text or None
    except Exception as exc:
        logger.debug("PDF text extraction failed: %s", exc)
        return None


def fetch_and_cache_paper(
    paper_id: int,
    *,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    force_refresh: bool = False,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fetches full text (and PDF when available) and writes them to the local cache.

    Returns a result dict with status ``cached``, ``skipped``, or ``failed``.
    """
    import calibration_pdf

    cache_dir = resolve_cache_dir(cache_dir)
    if not force_refresh:
        existing = read_cached_entry(paper_id, cache_dir)
        if existing:
            return {
                "paper_id": paper_id,
                "status": "skipped",
                "source": existing.get("source"),
                "char_count": existing.get("char_count"),
                "has_pdf": existing.get("has_pdf"),
            }

    memory_cache: Dict[str, Optional[str]] = {}
    pdf_bytes: Optional[bytes] = None
    link = (full_text_link or "").strip()
    if link and not re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/\d+/?$", link, re.IGNORECASE):
        pdf_bytes = download_pdf_bytes(link)

    text: Optional[str] = None
    source = calibration_pdf.CLASSIFICATION_SOURCE_ABSTRACT
    if pdf_bytes:
        text = _extract_pdf_text_from_bytes(pdf_bytes)
        if text:
            source = calibration_pdf.CLASSIFICATION_SOURCE_PDF

    if not text:
        text, source = calibration_pdf.resolve_classification_full_text(
            full_text_link=full_text_link,
            pmid=pmid,
            doi=doi,
            cache=memory_cache,
        )
        if source == calibration_pdf.CLASSIFICATION_SOURCE_PDF and not pdf_bytes and link:
            pdf_bytes = download_pdf_bytes(link)
    if not text:
        return {
            "paper_id": paper_id,
            "status": "failed",
            "reason": "no_text_resolved",
            "full_text_link": link,
            "pmid": pmid,
            "doi": doi,
        }

    entry = write_cached_entry(
        paper_id,
        text=text,
        source=source,
        full_text_link=full_text_link,
        pmid=pmid,
        doi=doi,
        pdf_bytes=pdf_bytes if source == calibration_pdf.CLASSIFICATION_SOURCE_PDF else None,
        cache_dir=cache_dir,
    )
    return {
        "paper_id": paper_id,
        "status": "cached",
        "source": entry.get("source"),
        "char_count": entry.get("char_count"),
        "has_pdf": entry.get("has_pdf"),
    }


def iter_calibration_batch_papers(
    calibration_dir: Path,
) -> Iterable[Dict[str, Any]]:
    """Yields unique paper rows from calibration batch JSON artifacts."""
    seen: set[int] = set()
    for path in sorted(calibration_dir.glob("*calibration*.json")):
        if path.name.endswith("_feedback_report.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for result in payload.get("results") or []:
            paper_id = result.get("paper_id")
            if paper_id is None:
                continue
            pid = int(paper_id)
            if pid in seen:
                continue
            seen.add(pid)
            yield {
                "paper_id": pid,
                "full_text_link": result.get("full_text_link") or "",
                "pmid": result.get("pmid") or "",
                "doi": result.get("doi") or "",
                "title": result.get("title") or "",
            }


def iter_db_papers_with_links(
    *,
    classifier_prefix: Optional[str] = "llm-pdf-reclassify-",
    limit: Optional[int] = None,
    offset: int = 0,
) -> Iterable[Dict[str, Any]]:
    """Yields papers from the active database that have a full_text_link."""
    from db_manager import DatabaseManager

    db = DatabaseManager()
    conn = db.get_connection()
    if not db.is_postgres:
        import sqlite3
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        clauses = ["full_text_link IS NOT NULL", "full_text_link != ''"]
        params: List[Any] = []
        if classifier_prefix:
            clauses.append("classifier_version LIKE ?")
            params.append(f"{classifier_prefix}%")
        sql = (
            "SELECT id, title, pmid, doi, full_text_link FROM papers "
            f"WHERE {' AND '.join(clauses)} ORDER BY id"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor.execute(sql, params)
        for row in cursor.fetchall():
            data = dict(row)
            yield {
                "paper_id": int(data["id"]),
                "title": data.get("title") or "",
                "pmid": data.get("pmid") or "",
                "doi": data.get("doi") or "",
                "full_text_link": data.get("full_text_link") or "",
            }
    finally:
        conn.close()


def cache_papers(
    papers: Sequence[Dict[str, Any]],
    *,
    force_refresh: bool = False,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Caches full text for a sequence of paper dicts."""
    results: List[Dict[str, Any]] = []
    for paper in papers:
        outcome = fetch_and_cache_paper(
            int(paper["paper_id"]),
            full_text_link=paper.get("full_text_link"),
            pmid=paper.get("pmid"),
            doi=paper.get("doi"),
            force_refresh=force_refresh,
            cache_dir=cache_dir,
        )
        results.append(outcome)
    summary = {
        "requested": len(results),
        "cached": sum(1 for row in results if row.get("status") == "cached"),
        "skipped": sum(1 for row in results if row.get("status") == "skipped"),
        "failed": sum(1 for row in results if row.get("status") == "failed"),
        "results": results,
    }
    return summary


def lookup_cached_text_for_paper(
    paper_id: int,
    cache_dir: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (text, source) from disk cache when available."""
    entry = read_cached_entry(paper_id, cache_dir)
    if not entry:
        return None, None
    return entry.get("text"), entry.get("source")


def store_paper_text_if_missing(
    paper_id: int,
    *,
    text: str,
    source: str,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    cache_dir: Optional[Path] = None,
) -> None:
    """Writes resolved text to disk when no cache entry exists yet."""
    cache_dir = resolve_cache_dir(cache_dir)
    if read_cached_entry(paper_id, cache_dir):
        return

    import calibration_pdf

    pdf_bytes: Optional[bytes] = None
    link = (full_text_link or "").strip()
    if source == calibration_pdf.CLASSIFICATION_SOURCE_PDF and link:
        if not re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/\d+/?$", link, re.IGNORECASE):
            pdf_bytes = download_pdf_bytes(link)

    write_cached_entry(
        paper_id,
        text=text,
        source=source,
        full_text_link=full_text_link,
        pmid=pmid,
        doi=doi,
        pdf_bytes=pdf_bytes,
        cache_dir=cache_dir,
    )


def resolve_paper_text(
    *,
    paper_id: Optional[int] = None,
    full_text: Optional[str] = None,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    memory_cache: Optional[MutableMapping[str, Optional[str]]] = None,
    use_disk_cache: bool = True,
    cache_dir: Optional[Path] = None,
) -> Tuple[Optional[str], str]:
    """Resolves paper text for classification, reading/writing the local disk cache.

    When ``paper_id`` is set and ``use_disk_cache`` is true, cached text is returned
    on hit. On miss, text is fetched via ``calibration_pdf`` and stored locally.
    """
    import calibration_pdf

    if full_text:
        return full_text, calibration_pdf.CLASSIFICATION_SOURCE_FULLTEXT

    if paper_id is not None and use_disk_cache:
        cached_text, cached_source = lookup_cached_text_for_paper(paper_id, cache_dir)
        if cached_text:
            return cached_text, cached_source or calibration_pdf.CLASSIFICATION_SOURCE_PDF

    text, source = calibration_pdf.resolve_classification_full_text(
        full_text_link=full_text_link,
        pmid=pmid,
        doi=doi,
        cache=memory_cache,
    )

    if paper_id is not None and text and use_disk_cache:
        try:
            store_paper_text_if_missing(
                paper_id,
                text=text,
                source=source,
                full_text_link=full_text_link,
                pmid=pmid,
                doi=doi,
                cache_dir=cache_dir,
            )
        except Exception as exc:
            logger.debug("Failed to write paper text cache for %s: %s", paper_id, exc)

    return text, source
