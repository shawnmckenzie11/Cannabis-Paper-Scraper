"""Main-text acquisition: PMC Open Access XML only (free, legal). Supplements are never fetched.

NCBI etiquette: < 3 requests/s, <= 200 ids per efetch, tool=crn_pipeline, placeholder email,
no API key. Neither email nor key is ever written into provenance or outputs.
"""
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
TOOL = "crn_pipeline"
EMAIL = "crn-pipeline@example.invalid"  # placeholder by design
MAX_IDS = 200
MIN_INTERVAL = 0.4  # seconds -> 2.5 req/s
_last = [0.0]


def _get(endpoint, params, opener=urllib.request.urlopen):
    wait = MIN_INTERVAL - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    q = urllib.parse.urlencode(dict(params, tool=TOOL, email=EMAIL), doseq=True)
    with opener(EUTILS + endpoint + "?" + q, timeout=60) as r:
        return r.read()


def chunks(ids, n=MAX_IDS):
    ids = list(ids)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def pmid_to_pmcid(pmids, opener=urllib.request.urlopen):
    """elink pubmed->pmc. Missing mapping => no OA full text (source_not_available)."""
    out = {}
    for batch in chunks(pmids):
        root = ET.fromstring(_get("elink.fcgi", {"dbfrom": "pubmed", "db": "pmc", "linkname": "pubmed_pmc",
                                                 "id": list(batch)}, opener))  # repeated id= -> one LinkSet per PMID
        for ls in root.iter("LinkSet"):
            src = [e.text for e in ls.findall("./IdList/Id")]
            dst = [e.text for e in ls.findall(".//LinkSetDb/Link/Id")]
            if len(src) == 1 and dst:
                out[src[0]] = "PMC" + dst[0]
    return out


SEC_MAP = (("method", "methods"), ("material", "methods"), ("experimental", "methods"), ("procedure", "methods"),
           ("result", "results"), ("discussion", "discussion"), ("conclusion", "discussion"),
           ("introduction", "introduction"), ("background", "introduction"))


def _text(el):
    return " ".join(" ".join(el.itertext()).split())


def parse_pmc_xml(xml_bytes):
    """JATS -> sections [{type,text}]. Raises ValueError on parse failure (-> parse_failed).
    supplementary-material, ack, ref-list, contrib/aff (PII) are dropped."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"parse_failed: {e}") from e
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
    try:
        mapping = pmid_to_pmcid(pmids, opener)
    except Exception:
        return {p: {"status": "fetch_failed", "source_tier": "unknown", "sections": []} for p in pmids}
    for p in pmids:
        if p not in mapping:
            out[p] = {"status": "source_not_available", "source_tier": "metadata_only", "sections": []}
    have = [p for p in pmids if p in mapping]
    for p in have:
        try:
            raw = _get("efetch.fcgi", {"db": "pmc", "id": mapping[p][3:], "retmode": "xml"}, opener)
        except Exception:
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
