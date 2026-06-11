# citation_graph.py
import re
import json
import time
import logging
import requests
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime
from db_manager import DatabaseManager

logger = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = (
    "paperId,externalIds,title,year,citationCount,referenceCount,"
    "isOpenAccess,openAccessPdf"
)
S2_REF_FIELDS = (
    "paperId,externalIds,title,year,citationCount,referenceCount"
)

TITLE_MATCH_THRESHOLD = 0.7


class CitationGraph:
    """Manages a directed citation graph between papers in the database.

    Edges are directional: source_paper_id *cites* target_paper_id.
    The reverse direction (cited-by) is derived by swapping the query.
    """

    def __init__(self, db: DatabaseManager):
        self.db = db

    # ── API helpers ──────────────────────────────────────────────

    def _fetch_paper(self, paper_id: str) -> Optional[Dict]:
        """Fetch a single paper's metadata from Semantic Scholar."""
        url = f"{S2_BASE}/paper/{paper_id}?fields={S2_FIELDS}"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 429:
                time.sleep(3)
                r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            logger.warning(f"S2 paper lookup {paper_id} returned {r.status_code}")
        except Exception as e:
            logger.error(f"S2 paper lookup error for {paper_id}: {e}")
        return None

    def _fetch_references(self, paper_id: str, limit: int = 1000) -> List[Dict]:
        """Fetch papers referenced by the given S2 paper ID."""
        url = f"{S2_BASE}/paper/{paper_id}/references?fields={S2_REF_FIELDS}&limit={limit}"
        results = []
        try:
            while url:
                r = requests.get(url, timeout=30)
                if r.status_code == 429:
                    time.sleep(3)
                    r = requests.get(url, timeout=30)
                if r.status_code != 200:
                    logger.warning(f"S2 references {paper_id} returned {r.status_code}")
                    break
                data = r.json()
                for entry in data.get("data", []):
                    ref = entry.get("citedPaper")
                    if ref and ref.get("paperId"):
                        results.append(ref)
                url = data.get("next", None)
                if url:
                    time.sleep(1.5)
        except Exception as e:
            logger.error(f"S2 references error for {paper_id}: {e}")
        return results

    def _fetch_citations(self, paper_id: str, limit: int = 1000) -> List[Dict]:
        """Fetch papers that cite the given S2 paper ID."""
        url = f"{S2_BASE}/paper/{paper_id}/citations?fields={S2_REF_FIELDS}&limit={limit}"
        results = []
        try:
            while url:
                r = requests.get(url, timeout=30)
                if r.status_code == 429:
                    time.sleep(3)
                    r = requests.get(url, timeout=30)
                if r.status_code != 200:
                    logger.warning(f"S2 citations {paper_id} returned {r.status_code}")
                    break
                data = r.json()
                for entry in data.get("data", []):
                    citing = entry.get("citingPaper")
                    if citing and citing.get("paperId"):
                        results.append(citing)
                url = data.get("next", None)
                if url:
                    time.sleep(1.5)
        except Exception as e:
            logger.error(f"S2 citations error for {paper_id}: {e}")
        return results

    # ── Matching ─────────────────────────────────────────────────

    def _get_s2_id_for_paper(self, paper_id: int) -> Optional[str]:
        """Get the Semantic Scholar paper ID for a local paper."""
        conn = self.db.get_connection()
        try:
            row = conn.execute(
                "SELECT semantic_scholar_id, doi, pmid FROM papers WHERE id = ?",
                (paper_id,)
            ).fetchone()
            if row and row["semantic_scholar_id"]:
                return row["semantic_scholar_id"]
            # Try to build from PMID/DOI
            if row and row["pmid"]:
                return f"PMID:{row['pmid']}"
            if row and row["doi"]:
                return f"DOI:{row['doi']}"
            return None
        finally:
            conn.close()

    def _match_to_local(self, ref: Dict) -> Optional[int]:
        """Try to match a Semantic Scholar reference to a paper in our DB.

        Priority: PMID > DOI > title fuzzy match.
        Returns the local paper id or None.
        """
        ext = ref.get("externalIds") or {}

        # 1. PMID
        pmid = ext.get("PMID")
        if pmid:
            conn = self.db.get_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM papers WHERE pmid = ?", (str(pmid),)
                ).fetchone()
                if row:
                    return row["id"]
            finally:
                conn.close()

        # 2. DOI
        doi = ext.get("DOI")
        if doi:
            conn = self.db.get_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM papers WHERE doi = ?", (doi,)
                ).fetchone()
                if row:
                    return row["id"]
            finally:
                conn.close()

        # 3. Semantic Scholar ID
        s2_id = ref.get("paperId")
        if s2_id:
            conn = self.db.get_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM papers WHERE semantic_scholar_id = ?",
                    (s2_id,)
                ).fetchone()
                if row:
                    return row["id"]
            finally:
                conn.close()

        # 4. Title fuzzy match
        ref_title = ref.get("title", "")
        if ref_title and len(ref_title) > 10:
            cleaned = re.sub(r"[^a-z0-9\s]", "", ref_title.lower()).strip()
            tokens = set(cleaned.split())
            conn = self.db.get_connection()
            try:
                rows = conn.execute(
                    "SELECT id, title FROM papers WHERE title IS NOT NULL"
                ).fetchall()
                for row in rows:
                    local_title = row["title"].lower()
                    local_clean = re.sub(r"[^a-z0-9\s]", "", local_title).strip()
                    local_tokens = set(local_clean.split())
                    if len(tokens) < 3 or len(local_tokens) < 3:
                        continue
                    overlap = tokens & local_tokens
                    score = 2 * len(overlap) / (len(tokens) + len(local_tokens))
                    if score >= TITLE_MATCH_THRESHOLD:
                        return row["id"]
            finally:
                conn.close()

        return None

    # ── Edge storage ─────────────────────────────────────────────

    def _store_edge(self, source_id: int, target_local_id: Optional[int],
                    ref: Dict, relationship: str = "cites") -> bool:
        """Insert a single citation edge, skipping duplicates."""
        conn = self.db.get_connection()
        try:
            # Check for existing edge (internal→internal)
            if target_local_id:
                existing = conn.execute(
                    "SELECT id FROM citation_edges WHERE source_paper_id = ? "
                    "AND target_paper_id = ? AND relationship = ?",
                    (source_id, target_local_id, relationship)
                ).fetchone()
                if existing:
                    return False
            # Check for existing edge (internal→external)
            else:
                ext_id = ref.get("paperId")
                if ext_id:
                    existing = conn.execute(
                        "SELECT id FROM citation_edges WHERE source_paper_id = ? "
                        "AND target_external_id = ? AND relationship = ?",
                        (source_id, ext_id, relationship)
                    ).fetchone()
                    if existing:
                        return False

            metadata = json.dumps({
                "s2_paper_id": ref.get("paperId"),
                "external_ids": ref.get("externalIds"),
                "citation_count": ref.get("citationCount"),
                "reference_count": ref.get("referenceCount"),
            })

            conn.execute("""
                INSERT INTO citation_edges
                    (source_paper_id, target_paper_id, target_external_id,
                     target_title, target_year, relationship, confidence,
                     source, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id,
                target_local_id,
                ref.get("paperId") if not target_local_id else None,
                ref.get("title"),
                ref.get("year"),
                relationship,
                "high" if target_local_id else "medium",
                "semantic_scholar",
                metadata
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to store edge: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    # ── Graph building ───────────────────────────────────────────

    def build_for_paper(self, paper_id: int,
                        include_references: bool = True,
                        include_citations: bool = True,
                        max_refs: int = 500) -> Dict[str, int]:
        """Fetch references and citations for one paper and store edges.

        Returns counts of {references_added, citations_added, errors}.
        """
        counts = {"references_added": 0, "citations_added": 0, "errors": 0}
        s2_id = self._get_s2_id_for_paper(paper_id)
        if not s2_id:
            logger.warning(f"No S2 identifier for paper {paper_id}, skipping")
            counts["errors"] += 1
            return counts

        if include_references:
            refs = self._fetch_references(s2_id, limit=max_refs)
            logger.info(f"Paper {paper_id}: fetched {len(refs)} references")
            for ref in refs:
                local_id = self._match_to_local(ref)
                if self._store_edge(paper_id, local_id, ref, "cites"):
                    counts["references_added"] += 1

        if include_citations:
            cites = self._fetch_citations(s2_id, limit=max_refs)
            logger.info(f"Paper {paper_id}: fetched {len(cites)} citations")
            for cit in cites:
                local_id = self._match_to_local(cit)
                if self._store_edge(paper_id, local_id, cit, "cited_by"):
                    counts["citations_added"] += 1

        return counts

    def build_for_all(self, limit: Optional[int] = None,
                      offset: int = 0,
                      include_references: bool = True,
                      include_citations: bool = True,
                      max_refs: int = 200,
                      papers_per_second: float = 0.5) -> Dict[str, int]:
        """Iterate over all papers in the DB and build the graph.

        Respects rate limits (papers_per_second).
        Returns aggregate counts.
        """
        conn = self.db.get_connection()
        try:
            query = "SELECT id FROM papers ORDER BY id"
            params = []
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            if offset:
                query += " OFFSET ?"
                params.append(offset)
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        total = {"references_added": 0, "citations_added": 0, "errors": 0}
        for i, row in enumerate(rows):
            pid = row["id"]
            logger.info(
                f"[{i + 1}/{len(rows)}] Building graph for paper {pid}..."
            )
            counts = self.build_for_paper(
                pid, include_references, include_citations, max_refs
            )
            for k in total:
                total[k] += counts[k]
            if i < len(rows) - 1:
                time.sleep(1.0 / papers_per_second)

        return total

    # ── Query methods ────────────────────────────────────────────

    def get_references(self, paper_id: int,
                       include_external: bool = False) -> List[Dict[str, Any]]:
        """Papers that this paper cites (outgoing edges)."""
        conn = self.db.get_connection()
        try:
            query = """
                SELECT ce.*, p.title as local_title,
                       p.pmid, p.doi, p.year as local_year,
                       p.study_type, p.exposure_method, p.cannabis_type
                FROM citation_edges ce
                LEFT JOIN papers p ON ce.target_paper_id = p.id
                WHERE ce.source_paper_id = ? AND ce.relationship = 'cites'
                ORDER BY ce.created_at DESC
            """
            rows = conn.execute(query, (paper_id,)).fetchall()
            results = []
            for r in rows:
                entry = {
                    "relationship": "cites",
                    "confidence": r["confidence"],
                    "source": r["source"],
                }
                if r["target_paper_id"]:
                    entry["paper_id"] = r["target_paper_id"]
                    entry["title"] = r["local_title"]
                    entry["pmid"] = r["pmid"]
                    entry["doi"] = r["doi"]
                    entry["year"] = r["local_year"]
                    entry["study_type"] = r["study_type"]
                    entry["exposure_method"] = r["exposure_method"]
                    entry["cannabis_type"] = r["cannabis_type"]
                    entry["is_internal"] = True
                elif include_external:
                    entry["title"] = r["target_title"]
                    entry["year"] = r["target_year"]
                    entry["external_id"] = r["target_external_id"]
                    entry["is_internal"] = False
                else:
                    continue
                results.append(entry)
            return results
        finally:
            conn.close()

    def get_cited_by(self, paper_id: int,
                     include_external: bool = False) -> List[Dict[str, Any]]:
        """Papers that cite this paper (incoming edges).

        Papers that cite `paper_id` appear in two edge patterns:
        1. cited_by edges where paper_id is the SOURCE (source→target: paper_id IS CITED BY target)
        2. cites edges where paper_id is the TARGET (source→target: source CITES paper_id)
        We UNION both and deduplicate by the citing paper's ID.
        """
        conn = self.db.get_connection()
        try:
            query = """
                SELECT ce.relationship, ce.confidence, ce.source,
                       ce.target_paper_id AS citing_paper_id,
                       ce.target_external_id AS citing_external_id,
                       ce.target_title AS citing_title,
                       ce.target_year AS citing_year,
                       p.title as local_title,
                       p.pmid, p.doi, p.year as local_year,
                       p.study_type, p.exposure_method, p.cannabis_type,
                       ce.created_at
                FROM citation_edges ce
                LEFT JOIN papers p ON ce.target_paper_id = p.id
                WHERE ce.source_paper_id = ? AND ce.relationship = 'cited_by'
                UNION
                SELECT ce.relationship, ce.confidence, ce.source,
                       ce.source_paper_id AS citing_paper_id,
                       NULL AS citing_external_id,
                       NULL AS citing_title,
                       NULL AS citing_year,
                       p.title as local_title,
                       p.pmid, p.doi, p.year as local_year,
                       p.study_type, p.exposure_method, p.cannabis_type,
                       ce.created_at
                FROM citation_edges ce
                JOIN papers p ON ce.source_paper_id = p.id
                WHERE ce.target_paper_id = ? AND ce.relationship = 'cites'
                ORDER BY created_at DESC
            """
            rows = conn.execute(query, (paper_id, paper_id)).fetchall()
            seen = set()
            results = []
            for r in rows:
                cid = r["citing_paper_id"]
                if cid and cid in seen:
                    continue
                if cid:
                    seen.add(cid)
                entry = {
                    "relationship": "cited_by",
                    "confidence": r["confidence"],
                    "source": r["source"],
                }
                if r["citing_paper_id"]:
                    entry["paper_id"] = r["citing_paper_id"]
                    entry["title"] = r["local_title"]
                    entry["pmid"] = r["pmid"]
                    entry["doi"] = r["doi"]
                    entry["year"] = r["local_year"]
                    entry["study_type"] = r["study_type"]
                    entry["exposure_method"] = r["exposure_method"]
                    entry["cannabis_type"] = r["cannabis_type"]
                    entry["is_internal"] = True
                elif include_external and r["citing_external_id"]:
                    entry["title"] = r["citing_title"]
                    entry["year"] = r["citing_year"]
                    entry["external_id"] = r["citing_external_id"]
                    entry["is_internal"] = False
                else:
                    continue
                results.append(entry)
            return results
        finally:
            conn.close()

    def get_connected_papers(self, paper_id: int,
                             max_depth: int = 2,
                             min_confidence: str = "low") -> Dict[int, Dict]:
        """BFS traversal returning all reachable papers within max_depth hops.

        Returns {paper_id: {depth, path, ...}}.
        """
        confidence_rank = {"high": 3, "medium": 2, "low": 1}
        min_rank = confidence_rank.get(min_confidence, 1)

        visited: Dict[int, Dict] = {}
        queue: List[Tuple[int, int, List[int]]] = [(paper_id, 0, [])]

        while queue:
            current_id, depth, path = queue.pop(0)
            if depth > max_depth:
                continue
            if current_id in visited:
                continue
            visited[current_id] = {
                "depth": depth,
                "path": path + [current_id],
            }
            if depth == max_depth:
                continue

            conn = self.db.get_connection()
            try:
                rows = conn.execute("""
                    SELECT target_paper_id AS neighbor, relationship,
                           confidence
                    FROM citation_edges
                    WHERE source_paper_id = ? AND target_paper_id IS NOT NULL
                    UNION
                    SELECT source_paper_id AS neighbor, relationship,
                           confidence
                    FROM citation_edges
                    WHERE target_paper_id = ? AND source_paper_id IS NOT NULL
                """, (current_id, current_id)).fetchall()
            finally:
                conn.close()

            for r in rows:
                nid = r["neighbor"]
                if nid is None or nid in visited:
                    continue
                rank = confidence_rank.get(r["confidence"], 1)
                if rank < min_rank:
                    continue
                queue.append((nid, depth + 1, path + [current_id]))

        return visited

    def get_graph_stats(self) -> Dict[str, Any]:
        """Summary statistics about the citation graph."""
        conn = self.db.get_connection()
        try:
            total_edges = conn.execute(
                "SELECT COUNT(*) as c FROM citation_edges"
            ).fetchone()["c"]

            internal_edges = conn.execute("""
                SELECT COUNT(*) as c FROM citation_edges
                WHERE target_paper_id IS NOT NULL
            """).fetchone()["c"]

            external_edges = conn.execute("""
                SELECT COUNT(*) as c FROM citation_edges
                WHERE target_paper_id IS NULL
            """).fetchone()["c"]

            papers_with_refs = conn.execute("""
                SELECT COUNT(DISTINCT source_paper_id) as c
                FROM citation_edges WHERE relationship = 'cites'
            """).fetchone()["c"]

            papers_with_citations = conn.execute("""
                SELECT COUNT(DISTINCT source_paper_id) as c
                FROM citation_edges WHERE relationship = 'cited_by'
            """).fetchone()["c"]

            edge_types = conn.execute("""
                SELECT relationship, COUNT(*) as c
                FROM citation_edges GROUP BY relationship
            """).fetchall()

            return {
                "total_edges": total_edges,
                "internal_edges": internal_edges,
                "external_edges": external_edges,
                "papers_with_references": papers_with_refs,
                "papers_with_citations": papers_with_citations,
                "edge_types": {r["relationship"]: r["c"] for r in edge_types},
            }
        finally:
            conn.close()

    def get_network_data(self, max_nodes: int = 2000) -> Dict[str, List]:
        """Return all internal nodes with degree counts and internal-internal edges.

        Returns: {nodes: [{id, title, year, degree}], edges: [{source, target, relationship}]}
        Limited to top ``max_nodes`` by degree to keep the payload manageable.
        """
        conn = self.db.get_connection()
        try:
            # Compute degree (total edges incident to each node)
            degree_rows = conn.execute("""
                    SELECT nid, SUM(cnt) AS degree FROM (
                        SELECT source_paper_id AS nid, COUNT(*) AS cnt
                        FROM citation_edges GROUP BY source_paper_id
                        UNION ALL
                        SELECT target_paper_id AS nid, COUNT(*) AS cnt
                        FROM citation_edges WHERE target_paper_id IS NOT NULL
                        GROUP BY target_paper_id
                    )
                    GROUP BY nid ORDER BY degree DESC LIMIT ?
            """, (max_nodes,)).fetchall()

            node_ids = set(r["nid"] for r in degree_rows)
            degree_map = {r["nid"]: r["degree"] for r in degree_rows}

            # Fetch paper metadata
            placeholders = ",".join("?" for _ in node_ids)
            nodes = []
            if node_ids:
                paper_rows = conn.execute(f"""
                    SELECT id, title, year FROM papers WHERE id IN ({placeholders})
                """, tuple(node_ids)).fetchall()
                paper_map = {r["id"]: {"title": r["title"], "year": r["year"]} for r in paper_rows}
                for nid in node_ids:
                    p = paper_map.get(nid, {"title": "Unknown", "year": None})
                    nodes.append({
                        "id": nid,
                        "title": p["title"],
                        "year": p["year"],
                        "degree": degree_map.get(nid, 0),
                    })

            # Fetch internal-internal edges (only between our top nodes)
            edges = []
            if node_ids:
                edge_rows = conn.execute(f"""
                    SELECT source_paper_id, target_paper_id, relationship
                    FROM citation_edges
                    WHERE source_paper_id IN ({placeholders})
                      AND target_paper_id IN ({placeholders})
                """, tuple(node_ids) + tuple(node_ids)).fetchall()
                for r in edge_rows:
                    edges.append({
                        "source": r["source_paper_id"],
                        "target": r["target_paper_id"],
                        "relationship": r["relationship"],
                    })

            return {"nodes": nodes, "edges": edges}
        finally:
            conn.close()

    def clear_paper_edges(self, paper_id: int):
        """Remove all edges for a given paper (for rebuilding)."""
        conn = self.db.get_connection()
        try:
            conn.execute(
                "DELETE FROM citation_edges WHERE source_paper_id = ?",
                (paper_id,)
            )
            conn.execute(
                "DELETE FROM citation_edges WHERE target_paper_id = ?",
                (paper_id,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to clear edges for paper {paper_id}: {e}")
            conn.rollback()
        finally:
            conn.close()

    def clear_all_edges(self):
        """Remove all citation edges."""
        conn = self.db.get_connection()
        try:
            conn.execute("DELETE FROM citation_edges")
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to clear all edges: {e}")
            conn.rollback()
        finally:
            conn.close()
