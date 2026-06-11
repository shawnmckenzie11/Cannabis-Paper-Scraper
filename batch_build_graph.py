# batch_build_graph.py
"""Fast graph builder using Semantic Scholar batch endpoint."""
import sys
import re
import time
import json
import logging
import requests
from typing import List, Dict, Any, Optional, Tuple
from db_manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_FIELDS = (
    "paperId,externalIds,title,year,"
    "references.paperId,references.title,references.year,references.externalIds,"
    "citations.paperId,citations.title,citations.year,citations.externalIds"
)
BATCH_SIZE = 50
RATE_DELAY = 2.5
TITLE_MATCH_THRESHOLD = 0.7


def tokenize_title(title: str) -> set:
    return set(re.sub(r"[^a-z0-9\s]", "", title.lower()).split())


def load_paper_index(db):
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT id, semantic_scholar_id, pmid, doi, title
        FROM papers ORDER BY id
    """).fetchall()
    conn.close()

    pmid_map = {}
    doi_map = {}
    s2_map = {}
    title_list = []
    id_list = []

    for r in rows:
        pid = r["id"]
        id_list.append((pid, r["semantic_scholar_id"], r["pmid"], r["doi"]))
        if r["pmid"]:
            pmid_map[str(r["pmid"])] = pid
        if r["doi"]:
            doi_map[r["doi"]] = pid
        if r["semantic_scholar_id"]:
            s2_map[r["semantic_scholar_id"]] = pid
        t = r["title"]
        if t and len(t) > 10:
            tokens = tokenize_title(t)
            if len(tokens) >= 3:
                title_list.append((pid, tokens))

    return pmid_map, doi_map, s2_map, title_list, id_list


def match_local(ref: dict, pmid_map, doi_map, s2_map, title_list):
    ext = ref.get("externalIds") or {}
    pmid = ext.get("PMID")
    if pmid and str(pmid) in pmid_map:
        return pmid_map[str(pmid)]
    doi = ext.get("DOI")
    if doi and doi in doi_map:
        return doi_map[doi]
    s2 = ref.get("paperId")
    if s2 and s2 in s2_map:
        return s2_map[s2]
    rt = ref.get("title", "")
    if rt and len(rt) > 10:
        rtoks = tokenize_title(rt)
        if len(rtoks) >= 3:
            best, best_id = 0.0, None
            for pid, ltoks in title_list:
                o = rtoks & ltoks
                s = 2.0 * len(o) / (len(rtoks) + len(ltoks))
                if s > best:
                    best, best_id = s, pid
            if best >= TITLE_MATCH_THRESHOLD:
                return best_id
    return None


def main():
    db = DatabaseManager()
    logger.info("Loading paper index...")
    pmid_map, doi_map, s2_map, title_list, id_list = load_paper_index(db)
    logger.info(f"Index: {len(id_list)} papers, {len(title_list)} with titles")

    conn = db.get_connection()
    conn.execute("DELETE FROM citation_edges")
    conn.commit()
    conn.close()
    logger.info("Cleared existing edges.")

    total_refs = total_cites = total_int = total_skip = total_err = 0

    for start in range(0, len(id_list), BATCH_SIZE):
        batch = id_list[start:start + BATCH_SIZE]
        idents = []
        idx_to_pid = {}
        for i, (pid, s2, pmid, doi) in enumerate(batch):
            ident = s2 or (f"PMID:{pmid}" if pmid else (f"DOI:{doi}" if doi else None))
            if ident:
                idents.append(ident)
                idx_to_pid[i] = pid
            else:
                total_skip += 1

        if not idents:
            continue

        try:
            resp = requests.post(
                S2_BATCH_URL,
                params={"fields": S2_FIELDS},
                json={"ids": idents},
                timeout=120
            )
            if resp.status_code == 429:
                logger.warning("Rate limited, sleeping 15s...")
                time.sleep(15)
                resp = requests.post(S2_BATCH_URL, params={"fields": S2_FIELDS},
                                     json={"ids": idents}, timeout=120)
            if resp.status_code != 200:
                logger.warning(f"Batch {start} status={resp.status_code}")
                total_err += len(idents)
                time.sleep(RATE_DELAY)
                continue
            results = resp.json()
        except Exception as e:
            logger.error(f"Batch {start}: {e}")
            total_err += len(idents)
            time.sleep(RATE_DELAY)
            continue

        # Collect unique edges in memory to avoid per-row SQL dedup
        seen = set()
        batch_rows = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        for idx, result in enumerate(results):
            if not result or not result.get("paperId"):
                continue
            src = idx_to_pid.get(idx)
            if not src:
                continue

            # references
            for ref in result.get("references") or []:
                cited = ref.get("citedPaper") or ref
                pid2 = cited.get("paperId")
                if not pid2:
                    continue
                local = match_local(cited, pmid_map, doi_map, s2_map, title_list)
                key = (src, local if local else pid2, "cites")
                if key in seen:
                    continue
                seen.add(key)
                batch_rows.append((
                    src, local,
                    None if local else pid2,
                    cited.get("title"),
                    cited.get("year"),
                    "cites",
                    "high" if local else "medium",
                    now_str
                ))

            # citations (papers that cite this paper)
            for cit in result.get("citations") or []:
                citing = cit.get("citingPaper") or cit
                pid2 = citing.get("paperId")
                if not pid2:
                    continue
                local = match_local(citing, pmid_map, doi_map, s2_map, title_list)
                # Edge stored as: source=this_paper, target=citing_paper, rel="cited_by"
                # Semantic: "this paper IS CITED BY citing_paper"
                # get_cited_by queries: WHERE source_paper_id=? AND rel='cited_by'
                # returning citing_paper (target) info
                key = (src, local if local else pid2, "cited_by")
                if key in seen:
                    continue
                seen.add(key)
                batch_rows.append((
                    src,                            # source_paper_id (this paper, always in DB)
                    local,                          # target_paper_id (citing paper if in DB)
                    pid2 if not local else None,    # target_external_id (citing paper S2 ID)
                    citing.get("title"),            # target_title
                    citing.get("year"),             # target_year
                    "cited_by",
                    "high" if local else "medium",
                    now_str
                ))

        # Bulk insert
        if batch_rows:
            conn = db.get_connection()
            inserted = 0
            for row in batch_rows:
                conn.execute("""
                    INSERT INTO citation_edges
                        (source_paper_id, target_paper_id, target_external_id,
                         target_title, target_year, relationship, confidence,
                         source, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row[0], row[1], row[2], row[3], row[4],
                    row[5], row[6], "semantic_scholar",
                    json.dumps({"s2_id": row[2] or row[0]}),
                    row[7]
                ))
                inserted += 1
            conn.commit()
            conn.close()

        # Count stats
        refs_in_batch = sum(1 for r in batch_rows if r[5] == "cites")
        cites_in_batch = sum(1 for r in batch_rows if r[5] == "cited_by")
        int_in_batch = sum(1 for r in batch_rows if r[1] is not None)
        total_refs += refs_in_batch
        total_cites += cites_in_batch
        total_int += int_in_batch

        progress = min(start + BATCH_SIZE, len(id_list))
        logger.info(
            f"[{progress}/{len(id_list)}] "
            f"total_edges={total_refs + total_cites} "
            f"(refs={total_refs} cites={total_cites} internal={total_int})"
        )

        time.sleep(RATE_DELAY)

    # Final stats
    conn = db.get_connection()
    te = conn.execute("SELECT COUNT(*) FROM citation_edges").fetchone()[0]
    ie = conn.execute("SELECT COUNT(*) FROM citation_edges WHERE target_paper_id IS NOT NULL").fetchone()[0]
    conn.close()

    logger.info("=" * 60)
    logger.info("GRAPH BUILD COMPLETE")
    logger.info(f"Total edges: {te}  Internal: {ie}  External: {te - ie}")
    logger.info(f"References: {total_refs}  Citations: {total_cites}  Errors: {total_err}")


if __name__ == "__main__":
    main()
