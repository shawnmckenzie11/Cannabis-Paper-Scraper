"""Re-run Maude classification on legacy heuristic/maude papers (two-pass or full-text)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue
from threading import Lock, Thread
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

import classifier
import maude_confidence
import paper_text_cache
import subnode_field_scopes
from calibration_pdf import has_direct_pdf_link, has_pmc_lookup_ids
from db_health import postgres_configured, postgres_is_healthy, production_reingest_limits
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
    "thc_mg_ml",
    "thc_mg_g",
    "thc_mg_kg",
    "thc_uM",
    "cbd_mg_ml",
    "cbd_mg_g",
    "cbd_mg_kg",
    "cbd_uM",
    "strain_reported",
    "strain_normalized",
    "publication_type",
    "ingestion_status",
    "species",
    "population_age",
    "population_sex",
    "inclusion_criteria",
    "exclusion_criteria",
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

SUBNODE_EXTRA_TRACK_FIELDS = [
    "ingestion_status",
    "species",
    "sample_size",
    "publication_type",
    "duration_days",
    "treatment_duration",
    "administration_frequency",
    "inhaled_exposure_duration",
    "repeat_exposure_count",
    "exposure_regimen_bin",
    "strain_reported",
    "strain_normalized",
    "dose_mg",
    "multiple_doses",
    "multiple_time_intervals",
    "population_age",
    "population_sex",
    "inclusion_criteria",
    "exclusion_criteria",
    "summary",
    "classification_confidence",
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


NOOP_SKIP_COLUMNS = frozenset({"classification_timestamp"})
DB_WRITE_MAX_RETRIES = 5
DB_WRITE_RETRY_BASE_SECONDS = 1.0


@dataclass
class ReingestStats:
    """Per-run counters and timing for re-ingest performance reporting."""

    classify_seconds: float = 0.0
    db_write_seconds: float = 0.0
    text_fetch_seconds: float = 0.0
    papers_written: int = 0
    papers_skipped_noop: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    written_paper_ids: Set[int] = field(default_factory=set)
    pre_reingest_snapshots: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    regression_merges: int = 0
    papers_skipped_tier_fast: int = 0


def _locked_fields(paper: Dict[str, Any]) -> List[str]:
    """Return expert-locked field names for a paper row."""
    locked = parse_json_field(paper.get("expert_locked_fields")) or []
    return locked if isinstance(locked, list) else []


def _tab_flag_inputs(
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
    locked: List[str],
) -> Tuple[Any, Any, Any]:
    """Return publication_type, study_type, ingestion_status for tab flag computation."""
    publication_type = (
        extracted.get("publication_type")
        if "publication_type" not in locked
        else paper.get("publication_type")
    )
    study_type = (
        extracted.get("study_type") if "study_type" not in locked else paper.get("study_type")
    )
    ingestion_status = (
        extracted.get("ingestion_status")
        if "ingestion_status" not in locked
        else paper.get("ingestion_status")
    )
    return publication_type, study_type, ingestion_status


def paper_update_is_noop(paper: Dict[str, Any], extracted: Dict[str, Any]) -> bool:
    """Return True when merged classification + tab updates would not change stored values."""
    locked = _locked_fields(paper)
    for col in UPDATE_COLUMNS:
        if col in locked or col in NOOP_SKIP_COLUMNS:
            continue
        if norm(paper.get(col)) != norm(extracted.get(col)):
            return False
    from paper_tab_flags import compute_tab_flags

    pub, study, ingest = _tab_flag_inputs(paper, extracted, locked)
    new_flags = compute_tab_flags(
        publication_type=pub,
        study_type=study,
        ingestion_status=ingest,
    )
    for tab_col, new_val in new_flags.items():
        if int(paper.get(tab_col) or 0) != int(new_val):
            return False
    return True


def build_merged_update(
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
) -> Tuple[List[str], List[Any]]:
    """Build SET clause fragments and params for classification + tab flags in one UPDATE."""
    from paper_tab_flags import TAB_FLAG_FIELDS, compute_tab_flags

    locked = _locked_fields(paper)
    set_parts: List[str] = []
    params: List[Any] = []
    for col in UPDATE_COLUMNS:
        if col in locked:
            continue
        set_parts.append(f"{col} = ?")
        params.append(serialize(col, extracted.get(col)))

    pub, study, ingest = _tab_flag_inputs(paper, extracted, locked)
    flags = compute_tab_flags(
        publication_type=pub,
        study_type=study,
        ingestion_status=ingest,
    )
    for column in TAB_FLAG_FIELDS.values():
        if column in flags:
            set_parts.append(f"{column} = ?")
            params.append(flags[column])

    params.append(paper["id"])
    return set_parts, params


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


def track_fields_for_scope_subnode(scope_subnode: Optional[str]) -> List[str]:
    """Return classification fields to diff-track for a golden subnode reingest."""
    if not scope_subnode:
        return list(TRACK_FIELDS)
    scoped = list(subnode_field_scopes.SUBNODE_FIELD_SCOPES.get(scope_subnode, []))
    seen: Set[str] = set()
    ordered: List[str] = []
    for field in scoped + list(TRACK_FIELDS) + SUBNODE_EXTRA_TRACK_FIELDS:
        if field in seen:
            continue
        if field in UPDATE_COLUMNS or field in CLASSIFICATION_FIELDS:
            seen.add(field)
            ordered.append(field)
    return ordered


def _subnode_where_clause(scope_subnode: str) -> str:
    """SQL WHERE fragment for all Maude/heuristic papers in a calibration subnode."""
    corpus = _reingest_where_clause(only_heuristic=False, maude_and_heuristic=True)
    if scope_subnode == "node2a":
        return f"({_tabs_where_clause(['clinical'])})"
    if scope_subnode == "node2b":
        return (
            f"({corpus}) AND (study_type LIKE '%Animal Models%') AND {_not_llm_clause()}"
        )
    if scope_subnode == "node2c":
        return (
            f"({corpus}) AND (study_type LIKE '%Cell Culture%') AND {_not_llm_clause()}"
        )
    raise ValueError(f"Unknown scope_subnode {scope_subnode!r}")


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
    """SQL fragment for slow-pass eligibility (text source, OA, or sparse/unknown fields)."""
    text_source = (
        "((pmid IS NOT NULL AND TRIM(pmid) != '') "
        "OR (doi IS NOT NULL AND TRIM(doi) != '') "
        "OR (full_text_link IS NOT NULL AND TRIM(full_text_link) != '' "
        "AND full_text_link NOT LIKE '%pubmed.ncbi.nlm.nih.gov/%'))"
    )
    open_access = "(open_access = 1)"
    sparse_fields = (
        "(study_type IS NULL OR TRIM(study_type) = '' OR study_type = '[]' "
        "OR study_type LIKE '%unknown%' "
        "OR exposure_method IS NULL OR TRIM(exposure_method) = '' OR exposure_method = '[]' "
        "OR exposure_method LIKE '%unknown%' "
        "OR cannabis_type IS NULL OR TRIM(cannabis_type) = '' OR cannabis_type = '[]' "
        "OR cannabis_type LIKE '%unknown%' "
        "OR outcome_domain IS NULL OR TRIM(outcome_domain) = '' OR outcome_domain = '[]' "
        "OR outcome_domain LIKE '%unknown%')"
    )
    return f"({text_source} OR {open_access} OR {sparse_fields})"


def paper_has_sparse_classification_fields(paper: Dict[str, Any]) -> bool:
    """Returns True when key Maude classification list fields are empty or unknown."""
    from classification_regression_guard import is_field_empty

    for field in CLASSIFICATION_FIELDS:
        if is_field_empty(paper.get(field)):
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
    if paper.get("open_access") in (1, True, "1"):
        return True
    return paper_has_fulltext_source(paper) or paper_has_sparse_classification_fields(paper)


def _current_maude_versions(rules_version: str) -> tuple:
    """Returns classifier_version labels for abstract, pdf, and fulltext tiers."""
    import calibration_pdf
    import os

    row_index: Optional[int] = None
    golden_row = os.getenv("GOLDEN_ROW_INDEX")
    if golden_row is not None:
        try:
            row_index = int(golden_row)
        except ValueError:
            row_index = None
    return calibration_pdf.maude_tier_classifier_versions(rules_version, row_index=row_index)


def _legacy_maude_fulltext_versions(rules_version: str) -> tuple:
    """Returns older full-text labels that should still count as slow-pass complete."""
    import calibration_pdf

    return (calibration_pdf.legacy_fulltext_classifier_version(rules_version),)


def _tier_versions_for_pass(pass_mode: PassMode, rules_version: str) -> tuple:
    """Return all classifier_version labels that satisfy the given pass tier."""
    abstract_v, pdf_v, ft_v = _current_maude_versions(rules_version)
    legacy_ft = _legacy_maude_fulltext_versions(rules_version)
    if pass_mode == "fast":
        return (abstract_v, pdf_v, ft_v) + legacy_ft
    if pass_mode == "slow":
        return (pdf_v, ft_v) + legacy_ft
    return (abstract_v, pdf_v, ft_v) + legacy_ft


def _already_at_pass_tier(paper: Dict[str, Any], pass_mode: PassMode, rules_version: str) -> bool:
    """Returns True when the paper already has the target tier for this pass."""
    version = str(paper.get("classifier_version") or "")
    return version in set(_tier_versions_for_pass(pass_mode, rules_version))


def _skip_current_version_clause(pass_mode: PassMode, rules_version: str) -> str:
    """SQL fragment excluding papers already stamped at the current pass tier."""
    if pass_mode == "full":
        return "1=1"
    versions = _tier_versions_for_pass(pass_mode, rules_version)
    quoted = ", ".join(f"'{v}'" for v in versions)
    return f"(classifier_version IS NULL OR classifier_version NOT IN ({quoted}))"


def _where_for_pass(
    *,
    pass_mode: PassMode,
    only_heuristic: bool,
    maude_and_heuristic: bool,
    tabs: Optional[List[str]] = None,
    scope_subnode: Optional[str] = None,
    skip_current_version: bool = False,
    rules_version: Optional[str] = None,
    paper_ids: Optional[List[int]] = None,
) -> str:
    """Builds the WHERE clause for a specific re-ingest pass."""
    if paper_ids:
        id_list = sorted({int(pid) for pid in paper_ids})
        base = "id IN (" + ", ".join(str(pid) for pid in id_list) + ")"
    elif scope_subnode:
        base = _subnode_where_clause(scope_subnode)
    elif tabs:
        base = _tabs_where_clause(tabs)
    else:
        base = _reingest_where_clause(
            only_heuristic=only_heuristic,
            maude_and_heuristic=maude_and_heuristic,
        )
    if pass_mode == "slow":
        base = f"{base} AND {_slow_pass_extra_clause()}"
    if skip_current_version and rules_version:
        base = f"{base} AND {_skip_current_version_clause(pass_mode, rules_version)}"
    return base


def _fetch_target_papers(
    db: DatabaseManager,
    *,
    pass_mode: PassMode,
    only_heuristic: bool,
    maude_and_heuristic: bool,
    tabs: Optional[List[str]] = None,
    scope_subnode: Optional[str] = None,
    limit: Optional[int],
    skip_current_version: bool = False,
    rules_version: Optional[str] = None,
    paper_ids: Optional[List[int]] = None,
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
        scope_subnode=scope_subnode,
        skip_current_version=skip_current_version,
        rules_version=rules_version,
        paper_ids=paper_ids,
    )
    limit_sql = f" LIMIT {int(limit)}" if limit else ""
    cur.execute(
        f"""
        SELECT id, pmid, doi, title, abstract, full_text_link, expert_locked_fields,
               study_type, exposure_method, cannabis_type, outcome_domain,
               publication_type, duration_days, classification_confidence, classifier_version,
               ingestion_status, species, summary, classification_timestamp,
               tab_preclinical, tab_clinical, tab_unclassified_preclinical,
               tab_tangential, tab_review
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
    stats: Optional[ReingestStats] = None,
) -> Dict[str, Any]:
    """Runs Maude classification for a single paper (thread-safe cache optional)."""
    abstract_only = pass_mode == "fast"
    resolved_text = None
    resolved_source: Optional[str] = None
    if pass_mode in {"full", "slow"}:
        fetch_start = time.time()
        paper_id = paper.get("id")
        had_disk_cache = False
        if paper_id is not None:
            cached_text, _ = paper_text_cache.lookup_cached_text_for_paper(int(paper_id))
            had_disk_cache = bool(cached_text)

        def _resolve():
            return paper_text_cache.resolve_paper_text(
                paper_id=paper_id,
                full_text_link=paper.get("full_text_link"),
                pmid=paper.get("pmid"),
                doi=paper.get("doi"),
                memory_cache=memory_cache,
            )

        if cache_lock:
            with cache_lock:
                resolved_text, resolved_source = _resolve()
        else:
            resolved_text, resolved_source = _resolve()

        if stats is not None:
            stats.text_fetch_seconds += time.time() - fetch_start
            if had_disk_cache:
                stats.cache_hits += 1
            else:
                stats.cache_misses += 1

    classify_start = time.time()
    extracted = classifier.process_paper_metadata(
        paper.get("title") or "",
        paper.get("abstract") or "",
        run_llm=False,
        full_text=resolved_text,
        full_text_link=paper.get("full_text_link"),
        pmid=paper.get("pmid"),
        doi=paper.get("doi"),
        pdf_cache=memory_cache,
        abstract_only=abstract_only,
        text_source=resolved_source,
    )
    from classification_regression_guard import merge_regression_safe

    extracted, merge_meta = merge_regression_safe(
        paper,
        extracted,
        title=paper.get("title") or "",
        abstract=paper.get("abstract") or "",
    )
    if stats is not None and merge_meta.get("merged"):
        stats.regression_merges += 1
    if stats is not None:
        stats.classify_seconds += time.time() - classify_start
    return extracted


def _source_bucket(classifier_version: str) -> str:
    """Maps classifier_version to pdf/fulltext/abstract source bucket."""
    version = str(classifier_version or "")
    if version.startswith("maude-pdf-"):
        return "pdf"
    if version.startswith("maude-ft-") or version.startswith("maude-fulltext-"):
        return "fulltext"
    return "abstract"


def _apply_paper_update(
    db: DatabaseManager,
    conn,
    cur,
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
    stats: Optional[ReingestStats] = None,
) -> bool:
    """Writes classification + tab flags in one UPDATE. Returns True when a write occurred."""
    if paper_update_is_noop(paper, extracted):
        if stats is not None:
            stats.papers_skipped_noop += 1
        return False

    set_parts, params = build_merged_update(paper, extracted)
    if not set_parts:
        return False

    write_start = time.time()
    cur.execute(f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?", params)
    if stats is not None:
        stats.db_write_seconds += time.time() - write_start
        stats.papers_written += 1
        stats.written_paper_ids.add(int(paper["id"]))
    if not db.is_postgres:
        try:
            from local_sync import mark_papers_dirty

            mark_papers_dirty(conn, [int(paper["id"])])
        except Exception:
            logger.debug("Could not mark paper %s dirty for local sync push", paper.get("id"))
    return True


def _apply_paper_update_with_retry(
    db: DatabaseManager,
    conn,
    cur,
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
    stats: Optional[ReingestStats] = None,
) -> Tuple[Any, Any]:
    """Apply merged update with exponential backoff reconnect on transient DB errors."""
    if paper_update_is_noop(paper, extracted):
        if stats is not None:
            stats.papers_skipped_noop += 1
        return conn, cur

    last_exc: Optional[Exception] = None
    for attempt in range(DB_WRITE_MAX_RETRIES):
        try:
            _apply_paper_update(db, conn, cur, paper, extracted, stats=stats)
            return conn, cur
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "DB write failed for paper %s (attempt %s/%s): %s",
                paper.get("id"),
                attempt + 1,
                DB_WRITE_MAX_RETRIES,
                exc,
            )
            try:
                conn.commit()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            if attempt + 1 >= DB_WRITE_MAX_RETRIES:
                break
            delay = min(DB_WRITE_RETRY_BASE_SECONDS * (2 ** attempt), 30.0)
            time.sleep(delay)
            conn, cur = _reopen_connection(db)

    if last_exc is not None:
        raise last_exc
    return conn, cur


def _record_classification_diffs(
    paper: Dict[str, Any],
    extracted: Dict[str, Any],
    field_change_counts: Counter,
    *,
    track_fields: Optional[List[str]] = None,
    pre_reingest_snapshots: Optional[Dict[int, Dict[str, Any]]] = None,
) -> bool:
    """Track field-level diffs and return True when tracked fields changed."""
    locked = _locked_fields(paper)
    paper_changed = False
    for field in track_fields or TRACK_FIELDS:
        if field in locked:
            continue
        if norm(paper.get(field)) != norm(extracted.get(field)):
            field_change_counts[field] += 1
            paper_changed = True
    if paper_changed and pre_reingest_snapshots is not None:
        paper_id = int(paper["id"])
        pre_reingest_snapshots[paper_id] = {
            field: paper.get(field)
            for field in (track_fields or TRACK_FIELDS)
            if field not in locked
        }
    return paper_changed


def _reopen_connection(db: DatabaseManager):
    """Returns a fresh connection and cursor after commit or disconnect."""
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    return conn, conn.cursor()


def _run_parallel_reingest(
    db: DatabaseManager,
    papers: List[Dict[str, Any]],
    *,
    pass_mode: PassMode,
    dry_run: bool,
    batch_size: int,
    workers: int,
    stats: ReingestStats,
    field_change_counts: Counter,
    source_counts: Counter,
    start: float,
    total: int,
    batch_pause_seconds: float = 0.0,
    track_fields: Optional[List[str]] = None,
) -> Tuple[int, Any, Any]:
    """Classify papers in parallel and write through a dedicated writer thread."""
    memory_cache: Dict[str, Optional[str]] = {}
    cache_lock = Lock()
    papers_changed = 0
    conn: Any = None
    cur: Any = None
    write_queue: Queue = Queue()
    writer_error: List[Exception] = []

    def _writer_loop() -> None:
        nonlocal conn, cur
        conn, cur = _reopen_connection(db)
        pending = 0
        processed = 0
        while True:
            item = write_queue.get()
            try:
                if item is None:
                    if pending > 0:
                        conn.commit()
                    return
                paper, extracted = item
                conn, cur = _apply_paper_update_with_retry(
                    db, conn, cur, paper, extracted, stats=stats
                )
                pending += 1
                processed += 1
                if pending >= batch_size:
                    conn.commit()
                    pending = 0
                    if batch_pause_seconds > 0:
                        time.sleep(batch_pause_seconds)
                    elapsed = time.time() - start
                    rate = processed / elapsed if elapsed else 0
                    eta = (total - processed) / rate / 60 if rate else 0
                    logger.info(
                        "Progress %s/%s written=%s noop=%s elapsed=%.0fs eta=%.1fm",
                        processed,
                        total,
                        stats.papers_written,
                        stats.papers_skipped_noop,
                        elapsed,
                        eta,
                    )
            except Exception as exc:
                writer_error.append(exc)
                logger.error("Writer thread failed: %s", exc)
            finally:
                write_queue.task_done()
        if conn is not None:
            conn.close()

    writer = Thread(target=_writer_loop, daemon=True)
    if not dry_run:
        writer.start()

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
                    stats=stats,
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
                if _record_classification_diffs(
                    paper,
                    extracted,
                    field_change_counts,
                    track_fields=track_fields,
                    pre_reingest_snapshots=stats.pre_reingest_snapshots,
                ):
                    papers_changed += 1
                if not dry_run:
                    write_queue.put((paper, extracted))

        if dry_run:
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

    if not dry_run:
        write_queue.put(None)
        writer.join()
        if writer_error:
            raise writer_error[0]

    return papers_changed, None, None


def _run_sequential_reingest(
    db: DatabaseManager,
    papers: List[Dict[str, Any]],
    *,
    pass_mode: PassMode,
    dry_run: bool,
    batch_size: int,
    rules_version: str,
    skip_current_version: bool,
    stats: ReingestStats,
    field_change_counts: Counter,
    source_counts: Counter,
    start: float,
    total: int,
    batch_pause_seconds: float = 0.0,
    track_fields: Optional[List[str]] = None,
) -> Tuple[int, int, Any, Any]:
    """Classify and write papers sequentially (single worker)."""
    papers_changed = 0
    papers_skipped = 0
    memory_cache: Dict[str, Optional[str]] = {}
    conn, cur = _reopen_connection(db)

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
                stats=stats,
            )
        except Exception as exc:
            logger.error("Classification failed for paper %s: %s", paper.get("id"), exc)
            continue

        version = str(extracted.get("classifier_version") or "")
        source_counts[_source_bucket(version)] += 1
        if _record_classification_diffs(
            paper,
            extracted,
            field_change_counts,
            track_fields=track_fields,
            pre_reingest_snapshots=stats.pre_reingest_snapshots,
        ):
            papers_changed += 1

        if not dry_run:
            conn, cur = _apply_paper_update_with_retry(
                db, conn, cur, paper, extracted, stats=stats
            )

        if not dry_run and idx % batch_size == 0:
            conn.commit()
            if batch_pause_seconds > 0:
                time.sleep(batch_pause_seconds)
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed else 0
            eta = (total - idx) / rate / 60 if rate else 0
            logger.info(
                "Progress %s/%s changed=%s written=%s noop=%s elapsed=%.0fs eta=%.1fm",
                idx,
                total,
                papers_changed,
                stats.papers_written,
                stats.papers_skipped_noop,
                elapsed,
                eta,
            )

    return papers_changed, papers_skipped, conn, cur


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
    paper_ids: Optional[List[int]] = None,
    scope_subnode: Optional[str] = None,
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
        paper_ids: Optional explicit paper id list (overrides tab/heuristic filters).
        scope_subnode: When set (``node2a``/``node2b``/``node2c``), re-classify all
            papers in that calibration subnode from local SQLite.

    Returns:
        Summary statistics for the run.
    """
    track_fields = track_fields_for_scope_subnode(scope_subnode)
    db = DatabaseManager()
    if postgres_configured():
        healthy, detail = postgres_is_healthy()
        if not healthy:
            msg = f"Postgres unavailable; aborting re-ingest to protect the live app: {detail}"
            logger.error(msg)
            return {
                "pass_mode": pass_mode,
                "error": "postgres_unavailable",
                "detail": detail,
                "papers_processed": 0,
                "dry_run": dry_run,
            }

    limits = production_reingest_limits()
    if postgres_configured():
        if batch_size >= 50:
            batch_size = limits["batch_size"]
        if pass_mode == "fast" and workers >= 4:
            workers = limits["workers_fast"]
        elif pass_mode in {"slow", "full"} and workers >= 4:
            workers = limits["workers"]
    batch_pause_seconds = limits["batch_pause_seconds"]

    rules_version = classifier.get_rules_version()
    papers = _fetch_target_papers(
        db,
        pass_mode=pass_mode,
        only_heuristic=only_heuristic,
        maude_and_heuristic=maude_and_heuristic,
        tabs=tabs,
        scope_subnode=scope_subnode,
        limit=limit,
        skip_current_version=skip_current_version,
        rules_version=rules_version if skip_current_version else None,
        paper_ids=paper_ids,
    )

    if pass_mode == "slow":
        papers = [p for p in papers if paper_needs_slow_pass(p)]

    tier_skipped = 0
    if pass_mode == "fast":
        from classification_regression_guard import should_skip_fast_pass_for_tier

        kept: List[Dict[str, Any]] = []
        for paper in papers:
            if should_skip_fast_pass_for_tier(paper, rules_version):
                tier_skipped += 1
                continue
            kept.append(paper)
        papers = kept

    total = len(papers)
    scope_label = scope_subnode or (",".join(tabs) if tabs else "default")
    print(
        f"Starting Maude re-ingestion pass={pass_mode} scope={scope_label} for {total} papers "
        f"(dry_run={dry_run}, workers={workers}, limit={limit}) "
        f"at {datetime.now().isoformat()}"
    )

    field_change_counts: Counter = Counter()
    source_counts: Counter = Counter()
    stats = ReingestStats()
    stats.papers_skipped_tier_fast = tier_skipped
    start = time.time()

    if workers > 1:
        papers_changed, conn, cur = _run_parallel_reingest(
            db,
            papers,
            pass_mode=pass_mode,
            dry_run=dry_run,
            batch_size=batch_size,
            workers=workers,
            stats=stats,
            field_change_counts=field_change_counts,
            source_counts=source_counts,
            start=start,
            total=total,
            batch_pause_seconds=batch_pause_seconds,
            track_fields=track_fields,
        )
        papers_skipped = 0
    else:
        papers_changed, papers_skipped, conn, cur = _run_sequential_reingest(
            db,
            papers,
            pass_mode=pass_mode,
            dry_run=dry_run,
            batch_size=batch_size,
            rules_version=rules_version,
            skip_current_version=skip_current_version,
            stats=stats,
            field_change_counts=field_change_counts,
            source_counts=source_counts,
            start=start,
            total=total,
            batch_pause_seconds=batch_pause_seconds,
            track_fields=track_fields,
        )

    if not dry_run and conn is not None:
        conn.commit()
    if conn is not None:
        conn.close()

    elapsed = time.time() - start
    summary = {
        "pass_mode": pass_mode,
        "tabs": tabs,
        "papers_processed": total,
        "papers_skipped": papers_skipped,
        "papers_changed": papers_changed,
        "papers_written": stats.papers_written,
        "papers_skipped_noop": stats.papers_skipped_noop,
        "written_paper_ids": sorted(stats.written_paper_ids),
        "classify_seconds": round(stats.classify_seconds, 1),
        "db_write_seconds": round(stats.db_write_seconds, 1),
        "text_fetch_seconds": round(stats.text_fetch_seconds, 1),
        "cache_hits": stats.cache_hits,
        "cache_misses": stats.cache_misses,
        "elapsed_minutes": round(elapsed / 60, 1),
        "papers_per_second": round(total / elapsed, 2) if elapsed else 0,
        "source_counts": dict(source_counts),
        "field_change_counts": dict(field_change_counts),
        "pre_reingest_snapshots": {
            str(pid): snap for pid, snap in stats.pre_reingest_snapshots.items()
        },
        "track_fields": track_fields,
        "scope_subnode": scope_subnode,
        "dry_run": dry_run,
        "rules_version": rules_version,
        "workers": workers,
        "regression_merges": stats.regression_merges,
        "papers_skipped_tier_fast": stats.papers_skipped_tier_fast,
    }
    print(f"Maude re-ingestion complete: {summary}")
    return summary


def prewarm_slow_pass_cache(
    db: Optional[DatabaseManager] = None,
    *,
    limit: Optional[int] = None,
    workers: int = 4,
    skip_current_version: bool = True,
    tabs: Optional[List[str]] = None,
    paper_ids: Optional[List[int]] = None,
    scope_subnode: Optional[str] = None,
) -> dict:
    """Pre-fetch PDF/PMC text for slow-pass candidates into the disk cache."""
    db = db or DatabaseManager()
    rules_version = classifier.get_rules_version()
    papers = _fetch_target_papers(
        db,
        pass_mode="slow",
        only_heuristic=False,
        maude_and_heuristic=True,
        tabs=tabs,
        scope_subnode=scope_subnode,
        limit=limit,
        skip_current_version=skip_current_version,
        rules_version=rules_version if skip_current_version else None,
        paper_ids=paper_ids,
    )
    papers = [p for p in papers if paper_needs_slow_pass(p)]

    cached = skipped = failed = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                paper_text_cache.fetch_and_cache_paper,
                int(paper["id"]),
                full_text_link=paper.get("full_text_link"),
                pmid=paper.get("pmid"),
                doi=paper.get("doi"),
            ): paper
            for paper in papers
        }
        for future in as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:
                failed += 1
                logger.debug("Cache prewarm failed: %s", exc)
                continue
            status = outcome.get("status")
            if status == "cached":
                cached += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1

    summary = {
        "papers_requested": len(papers),
        "cached": cached,
        "skipped": skipped,
        "failed": failed,
        "elapsed_minutes": round((time.time() - start) / 60, 1),
    }
    print(f"Slow-pass cache prewarm complete: {summary}")
    return summary


def run_two_pass_reingest(
    dry_run: bool = False,
    batch_size: int = 50,
    limit: Optional[int] = None,
    fast_only: bool = False,
    slow_only: bool = False,
    workers: int = 4,
    workers_fast: Optional[int] = None,
    prewarm_cache: bool = True,
    refresh_maude_confidence: bool = True,
    refresh_maude_confidence_full: bool = False,
    skip_current_version: bool = True,
    tabs: Optional[List[str]] = None,
    paper_ids: Optional[List[int]] = None,
    scope_subnode: Optional[str] = None,
) -> dict:
    """Runs fast abstract-only pass then slow PDF/full-text pass on the non-LLM corpus.

    Args:
        dry_run: When True, compute changes without writing.
        batch_size: Commit interval for DB writes.
        limit: Optional cap per pass (for testing).
        fast_only: Run only the abstract-only pass.
        slow_only: Run only the PDF/full-text pass.
        workers: Parallel workers for the slow pass.
        workers_fast: Parallel workers for the fast pass (default env WORKERS_FAST or 4).
        prewarm_cache: When True, pre-fetch slow-pass PDF/PMC text between passes.
        refresh_maude_confidence: Refresh confidence scores after both passes.
        refresh_maude_confidence_full: Scan all maude-* papers for confidence refresh.
        skip_current_version: Skip papers already at the current rules version.

    Returns:
        Combined summary for all passes executed.
    """
    if workers_fast is None:
        workers_fast = production_reingest_limits()["workers_fast"]

    combined: Dict[str, Any] = {"passes": []}
    written_ids: Set[int] = set()
    db = DatabaseManager()

    if postgres_configured():
        healthy, detail = postgres_is_healthy()
        if not healthy:
            return {
                "error": "postgres_unavailable",
                "detail": detail,
                "passes": [],
            }

    limits = production_reingest_limits()
    if batch_size >= 50 and postgres_configured():
        batch_size = limits["batch_size"]
    if workers >= 4 and postgres_configured():
        workers = limits["workers"]

    if not slow_only:
        fast_summary = reingest_heuristic_papers(
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            maude_and_heuristic=True,
            pass_mode="fast",
            workers=max(1, workers_fast),
            skip_current_version=skip_current_version,
            tabs=tabs,
            paper_ids=paper_ids,
            scope_subnode=scope_subnode,
        )
        combined["passes"].append(fast_summary)
        written_ids.update(fast_summary.get("written_paper_ids") or [])

    if prewarm_cache and not fast_only and not dry_run:
        combined["cache_prewarm"] = prewarm_slow_pass_cache(
            db,
            limit=limit,
            workers=workers,
            skip_current_version=skip_current_version,
            tabs=tabs,
            paper_ids=paper_ids,
            scope_subnode=scope_subnode,
        )

    if not fast_only:
        slow_summary = reingest_heuristic_papers(
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            maude_and_heuristic=bool(tabs) or bool(paper_ids) or bool(scope_subnode) or True,
            pass_mode="slow",
            workers=workers,
            skip_current_version=skip_current_version,
            tabs=tabs,
            paper_ids=paper_ids,
            scope_subnode=scope_subnode,
        )
        combined["passes"].append(slow_summary)
        written_ids.update(slow_summary.get("written_paper_ids") or [])

    if refresh_maude_confidence and not dry_run:
        combined["confidence_refresh"] = refresh_maude_confidence_scores(
            batch_size=batch_size,
            paper_ids=None if refresh_maude_confidence_full else written_ids,
        )
    combined_field_changes: Counter = Counter()
    papers_processed = 0
    for pass_summary in combined["passes"]:
        combined_field_changes.update(pass_summary.get("field_change_counts") or {})
        papers_processed = max(papers_processed, int(pass_summary.get("papers_processed") or 0))
    combined["written_paper_ids"] = sorted(written_ids)
    combined["field_change_counts"] = dict(combined_field_changes)
    combined_snapshots: Dict[str, Any] = {}
    for pass_summary in combined["passes"]:
        combined_snapshots.update(pass_summary.get("pre_reingest_snapshots") or {})
    if combined_snapshots:
        combined["pre_reingest_snapshots"] = combined_snapshots
    combined["track_fields"] = track_fields_for_scope_subnode(scope_subnode)
    combined["scope_subnode"] = scope_subnode
    combined["papers_processed"] = papers_processed
    combined["papers_written"] = len(written_ids)
    return combined


def refresh_maude_confidence_scores(
    batch_size: int = 100,
    limit: Optional[int] = None,
    paper_ids: Optional[Set[int]] = None,
) -> dict:
    """Recomputes classification_confidence for maude-* papers from node alignment %."""
    db = DatabaseManager()
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    limit_sql = f" LIMIT {int(limit)}" if limit else ""

    if paper_ids:
        id_list = sorted(int(pid) for pid in paper_ids)
        if not id_list:
            conn.close()
            return {"papers_scanned": 0, "papers_updated": 0, "scoped": True}
        placeholders = ", ".join("?" for _ in id_list)
        cur.execute(
            f"""
            SELECT id, title, abstract, publication_type, study_type, ingestion_status,
                   classifier_version, classification_confidence
            FROM papers
            WHERE id IN ({placeholders})
              AND classifier_version LIKE 'maude-%'
            ORDER BY id
            {limit_sql}
            """,
            id_list,
        )
    else:
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
    summary = {
        "papers_scanned": len(papers),
        "papers_updated": updated,
        "scoped": paper_ids is not None,
    }
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
        "--workers-fast",
        type=int,
        default=None,
        help="Parallel workers for fast pass (default: WORKERS_FAST env or 4).",
    )
    parser.add_argument(
        "--no-prewarm-cache",
        action="store_true",
        help="Skip slow-pass disk cache prewarm between two-pass stages.",
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
        help="After re-ingestion, refresh classification_confidence on written maude-* papers.",
    )
    parser.add_argument(
        "--refresh-maude-confidence-full",
        action="store_true",
        help="Refresh classification_confidence on all maude-* papers (not just written IDs).",
    )
    parser.add_argument(
        "--paper-ids",
        default=None,
        help="Comma-separated paper ids to re-classify (scoped reingest).",
    )
    parser.add_argument(
        "--scope-subnode",
        default=None,
        choices=("node2a", "node2b", "node2c"),
        help="Re-classify all Maude/heuristic papers in this calibration subnode.",
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
    paper_id_list = None
    if args.paper_ids:
        paper_id_list = [int(pid.strip()) for pid in args.paper_ids.split(",") if pid.strip()]
    if args.pass_mode == "two-pass":
        summary = run_two_pass_reingest(
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            limit=args.limit,
            fast_only=args.fast_only,
            slow_only=args.slow_only,
            workers=args.workers,
            workers_fast=args.workers_fast,
            prewarm_cache=not args.no_prewarm_cache,
            refresh_maude_confidence=args.refresh_maude_confidence,
            refresh_maude_confidence_full=args.refresh_maude_confidence_full,
            skip_current_version=skip_current,
            tabs=tab_list,
            paper_ids=paper_id_list,
            scope_subnode=args.scope_subnode,
        )
        print(json.dumps(summary, indent=2, default=str))
        print("GOLDEN_REINGEST_SUMMARY=" + json.dumps(summary, default=str))
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
        paper_ids=paper_id_list,
        scope_subnode=args.scope_subnode,
    )
    if args.refresh_maude_confidence or summary.get("papers_written", 0) > 0:
        written_ids = set(summary.get("written_paper_ids") or [])
        refresh_maude_confidence_scores(
            batch_size=args.batch_size,
            paper_ids=None if args.refresh_maude_confidence_full else written_ids,
        )


if __name__ == "__main__":
    main()
