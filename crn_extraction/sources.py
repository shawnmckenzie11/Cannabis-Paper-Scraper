"""Main-text acquisition: PMC Open Access XML only (free, legal). Supplements are never fetched.

NCBI etiquette: < 3 requests/s (retries included), <= 200 ids per call, tool=crn_pipeline,
email from $CRN_NCBI_EMAIL or a placeholder, no API key. XML is parsed with defusedxml;
responses over 20 MB are rejected (fetch_failed). 429/5xx are retried with exponential backoff. Neither email nor key is ever written into provenance or outputs.
"""
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as _StdET  # only for the ParseError type; parsing uses defusedxml

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "crn_pipeline"
PLACEHOLDER_EMAIL = "crn-pipeline@example.invalid"
EMAIL_ENV = "CRN_NCBI_EMAIL"
MAX_IDS = 200
MIN_INTERVAL = 0.4  # seconds -> 2.5 req/s, also applied between retries
MAX_BYTES = 20 * 1024 * 1024  # responses larger than this are rejected (fetch_failed)
RETRIES = 4
BACKOFF = 1.0  # seconds; doubles per retry
RETRY_STATUS = {429, 500, 502, 503, 504}
_last = [0.0]
_sleep = time.sleep


class FetchError(Exception):
    pass


def ncbi_email():
    """Contact email for NCBI etiquette: env var, else placeholder. Sent only as a query
    parameter; never stored in provenance, records or outputs."""
    return os.environ.get(EMAIL_ENV, "").strip() or PLACEHOLDER_EMAIL


def _throttle():
    wait = MIN_INTERVAL - (time.monotonic() - _last[0])
    if wait > 0:
        _sleep(wait)
    _last[0] = time.monotonic()


def _read_capped(r):
    data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise FetchError(f"response over {MAX_BYTES} bytes")
    return data


def _get(endpoint, params, opener=urllib.request.urlopen):
    q = urllib.parse.urlencode(dict(params, tool=TOOL, email=ncbi_email()), doseq=True)
    url = EUTILS + endpoint + "?" + q
    for attempt in range(RETRIES + 1):
        _throttle()
        try:
            with opener(url, timeout=60) as r:
                return _read_capped(r)
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUS and attempt < RETRIES:
                _sleep(BACKOFF * 2 ** attempt)
                continue
            raise FetchError(f"HTTP {e.code}") from None
    raise FetchError("retries exhausted")


def chunks(ids, n=MAX_IDS):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _elink(batch, opener):
    out = {}
    root = _parse(_get("elink.fcgi", {"dbfrom": "pubmed", "db": "pmc", "linkname": "pubmed_pmc",
                                                 "id": list(batch)}, opener))  # repeated id= -> one LinkSet per PMID
    for ls in root.iter("LinkSet"):
        src = [e.text for e in ls.findall("./IdList/Id")]
        dst = [e.text for e in ls.findall(".//LinkSetDb/Link/Id")]
        if len(src) == 1 and dst:
            out[src[0]] = "PMC" + dst[0]
    return out


def _parse(raw):
    try:
        return ET.fromstring(raw)
    except (_StdET.ParseError, DefusedXmlException) as e:
        raise FetchError(f"bad XML ({type(e).__name__})") from None


def pmid_to_pmcid(pmids, opener=urllib.request.urlopen):
    """elink pubmed->pmc -> ({pmid: PMCID}, {failed pmids}). A failed batch is retried one PMID
    at a time so a single bad lookup marks only that paper fetch_failed. Missing mapping (no
    failure) => no OA full text (source_not_available)."""
    out, failed = {}, set()
    for batch in chunks(pmids):
        try:
            out.update(_elink(batch, opener))
            continue
        except Exception as e:  # noqa: BLE001 - logged with type, then narrowed per PMID
            log.warning("elink batch failed (%s); retrying per PMID", type(e).__name__)
        for p in batch:
            try:
                out.update(_elink([p], opener))
            except Exception as e:  # noqa: BLE001
                log.warning("elink failed for one PMID (%s)", type(e).__name__)
                failed.add(p)
    return out, failed


SEC_MAP = (("method", "methods"), ("material", "methods"), ("experimental", "methods"), ("procedure", "methods"),
           ("result", "results"), ("discussion", "discussion"), ("conclusion", "discussion"),
           ("introduction", "introduction"), ("background", "introduction"))


def _text(el):
    return " ".join(" ".join(el.itertext()).split())


def parse_pmc_xml(xml_bytes):
    """JATS -> sections [{type,text}]. Raises ValueError on parse failure (-> parse_failed).
    supplementary-material, ack, ref-list, contrib/aff (PII) are dropped."""
    if len(xml_bytes) > MAX_BYTES:
        raise ValueError("parse_failed: document over size cap")
    try:
        root = ET.fromstring(xml_bytes)
    except (_StdET.ParseError, DefusedXmlException) as e:
        raise ValueError(f"parse_failed: {type(e).__name__}") from None
    art = root.find(".//article") if root.tag != "article" else root
    if art is None:
        raise ValueError("parse_failed: no article element")
    secs = []
    ab = art.find(".//front//abstract")
    if ab is not None:
        secs.append({"type": "abstract", "text": _text(ab)})
    body = art.find("./body")
    if body is None:
        return secs  # abstract only -> caller marks source_not_available
    for drop in ("supplementary-material", "ack", "ref-list"):
        for parent in body.iter():
            for ch in list(parent):
                if ch.tag == drop:
                    parent.remove(ch)
    for parent in body.iter():
        for ch in list(parent):
            if ch.tag == "table-wrap":
                secs.append({"type": "table", "text": _text(ch)})
                parent.remove(ch)
            elif ch.tag == "fig":
                cap = ch.find("caption")
                if cap is not None:
                    secs.append({"type": "figure_caption", "text": _text(cap)})
                parent.remove(ch)
    for sec in body.findall("./sec"):
        title = (sec.findtext("title") or "").lower() + " " + (sec.get("sec-type") or "").lower()
        typ = next((t for k, t in SEC_MAP if k in title), "body")
        secs.append({"type": typ, "text": _text(sec)})
    loose = " ".join(_text(p) for p in body.findall("./p"))
    if loose:
        secs.append({"type": "body", "text": loose})
    return secs


def fetch_main_text(pmids, opener=urllib.request.urlopen):
    """-> {pmid: {"status", "source_tier", "sections"}}. Status values are the contract's
    check_failure_reason names for paper-level failures."""
    out = {}
    mapping, failed = pmid_to_pmcid(pmids, opener)
    for p in pmids:
        if p in failed:
            out[p] = {"status": "fetch_failed", "source_tier": "unknown", "sections": []}
        elif p not in mapping:
            out[p] = {"status": "source_not_available", "source_tier": "metadata_only", "sections": []}
    have = [p for p in pmids if p in mapping]
    for p in have:
        try:
            raw = _get("efetch.fcgi", {"db": "pmc", "id": mapping[p][3:], "retmode": "xml"}, opener)
        except Exception as e:  # noqa: BLE001
            log.warning("efetch failed for one PMID (%s)", type(e).__name__)
            out[p] = {"status": "fetch_failed", "source_tier": "unknown", "sections": []}
            continue
        try:
            secs = parse_pmc_xml(raw)
        except ValueError:
            out[p] = {"status": "parse_failed", "source_tier": "pmc_oa_xml", "sections": []}
            continue
        full = any(s["type"] != "abstract" for s in secs)
        out[p] = {"status": "ok" if full else "source_not_available",
                  "source_tier": "pmc_oa_xml" if full else "abstract_only", "sections": secs}
    return out
