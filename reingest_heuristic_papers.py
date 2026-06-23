"""Re-run Maude classification on legacy heuristic/maude papers (two-pass or full-text)."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Literal, Optional

import classifier
import maude_confidence
import paper_text_cache
from calibration_pdf import has_direct_pdf_link, has_pmc_lookup_ids
from db_manager import DatabaseManager, _SQL_ORIGINAL_RESEARCH, _TAB_SQL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

PassMode = Literal["full", "fast", "slow"]

UPDATE_COLUMNS = [
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "duration_days",
    "inhaled_exposure_duration",
    "administration_frequency",
    "treatment_duration",
    "sample_size",
    "thc_pct",
    "cbd_pct",
    "dose_mg",
    "strain_reported",
    "strain_normalized",
    "publication_type",
    "ingestion_status",
    "species",
    "summary",
    "classification_confidence",
    "classification_timestamp",
    "classifier_version",
]

TRACK_FIELDS = [
    "publication_type",
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "classifier_version",
]

CLASSIFICATION_FIELDS = [
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
]


def parse_json_field(val):
    """Parse a JSON-encoded DB field into native Python values."""
    if val is None:
        return None
    if isinstance(val, (list, dict, int, float)):
        return val
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("[") or val.startswith("{"):
            try:
                return json.loads(val)
            except Exception:
                pass
        return val
    return val


def norm(val):
    """Normalize list/JSON DB values for change comparison."""
    parsed = parse_json_field(val)
    if isinstance(parsed, list):
        return sorted(str(x) for x in parsed)
    return parsed


def serialize(field, val):
    """Serialize extracted values for DB writes."""
    if field in {"study_type", "exposure_method", "cannabis_type", "outcome_domain"}:
        return json.dumps(val) if val is not None else None
    return val


def _not_llm_clause() -> str:
    """SQL fragment excluding LLM-classified papers."""
    return (
        "(classifier_version NOT LIKE 'llm-reclassify-%' "
        "AND classifier_version NOT LIKE 'llm-pdf-%' "
        "AND classifier_version NOT LIKE 'llm-node%')"
    )


def _tabs_where_clause(tabs: List[str]) -> str:
    """Builds a WHERE clause for UI tab membership (excludes LLM-classified papers)."""
    parts = []
    for tab in tabs:
        tab_key = tab.strip()
        if tab_key not in _TAB_SQL:
            raise ValueError(f"Unknown tab {tab_key!r}; expected one of {sorted(_TAB_SQL)}")
        parts.append(f"({_TAB_SQL[tab_key]})")
    return f"({' OR '.join(parts)}) AND {_not_llm_clause()}"


def _reingest_where_clause(
    *,
    only_heuristic: bool = True,
    maude_and_heuristic: bool = False,
) -> str:
    """Builds the SQL WHERE clause for Maude re-ingestion targets."""
    if maude_and_heuristic:
        classifier_filter = (
            "(classifier_version LIKE 'maude-%' "
            "OR classifier_version LIKE 'heuristic%')"
        )
    elif only_heuristic:
        classifier_filter = "classifier_version = 'heuristic-1.0.0'"
    else:
        classifier_filter = (
            "classifier_version IS NULL "
            "OR classifier_version = 'heuristic-1.0.0' "
            "OR classifier_version LIKE 'heuristic-reclassify%'"
        )
    return f"{classifier_filter} AND {_SQL_ORIGINAL_RESEARCH} AND {_not_llm_clause()}"


def _slow_pass_extra_clause() -> str:
    """SQL fragment for slow-pass eligibility (text source or sparse classification fields)."""
    text_source = (
        "((pmid IS NOT NULL AND TRIM(pmid) != '') "
        "OR (doi IS NOT NULL AND TRIM(doi) != '') "
        "OR (full_text_link IS NOT NULL AND TRIM(full_text_link) != '' "
        "AND full_text_link NOT LIKE '%pubmed.ncbi.nlm.nih.gov/%'))"
    )
    sparse_fields = (
        "(study_type IS NULL OR TRIM(study_type) = '' OR study_type = '[]' "
        "OR exposure_method IS NULL OR TRIM(exposure_method) = '' OR exposure_method = '[]' "
        "OR cannabis_type IS NULL OR TRIM(cannabis_type) = '' OR cannabis_type = '[]' "
        "OR outcome_domain IS NULL OR TRIM(outcome_domain) = '' OR outcome_domain = '[]')"
    )
    return f"({text_source} OR {sparse_fields})"


def paper_has_sparse_classification_fields(paper: Dict[str, Any]) -> bool:
    """Returns True when key Maude classification list fields are empty or missing."""
    for field in CLASSIFICATION_FIELDS:
        val = parse_json_field(paper.get(field))
        if val is None or val == "" or val == []:
            return True
    return False


def paper_has_fulltext_source(paper: Dict[str, Any]) -> bool:
    """Returns True when the paper has a PMC id or a direct PDF link."""
    return has_pmc_lookup_ids(
        pmid=paper.get("pmid"),
        doi=paper.get("doi"),
    ) or has_direct_pdf_link(paper.get("full_text_link"))


def paper_needs_slow_pass(paper: Dict[str, Any]) -> bool:
    """Returns True when slow pass should attempt PDF/full-text Maude classification."""
    return paper_has_fulltext_source(paper) or paper_has_sparse_classification_fields(paper)


def _current_maude_versions(rules_version: str) -> tuple:
    """Returns classifier_version labels for abstract, pdf, and fulltext tiers."""
    return (
        f"maude-{rules_version}",
        f"maude-pdf-{rules_version}",
        f"maude-fulltext-{rules_version}",
    )


def _already_at_pass_tier(paper: Dict[str, Any], pass_mode: PassMode, rules_version: str) -> bool:
    """Returns True when the paper already has the target tier for this pass."""
    version = str(paper.get("classifier_version") or "")
    abstract_v, pdf_v, fulltext_v = _current_maude_versions(rules_version)
    if pass_mode == "fast":
        return version in {abstract_v, pdf_v, fulltext_v}
    if pass_mode == "slow":
        return version in {pdf_v, fulltext_v}
    return False


def _where_for_pass(
    *,
    pass_mode: PassMode,
    only_heuristic: bool,
    maude_and_heuristic: bool,
    tabs: Optional[List[str]] = None,
) -> str:
    """Builds the WHERE clause for a specific re-ingest pass."""
    if tabs:
        base = _tabs_where_clause(tabs)
    else:
        base = _reingest_where_clause(
            only_heuristic=only_heuristic,
            maude_and_heuristic=maude_and_heuristic,
        )
    if pass_mode == "slow":
        return f"{base} AND {_slow_pass_extra_clause()}"
    return base


def _fetch_target_papers(
    db: DatabaseManager,
    *,
    pass_mode: PassMode,
    only_heuristic: bool,
    maude_and_heuristic: bool,
    tabs: Optional[List[str]] = None,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Loads paper rows for the requested pass."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    where_clause = _where_for_pass(
        pass_mode=pass_mode,
        only_heuristic=only_heuristic,
        maude_and_heuristic=maude_and_heuristic,
        tabs=tabs,
    )
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    cur.execute(
        f"""
        SELECT id, pmid, doi, title, abstract, full_text_link, expert_locked_fields,
               study_type, exposure_method, cannabis_type, outcome_domain,
               publication_type, duration_days, classification_confidence, classifier_version
        FROM papers
        WHERE {where_clause}
        ORDER BY id
        {limit_sql}
        """
    )
    papers = [dict(row) for row in cur.fetchall()]
    conn.close()
    return papers


def _classify_one_paper(
    paper: Dict[str, Any],
    *,
    pass_mode: PassMode,
    memory_cache: Dict[str, Optional[str]],
    cache_lock: Optional[Lock] = None,
) -> Dict[str, Any]:
    """Runs Maude classification for a single paper (thread-safe cache optional)."""
    abstract_only = pass_mode == "fast"
    resolved_text = None
    if pass_mode in {"full", "slow"}:
        if cache_lock:
            with cache_lock:
                resolved_text, _ = paper_text_cache.resolve_paper_text(
                    paper_id=paper["id"],
                    full_text_link=paper.get("full_text_link"),
                    pmid=paper.get("pmid"),
                    doi=paper.get("doi"),
                    memory_cache=memory_cache,
                )
        else:
            resolved_text, _ = paper_text_cache.resolve_paper_text(
                paper_id=paper["id"],
                full_text_link=paper.get("full_text_link"),
                pmid=paper.get("pmid"),
                doi=paper.get("doi"),
                memory_cache=memory_cache,
            )

    return classifier.process_paper_metadata(
        paper.get("title") or "",
        paper.get("abstract") or "",
        run_llm=False,
        full_text=resolved_text,
        full_text_link=paper.get("full_text_link"),
        pmid=paper.get("pmid"),
        doi=paper.get("doi"),
        pdf_cache=memory_cache,
        abstract_only=abstract_only,
    )


def _source_bucket(classifier_version: str) -> str:
    """Maps classifier_version to pdf/fulltext/abstract source bucket."""
    version = str(classifier_version or "")
    if version.startswith("maude-pdf-"):
        return "pdf"
    if version.startswith("maude-fulltext-"):
        return "fulltext"
    return "abstract"


def _apply_paper_update(
    db: DatabaseManager,
    conn,
    cur,
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
) -> None:
    """Writes classification fields for one paper and syncs tab flags."""
    locked = parse_json_field(paper.get("expert_locked_fields")) or []
    if not isinstance(locked, list):
        locked = []
    set_parts = []
    params = []
    for col in UPDATE_COLUMNS:
        if col in locked:
            continue
        set_parts.append(f"{col} = ?")
        params.append(serialize(col, extracted.get(col)))
    params.append(paper["id"])
    cur.execute(f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?", params)
    db.sync_tab_flags_for_paper(paper["id"], conn=conn)


def _reopen_connection(db: DatabaseManager):
    """Returns a fresh connection and cursor after commit or disconnect."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    return conn, conn.cursor()


def reingest_heuristic_papers(
    dry_run: bool = False,
    batch_size: int = 25,
    limit: Optional[int] = None,
    only_heuristic: bool = True,
    maude_and_heuristic: bool = False,
    pass_mode: PassMode = "full",
    workers: int = 1,
    skip_current_version: bool = True,
    tabs: Optional[List[str]] = None,
) -> dict:
    """Re-classify papers with the current Maude pipeline.

    Args:
        dry_run: When True, compute changes without writing to the database.
        batch_size: Commit interval for database writes.
        limit: Optional maximum number of papers to process.
        only_heuristic: When True, target heuristic-1.0.0 papers only.
        maude_and_heuristic: When True, target all maude-* and heuristic-* original
            research papers that were not LLM-classified (overrides only_heuristic).
        pass_mode: ``fast`` (abstract-only), ``slow`` (PDF/PMC + cache), or ``full``.
        workers: Thread pool size for classification (slow/full pass; use 1 for fast).
        skip_current_version: Skip papers already stamped with the current rules version
            for the pass tier.
        tabs: Optional UI tab keys (e.g. ``tangential``, ``unclassified_preclinical``).

    Returns:
        Summary statistics for the run.
    """
    db = DatabaseManager()
    rules_version = classifier.get_rules_version()
    papers = _fetch_target_papers(
        db,
        pass_mode=pass_mode,
        only_heuristic=only_heuristic,
        maude_and_heuristic=maude_and_heuristic,
        tabs=tabs,
        limit=limit,
    )

    if pass_mode == "slow":
        papers = [p for p in papers if paper_needs_slow_pass(p)]

    if skip_current_version:
        papers = [p for p in papers if not _already_at_pass_tier(p, pass_mode, rules_version)]

    total = len(papers)
    print(
        f"Starting Maude re-ingestion pass={pass_mode} for {total} papers "
        f"(dry_run={dry_run}, workers={workers}, limit={limit}) "
        f"at {datetime.now().isoformat()}"
    )

    field_change_counts: Counter = Counter()
    source_counts: Counter = Counter()
    papers_changed = 0
    papers_skipped = 0
    memory_cache: Dict[str, Optional[str]] = {}
    cache_lock = Lock() if workers > 1 else None
    start = time.time()

    conn, cur = _reopen_connection(db)

    if workers > 1 and pass_mode != "fast":
        indexed = list(enumerate(papers, 1))
        chunk_size = max(batch_size, workers * 2)
        for chunk_start in range(0, len(indexed), chunk_size):
            chunk = indexed[chunk_start:chunk_start + chunk_size]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        _classify_one_paper,
                        paper,
                        pass_mode=pass_mode,
                        memory_cache=memory_cache,
                        cache_lock=cache_lock,
                    ): (idx, paper)
                    for idx, paper in chunk
                }
                for future in as_completed(futures):
                    idx, paper = futures[future]
                    try:
                        extracted = future.result()
                    except Exception as exc:
                        logger.error("Parallel classification failed for %s: %s", paper.get("id"), exc)
                        continue

                    version = str(extracted.get("classifier_version") or "")
                    source_counts[_source_bucket(version)] += 1
                    locked = parse_json_field(paper.get("expert_locked_fields")) or []
                    if not isinstance(locked, list):
                        locked = []
                    paper_changed = False
                    for field in TRACK_FIELDS:
                        if field in locked:
                            continue
                        if norm(paper.get(field)) != norm(extracted.get(field)):
                            field_change_counts[field] += 1
                            paper_changed = True
                    if paper_changed:
                        papers_changed += 1
                    if not dry_run:
                        try:
                            _apply_paper_update(db, conn, cur, paper, extracted)
                        except Exception as exc:
                            logger.warning("DB write failed, reconnecting: %s", exc)
                            try:
                                conn.commit()
                            except Exception:
                                pass
                            try:
                                conn.close()
                            except Exception:
                                pass
                            conn, cur = _reopen_connection(db)
                            _apply_paper_update(db, conn, cur, paper, extracted)

            if not dry_run:
                conn.commit()
                done = min(chunk_start + len(chunk), total)
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                eta = (total - done) / rate / 60 if rate else 0
                logger.info(
                    "Progress %s/%s changed=%s sources=%s elapsed=%.0fs eta=%.1fm",
                    done,
                    total,
                    papers_changed,
                    dict(source_counts),
                    elapsed,
                    eta,
                )
    else:
        for idx, paper in enumerate(papers, 1):
            if skip_current_version and _already_at_pass_tier(paper, pass_mode, rules_version):
                papers_skipped += 1
                continue
            if pass_mode == "slow" and not paper_needs_slow_pass(paper):
                papers_skipped += 1
                continue

            try:
                extracted = _classify_one_paper(
                    paper,
                    pass_mode=pass_mode,
                    memory_cache=memory_cache,
                    cache_lock=cache_lock,
                )
            except Exception as exc:
                logger.error("Classification failed for paper %s: %s", paper.get("id"), exc)
                continue

            version = str(extracted.get("classifier_version") or "")
            source_counts[_source_bucket(version)] += 1

            locked = parse_json_field(paper.get("expert_locked_fields")) or []
            if not isinstance(locked, list):
                locked = []
            paper_changed = False
            for field in TRACK_FIELDS:
                if field in locked:
                    continue
                if norm(paper.get(field)) != norm(extracted.get(field)):
                    field_change_counts[field] += 1
                    paper_changed = True
            if paper_changed:
                papers_changed += 1

            if not dry_run:
                try:
                    _apply_paper_update(db, conn, cur, paper, extracted)
                except Exception as exc:
                    logger.warning("DB write failed for paper %s, reconnecting: %s", paper.get("id"), exc)
                    try:
                        conn.commit()
                    except Exception:
                        pass
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn, cur = _reopen_connection(db)
                    _apply_paper_update(db, conn, cur, paper, extracted)

            if not dry_run and idx % batch_size == 0:
                conn.commit()
                elapsed = time.time() - start
                rate = idx / elapsed if elapsed else 0
                eta = (total - idx) / rate / 60 if rate else 0
                logger.info(
                    "Progress %s/%s changed=%s sources=%s elapsed=%.0fs eta=%.1fm",
                    idx,
                    total,
                    papers_changed,
                    dict(source_counts),
                    elapsed,
                    eta,
                )

    if not dry_run:
        conn.commit()
    conn.close()

    elapsed = time.time() - start
    summary = {
        "pass_mode": pass_mode,
        "tabs": tabs,
        "papers_processed": total,
        "papers_skipped": papers_skipped,
        "papers_changed": papers_changed,
        "elapsed_minutes": round(elapsed / 60, 1),
        "source_counts": dict(source_counts),
        "field_change_counts": dict(field_change_counts),
        "dry_run": dry_run,
        "rules_version": rules_version,
    }
    print(f"Maude re-ingestion complete: {summary}")
    return summary


def run_two_pass_reingest(
    dry_run: bool = False,
    batch_size: int = 50,
    limit: Optional[int] = None,
    fast_only: bool = False,
    slow_only: bool = False,
    workers: int = 4,
    refresh_maude_confidence: bool = True,
    skip_current_version: bool = True,
    tabs: Optional[List[str]] = None,
) -> dict:
    """Runs fast abstract-only pass then slow PDF/full-text pass on the non-LLM corpus.

    Args:
        dry_run: When True, compute changes without writing.
        batch_size: Commit interval for DB writes.
        limit: Optional cap per pass (for testing).
        fast_only: Run only the abstract-only pass.
        slow_only: Run only the PDF/full-text pass.
        workers: Parallel workers for the slow pass.
        refresh_maude_confidence: Refresh confidence scores after both passes.
        skip_current_version: Skip papers already at the current rules version.

    Returns:
        Combined summary for all passes executed.
    """
    combined: Dict[str, Any] = {"passes": []}
    if not slow_only:
        combined["passes"].append(
            reingest_heuristic_papers(
                dry_run=dry_run,
                batch_size=batch_size,
                limit=limit,
                maude_and_heuristic=True,
                pass_mode="fast",
                workers=1,
                skip_current_version=skip_current_version,
                tabs=tabs,
            )
        )
    if not fast_only:
        combined["passes"].append(
            reingest_heuristic_papers(
                dry_run=dry_run,
                batch_size=batch_size,
                limit=limit,
                maude_and_heuristic=bool(tabs) or True,
                pass_mode="slow",
                workers=workers,
                skip_current_version=skip_current_version,
                tabs=tabs,
            )
        )
    if refresh_maude_confidence and not dry_run:
        combined["confidence_refresh"] = refresh_maude_confidence_scores(batch_size=batch_size)
    return combined


def refresh_maude_confidence_scores(
    batch_size: int = 100,
    limit: Optional[int] = None,
) -> dict:
    """Recomputes classification_confidence for all maude-* papers from node alignment %."""
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    cur.execute(
        f"""
        SELECT id, title, abstract, publication_type, study_type, ingestion_status,
               classifier_version, classification_confidence
        FROM papers
        WHERE classifier_version LIKE 'maude-%'
        ORDER BY id
        {limit_sql}
        """
    )
    papers = cur.fetchall()
    updated = 0
    for idx, row in enumerate(papers, 1):
        paper = dict(row)
        block = {
            "publication_type": paper.get("publication_type"),
            "study_type": parse_json_field(paper.get("study_type")) or [],
            "ingestion_status": paper.get("ingestion_status"),
        }
        confidence = maude_confidence.confidence_for_classification(block)
        if paper.get("classification_confidence") != confidence:
            cur.execute(
                "UPDATE papers SET classification_confidence = ? WHERE id = ?",
                (confidence, paper["id"]),
            )
            updated += 1
        if idx % batch_size == 0:
            conn.commit()
    conn.commit()
    conn.close()
    summary = {"papers_scanned": len(papers), "papers_updated": updated}
    print(f"Maude confidence refresh complete: {summary}")
    return summary


def main():
    """CLI entry point for Maude re-ingestion of heuristic papers."""
    parser = argparse.ArgumentParser(
        description="Re-classify heuristic/maude papers with Maude (two-pass or full-text)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute changes without writing to the database.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Commit interval for database writes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of papers to re-classify per pass.",
    )
    parser.add_argument(
        "--all-native",
        action="store_true",
        help="Include NULL/heuristic-reclassify native papers, not only heuristic-1.0.0.",
    )
    parser.add_argument(
        "--maude-and-heuristic",
        action="store_true",
        help=(
            "Re-classify all maude-* and heuristic-* original research papers "
            "that were not LLM-classified (excludes reviews)."
        ),
    )
    parser.add_argument(
        "--pass",
        dest="pass_mode",
        choices=("full", "fast", "slow", "two-pass"),
        default="full",
        help="Classification pass: full (legacy), fast (abstract-only), slow (PDF/PMC), two-pass (fast then slow).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel workers for slow/full pass (default: 4).",
    )
    parser.add_argument(
        "--fast-only",
        action="store_true",
        help="With --pass two-pass, run only the abstract-only pass.",
    )
    parser.add_argument(
        "--slow-only",
        action="store_true",
        help="With --pass two-pass, run only the PDF/full-text pass.",
    )
    parser.add_argument(
        "--no-skip-current",
        action="store_true",
        help="Re-classify papers even if already stamped with the current rules version.",
    )
    parser.add_argument(
        "--tabs",
        default=None,
        help="Comma-separated UI tabs to target (e.g. tangential,unclassified_preclinical).",
    )
    parser.add_argument(
        "--refresh-maude-confidence",
        action="store_true",
        help="After re-ingestion, refresh classification_confidence on all maude-* papers.",
    )
    parser.add_argument(
        "--confidence-only",
        action="store_true",
        help="Only refresh maude-* classification_confidence from node alignment (no re-ingest).",
    )
    args = parser.parse_args()
    if args.confidence_only:
        refresh_maude_confidence_scores(batch_size=args.batch_size, limit=args.limit)
        return

    skip_current = not args.no_skip_current
    tab_list = [t.strip() for t in args.tabs.split(",")] if args.tabs else None
    if args.pass_mode == "two-pass":
        summary = run_two_pass_reingest(
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            fast_only=args.fast_only,
            slow_only=args.slow_only,
            workers=args.workers,
            refresh_maude_confidence=args.refresh_maude_confidence,
            skip_current_version=skip_current,
            tabs=tab_list,
        )
        print(json.dumps(summary, indent=2, default=str))
        return

    summary = reingest_heuristic_papers(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
        only_heuristic=not args.all_native and not args.maude_and_heuristic,
        maude_and_heuristic=args.maude_and_heuristic,
        pass_mode=args.pass_mode,
        workers=args.workers,
        skip_current_version=skip_current,
        tabs=tab_list,
    )
    if args.refresh_maude_confidence or summary.get("papers_processed", 0) > 0:
        refresh_maude_confidence_scores(batch_size=args.batch_size)


if __name__ == "__main__":
    main()
