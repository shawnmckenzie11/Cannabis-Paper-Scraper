"""Fetch bibliographic PubMed metadata (dev only) for PMIDs in a split manifest.

Reads ONLY the `pmid` column of the manifest CSV. Writes a JSON list of
objects with pmid/title/authors/journal/year/doi/publication_types, each field
either a value or null with an entry in `missing` giving the reason.
"""
from __future__ import annotations

import argparse, csv, json, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
TOOL = "crn-pipeline"
EXCLUDED = {"38757607", "36803833", "34265088"}
FIELDS = ("title", "authors", "journal", "year", "doi", "publication_types")


def read_pmids(path):
    with open(path, newline="") as f:
        return [r["pmid"].strip() for r in csv.DictReader(f) if r.get("pmid", "").strip()]


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_article(art):
    mc = art.find("MedlineCitation")
    pmid = _text(mc.find("PMID"))
    a = mc.find("Article")
    rec = {"pmid": pmid}
    rec["title"] = _text(a.find("ArticleTitle")) or None
    authors = []
    for au in a.findall("AuthorList/Author"):
        name = " ".join(x for x in (_text(au.find("LastName")), _text(au.find("Initials"))) if x) or _text(au.find("CollectiveName"))
        if name:
            authors.append(name)
    rec["authors"] = authors or None
    rec["journal"] = _text(a.find("Journal/Title")) or None
    y = _text(a.find("Journal/JournalIssue/PubDate/Year")) or _text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
    rec["year"] = int(y) if y.isdigit() else None
    doi = None
    for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi" and _text(aid):
            doi = _text(aid)
    if not doi:
        for eid in a.findall("ELocationID"):
            if eid.get("EIdType") == "doi" and _text(eid):
                doi = _text(eid)
    rec["doi"] = doi
    rec["publication_types"] = [_text(p) for p in a.findall("PublicationTypeList/PublicationType") if _text(p)] or None
    rec["missing"] = {k: "absent in PubMed record" for k in FIELDS if rec[k] is None}
    return rec


SECRET_PARAMS = ("id", "email", "api_key")


def fetch(pmids, opener=urllib.request.urlopen, email=None, api_key=None):
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "tool": TOOL}
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    url = EFETCH + "?" + urllib.parse.urlencode(params)
    stored = {k: v for k, v in params.items() if k not in SECRET_PARAMS}
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with opener(url) as r:
        root = ET.fromstring(r.read())
    out = {}
    for art in root.findall("PubmedArticle"):
        rec = parse_article(art)
        rec["provenance"] = {"source": "NCBI E-utilities", "endpoint": EFETCH, "params": dict(stored), "retrieved_at": retrieved_at}
        out[rec["pmid"]] = rec
    return out


def build(pmids, opener=urllib.request.urlopen, batch=200):
    pmids = [p for p in dict.fromkeys(pmids) if p not in EXCLUDED]
    got = {}
    for i in range(0, len(pmids), batch):
        if i:
            time.sleep(0.4)  # <=3 req/s
        got.update(fetch(pmids[i:i + batch], opener))
    records = [got[p] for p in pmids if p in got]
    failed = [p for p in pmids if p not in got]
    return records, failed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    records, failed = build(read_pmids(a.manifest))
    with open(a.out, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(json.dumps({"written": len(records), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
