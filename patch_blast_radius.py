"""Unified blast-radius reporting after Loop A/B/manual patch finish."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from calibration_build import MAUDE_CLASSIFIER_BUILD_ID
from local_sync import empty_baseline_snapshot, load_baseline_row, push_tracked_columns
from reingest_heuristic_papers import norm, parse_json_field, track_fields_for_scope_subnode

REPO_ROOT = Path(__file__).resolve().parent
REPORT_ROOT = REPO_ROOT / "scratch/patch_reports"
ROUTING_FIELDS = ("study_type", "exposure_method", "cannabis_type", "outcome_domain")
PRIOR_CLASSIFICATION_FIELDS = ROUTING_FIELDS
PRE_EXISTING_TOP_CHANGED_FIELDS: Tuple[str, ...] = (
    "inhaled_exposure_duration",
    "thc_pct",
    "cbd_pct",
    "thc_mg_ml",
    "cbd_mg_ml",
    "duration_days",
    "administration_frequency",
    "thc_mg_kg",
    "cbd_mg_kg",
    "thc_mg_g",
    "cbd_mg_g",
    "dose_mg",
    "population_sex",
    "population_gender",
    "sample_size",
)
TOP_CHANGED_PAPERS_LIMIT = 10
FIELD_SAMPLE_LIMIT = 25
PRE_EXISTING_TOP_CHANGED_HEADING = (
    "Top 10 most-updated papers (pre-existing classifications only — "
    "extractable dose / sample properties with prior measurements)"
)


@dataclass
class PatchFinishContext:
    """Inputs for a post-patch blast-radius report."""

    loop_type: str
    patch_id: str
    scope_subnode: Optional[str] = None
    endpoint_id: Optional[str] = None
    reingest_summary: Dict[str, Any] = field(default_factory=dict)
    push_summary: Optional[Dict[str, Any]] = None
    cohort_validation: Optional[Dict[str, Any]] = None
    maude_build_id: Optional[str] = None
    sqlite_path: Optional[str] = None
    use_postgres_before_fallback: bool = True


def local_report_file_url(path: Optional[str], *, repo_root: Path = REPO_ROOT) -> str:
    """Convert a repo-relative report path to a file:// URL for local HTML browsing."""
    if not path:
        return ""
    if path.startswith("file://"):
        return path
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = (repo_root / path).resolve()
    return resolved.as_uri()


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _papers_pushed_from_push_summary(push: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract pushed paper count from push summary dict or stdout string."""
    if not push:
        return None
    if push.get("papers_pushed") is not None:
        return int(push["papers_pushed"])
    if push.get("pushed") is not None:
        return int(push["pushed"])
    if push.get("delta_count") is not None:
        return int(push["delta_count"])
    raw = push.get("stdout_tail") or push.get("stdout") or ""
    if isinstance(raw, str):
        match = re.search(r"(\d+)\s+deltas?\s+applied", raw, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"applied\s+(\d+)\s+of\s+\d+\s+delta", raw, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def display_field_value(value: Any) -> str:
    """Format a classification field value for human-readable reports."""
    parsed = parse_json_field(value)
    if parsed is None or parsed == "":
        return "—"
    if isinstance(parsed, list):
        if not parsed:
            return "—"
        return ", ".join(str(item) for item in parsed)
    if isinstance(parsed, float):
        if parsed == int(parsed):
            return str(int(parsed))
        return f"{parsed:g}"
    return str(parsed)


def landing_url(paper: Dict[str, Any]) -> Optional[str]:
    """Return the primary article landing URL (L link): DOI preferred, else PubMed."""
    doi = str(paper.get("doi") or "").strip()
    if doi:
        if doi.startswith("http"):
            return doi
        return f"https://doi.org/{doi}"
    pmid = str(paper.get("pmid") or "").strip()
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return None


def pdf_url(paper: Dict[str, Any]) -> Optional[str]:
    """Return the PDF/full-text URL (P link) when available."""
    link = str(paper.get("full_text_link") or "").strip()
    return link or None


def link_tags_html(paper: Dict[str, Any]) -> str:
    """Render compact L/P link anchors for a paper row."""
    parts: List[str] = []
    land = landing_url(paper)
    pdf = pdf_url(paper)
    if land:
        parts.append(
            f'<a href="{escape(land)}" target="_blank" rel="noopener" title="Article URL">L</a>'
        )
    if pdf:
        parts.append(
            f'<a href="{escape(pdf)}" target="_blank" rel="noopener" title="PDF / full text">P</a>'
        )
    return " ".join(parts) if parts else "—"


def link_tags_markdown(paper: Dict[str, Any]) -> str:
    """Render L/P markdown links for a paper row."""
    parts: List[str] = []
    land = landing_url(paper)
    pdf = pdf_url(paper)
    if land:
        parts.append(f"[L]({land})")
    if pdf:
        parts.append(f"[P]({pdf})")
    return " ".join(parts) if parts else "—"


def _fetch_paper_row(conn: sqlite3.Connection, paper_id: int) -> Optional[Dict[str, Any]]:
    """Load one paper row from SQLite as a plain dict."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM papers WHERE id = ?", (int(paper_id),))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def had_prior_classification(baseline: Optional[Dict[str, Any]]) -> bool:
    """
    Return True when the before-state already had routing/classification values.

    Papers without a baseline row, pre-reingest snapshot, or Postgres fallback values
    are treated as first-time classifications.
    """
    if not baseline:
        return False
    for field_name in PRIOR_CLASSIFICATION_FIELDS:
        parsed = parse_json_field(baseline.get(field_name))
        if parsed is None or parsed == "" or parsed == []:
            continue
        return True
    return False


def fetch_postgres_before_snapshots(
    paper_ids: List[int],
    track_fields: List[str],
) -> Dict[int, Dict[str, Any]]:
    """
    Fetch production Postgres classification values for papers missing local baselines.

    Used when reingest ran without a prior pull baseline but production still holds the
    pre-patch classification (common when push was skipped).
    """
    if not paper_ids or not os.getenv("DATABASE_URL"):
        return {}

    from db_manager import DatabaseManager

    db = DatabaseManager()
    if not db.is_postgres:
        return {}

    columns = list(dict.fromkeys(["id"] + list(track_fields) + list(push_tracked_columns())))
    columns = [col for col in columns if col]
    col_sql = ", ".join(columns)
    snapshots: Dict[int, Dict[str, Any]] = {}
    conn = db.get_connection()
    try:
        for batch_start in range(0, len(paper_ids), 500):
            batch = paper_ids[batch_start:batch_start + 500]
            placeholders = ", ".join("%s" for _ in batch)
            cur = conn.cursor()
            cur.execute(
                f"SELECT {col_sql} FROM papers WHERE id IN ({placeholders}) ORDER BY id",
                tuple(int(pid) for pid in batch),
            )
            rows = cur.fetchall()
            if not rows:
                continue
            if hasattr(rows[0], "keys"):
                for row in rows:
                    row_dict = dict(row)
                    snapshots[int(row_dict["id"])] = row_dict
            else:
                for row in rows:
                    row_dict = {columns[idx]: row[idx] for idx in range(len(columns))}
                    snapshots[int(row_dict["id"])] = row_dict
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return snapshots


def resolve_before_state(
    *,
    stored_baseline: Optional[Dict[str, Any]],
    pre_reingest_snapshot: Optional[Dict[str, Any]],
    postgres_snapshot: Optional[Dict[str, Any]],
    track_fields: List[str],
) -> Tuple[Dict[str, Any], str]:
    """
    Choose the best available before-state for blast-radius diffs.

    Priority: sync baseline → reingest pre-write snapshot → production Postgres.
    """
    if stored_baseline is not None:
        return stored_baseline, "sync_baseline"
    if pre_reingest_snapshot:
        return pre_reingest_snapshot, "pre_reingest_snapshot"
    if postgres_snapshot:
        return postgres_snapshot, "postgres"
    merged = empty_baseline_snapshot()
    for field_name in track_fields:
        merged[field_name] = None
    return merged, "none"


def is_measured_property_value(value: Any) -> bool:
    """Return True when a field value represents an extracted measurement (not null/unmeasured)."""
    parsed = parse_json_field(value)
    if parsed is None:
        return False
    if isinstance(parsed, str):
        lowered = parsed.strip().lower()
        return lowered not in {"", "—", "-", "unknown", "unspecified", "null", "n/a", "not reported"}
    if isinstance(parsed, list):
        if not parsed:
            return False
        return any(
            str(item).strip().lower() not in {"", "unknown", "unspecified"}
            for item in parsed
        )
    return True


def is_property_update(before_raw: Any, after_raw: Any) -> bool:
    """Return True when a measured property changed to a different measured value."""
    return (
        is_measured_property_value(before_raw)
        and is_measured_property_value(after_raw)
        and norm(before_raw) != norm(after_raw)
    )


def _fields_changed_count(field_diffs: List[Dict[str, Any]], fields: Sequence[str]) -> int:
    """Count changed diffs limited to the supplied field names."""
    allowed = set(fields)
    return sum(
        1
        for diff in field_diffs
        if diff.get("changed") and diff.get("field") in allowed
    )


def _property_updates_count(field_diffs: List[Dict[str, Any]], fields: Sequence[str]) -> int:
    """Count property updates (both sides measured) within the supplied field names."""
    allowed = set(fields)
    return sum(
        1
        for diff in field_diffs
        if diff.get("field") in allowed and diff.get("property_updated")
    )


def _subset_paper_summary(
    summary: Dict[str, Any],
    count_fields: Sequence[str],
    *,
    updates_only: bool = False,
) -> Dict[str, Any]:
    """Return a copy of a paper summary ranked/displayed on a field subset only."""
    allowed = set(count_fields)
    field_diffs = [
        diff for diff in (summary.get("field_diffs") or []) if diff.get("field") in allowed
    ]
    if updates_only:
        field_diffs = [diff for diff in field_diffs if diff.get("property_updated")]
    if updates_only:
        fields_changed = len(field_diffs)
    else:
        fields_changed = _fields_changed_count(summary.get("field_diffs") or [], count_fields)
    return {
        **summary,
        "fields_changed": fields_changed,
        "field_diffs": field_diffs,
        "change_count_fields": list(count_fields),
        "updates_only": updates_only,
    }


def _summarize_paper_changes(
    *,
    paper_id: int,
    current: Dict[str, Any],
    before_row: Dict[str, Any],
    before_source: str,
    track_fields: List[str],
    field_changed_counts: Dict[str, int],
    field_samples: Dict[str, List[int]],
    sample_limit_per_field: int,
    diff_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build one paper's before/after diff summary."""
    locked = set(parse_json_field(current.get("expert_locked_fields")) or [])
    compare_fields = list(dict.fromkeys(diff_fields or track_fields))
    field_diffs: List[Dict[str, Any]] = []
    change_count = 0
    for field_name in compare_fields:
        if field_name in locked:
            continue
        before_raw = before_row.get(field_name, current.get(field_name))
        after_raw = current.get(field_name)
        changed = norm(before_raw) != norm(after_raw)
        if changed and field_name in track_fields:
            change_count += 1
            field_changed_counts[field_name] = field_changed_counts.get(field_name, 0) + 1
            samples = field_samples.setdefault(field_name, [])
            if len(samples) < sample_limit_per_field:
                samples.append(paper_id)
        field_diffs.append({
            "field": field_name,
            "before": display_field_value(before_raw),
            "after": display_field_value(after_raw),
            "changed": changed,
            "property_updated": is_property_update(before_raw, after_raw),
        })
    return {
        "paper_id": paper_id,
        "title": (current.get("title") or "")[:160],
        "pmid": current.get("pmid"),
        "doi": current.get("doi"),
        "full_text_link": current.get("full_text_link"),
        "landing_url": landing_url(current),
        "pdf_url": pdf_url(current),
        "fields_changed": change_count,
        "before_source": before_source,
        "had_prior_classification": had_prior_classification(before_row),
        "field_diffs": field_diffs,
    }


def analyze_reingest_changes(
    sqlite_path: str,
    paper_ids: List[int],
    track_fields: List[str],
    *,
    papers_scanned: int,
    pre_reingest_snapshots: Optional[Dict[Any, Dict[str, Any]]] = None,
    use_postgres_before_fallback: bool = True,
    top_n: int = TOP_CHANGED_PAPERS_LIMIT,
    sample_limit_per_field: int = FIELD_SAMPLE_LIMIT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """
    Compare before-states to current SQLite rows for all written paper ids.

    Returns (top_changed_papers, top_changed_papers_prior_classification, field_detail_rows).
    """
    if not paper_ids or not Path(sqlite_path).is_file():
        return [], [], [], {}

    snapshot_map: Dict[int, Dict[str, Any]] = {}
    for raw_id, snapshot in (pre_reingest_snapshots or {}).items():
        snapshot_map[int(raw_id)] = snapshot

    conn = sqlite3.connect(sqlite_path)
    missing_before_ids: List[int] = []
    for paper_id in sorted({int(pid) for pid in paper_ids}):
        if load_baseline_row(conn, paper_id) is not None:
            continue
        if paper_id in snapshot_map:
            continue
        missing_before_ids.append(paper_id)
    conn.close()

    postgres_snapshots: Dict[int, Dict[str, Any]] = {}
    if use_postgres_before_fallback and missing_before_ids:
        postgres_snapshots = fetch_postgres_before_snapshots(missing_before_ids, track_fields)

    conn = sqlite3.connect(sqlite_path)
    field_changed_counts: Dict[str, int] = {name: 0 for name in track_fields}
    field_samples: Dict[str, List[int]] = {name: [] for name in track_fields}
    paper_summaries: List[Dict[str, Any]] = []
    before_source_counts: Dict[str, int] = {}
    diff_fields = list(dict.fromkeys(list(track_fields) + list(PRE_EXISTING_TOP_CHANGED_FIELDS)))

    for paper_id in sorted({int(pid) for pid in paper_ids}):
        current = _fetch_paper_row(conn, paper_id)
        if not current:
            continue
        stored_baseline = load_baseline_row(conn, paper_id)
        before_row, before_source = resolve_before_state(
            stored_baseline=stored_baseline,
            pre_reingest_snapshot=snapshot_map.get(paper_id),
            postgres_snapshot=postgres_snapshots.get(paper_id),
            track_fields=track_fields,
        )
        before_source_counts[before_source] = before_source_counts.get(before_source, 0) + 1
        paper_summaries.append(
            _summarize_paper_changes(
                paper_id=paper_id,
                current=current,
                before_row=before_row,
                before_source=before_source,
                track_fields=track_fields,
                field_changed_counts=field_changed_counts,
                field_samples=field_samples,
                sample_limit_per_field=sample_limit_per_field,
                diff_fields=diff_fields,
            )
        )

    conn.close()
    paper_summaries.sort(key=lambda row: (-row["fields_changed"], row["paper_id"]))
    top_papers = paper_summaries[:top_n]
    prior_summaries = [
        _subset_paper_summary(
            row,
            PRE_EXISTING_TOP_CHANGED_FIELDS,
            updates_only=True,
        )
        for row in paper_summaries
        if row.get("had_prior_classification")
        and _property_updates_count(
            row.get("field_diffs") or [],
            PRE_EXISTING_TOP_CHANGED_FIELDS,
        ) > 0
    ]
    prior_summaries.sort(key=lambda row: (-row["fields_changed"], row["paper_id"]))
    top_prior_papers = prior_summaries[:top_n]

    scanned = max(papers_scanned, len(paper_ids), 1)
    detail_rows: List[Dict[str, Any]] = []
    for field_name in track_fields:
        changed = int(field_changed_counts.get(field_name, 0))
        unchanged = max(scanned - changed, 0)
        pct = round(100.0 * changed / scanned, 2) if scanned else 0.0
        detail_rows.append({
            "field": field_name,
            "papers_changed": changed,
            "papers_unchanged": unchanged,
            "pct_of_scanned": pct,
            "sample_paper_ids": field_samples.get(field_name, []),
        })
    detail_rows.sort(key=lambda row: (-row["papers_changed"], row["field"]))
    return top_papers, top_prior_papers, detail_rows, before_source_counts


def normalize_blast_radius_payload(ctx: PatchFinishContext) -> Dict[str, Any]:
    """Build normalized blast-radius JSON from finish context."""
    summary = ctx.reingest_summary or {}
    papers_scanned = int(
        summary.get("papers_processed")
        or summary.get("papers_scanned")
        or 0
    )
    papers_written = int(
        summary.get("papers_written")
        or len(summary.get("written_paper_ids") or [])
        or 0
    )
    papers_changed = int(summary.get("papers_changed") or papers_written)
    papers_pushed = _papers_pushed_from_push_summary(ctx.push_summary)
    track_fields = summary.get("track_fields") or track_fields_for_scope_subnode(
        ctx.scope_subnode or summary.get("scope_subnode")
    )

    payload: Dict[str, Any] = {
        "loop_type": ctx.loop_type,
        "patch_id": ctx.patch_id,
        "endpoint_id": ctx.endpoint_id,
        "scope_subnode": ctx.scope_subnode or summary.get("scope_subnode"),
        "papers_scanned": papers_scanned,
        "papers_changed": papers_changed,
        "papers_written": papers_written,
        "papers_pushed": papers_pushed,
        "field_change_counts": dict(summary.get("field_change_counts") or {}),
        "track_fields": track_fields,
        "full_subnode": bool(summary.get("full_subnode", True)),
        "maude_build_id": ctx.maude_build_id or MAUDE_CLASSIFIER_BUILD_ID,
        "cohort_validation": ctx.cohort_validation,
        "generated_at": utc_now_iso(),
    }

    written_ids = list(summary.get("written_paper_ids") or [])
    sqlite_path = ctx.sqlite_path
    pre_reingest_snapshots = summary.get("pre_reingest_snapshots")
    if sqlite_path and written_ids:
        top_papers, top_prior_papers, field_details, before_source_counts = analyze_reingest_changes(
            sqlite_path,
            written_ids,
            track_fields,
            papers_scanned=papers_scanned,
            pre_reingest_snapshots=pre_reingest_snapshots,
            use_postgres_before_fallback=ctx.use_postgres_before_fallback,
        )
        if before_source_counts:
            payload["before_source_counts"] = before_source_counts
        if field_details:
            payload["field_details"] = field_details
            payload["field_change_counts"] = {
                row["field"]: row["papers_changed"] for row in field_details
            }
        if top_papers:
            payload["top_changed_papers"] = top_papers
        if top_prior_papers:
            payload["top_changed_papers_prior_classification"] = top_prior_papers

    return payload


def build_field_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return sorted table rows for all tracked characteristics."""
    if payload.get("field_details"):
        return list(payload["field_details"])

    scope = payload.get("scope_subnode")
    track_fields = payload.get("track_fields") or track_fields_for_scope_subnode(scope)
    changes: Dict[str, int] = dict(payload.get("field_change_counts") or {})
    scanned = int(payload.get("papers_scanned") or 0) or 1
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for field_name in track_fields:
        seen.add(field_name)
        changed = int(changes.get(field_name, 0))
        rows.append({
            "field": field_name,
            "papers_changed": changed,
            "papers_unchanged": max(scanned - changed, 0),
            "pct_of_scanned": round(100.0 * changed / scanned, 2),
            "sample_paper_ids": [],
        })
    for field_name, count in sorted(changes.items()):
        if field_name not in seen:
            changed = int(count)
            rows.append({
                "field": field_name,
                "papers_changed": changed,
                "papers_unchanged": max(scanned - changed, 0),
                "pct_of_scanned": round(100.0 * changed / scanned, 2),
                "sample_paper_ids": [],
            })
    rows.sort(key=lambda row: (-row["papers_changed"], row["field"]))
    return rows


def _render_top_papers_html(
    top_papers: List[Dict[str, Any]],
    *,
    heading: str,
    anchor_prefix: str = "paper",
) -> str:
    """Render before/after tables for the top most-changed papers."""
    if not top_papers:
        return ""
    blocks: List[str] = [f"<h2>{escape(heading)}</h2>"]
    for paper in top_papers:
        paper_id = paper.get("paper_id")
        title = escape(paper.get("title") or f"Paper {paper_id}")
        links = link_tags_html(paper)
        count_label = (
            "properties updated"
            if paper.get("updates_only")
            else "fields changed"
        )
        blocks.append(
            f'<h3 id="{anchor_prefix}-{paper_id}">'
            f'Paper {paper_id} · {paper.get("fields_changed")} {count_label} · {links}</h3>'
            f'<p class="paper-title">{title}</p>'
        )
        rows = []
        for diff in paper.get("field_diffs") or []:
            css = ' class="changed"' if diff.get("changed") else ""
            rows.append(
                f"<tr{css}><td>{escape(str(diff.get('field')))}</td>"
                f"<td>{escape(str(diff.get('before')))}</td>"
                f"<td>{escape(str(diff.get('after')))}</td></tr>"
            )
        blocks.append(
            "<table><thead><tr><th>Characteristic</th><th>Before</th><th>After</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return "\n".join(blocks)


def _render_top_papers_markdown(
    top_papers: List[Dict[str, Any]],
    *,
    heading: str,
) -> str:
    """Render before/after markdown for the top most-changed papers."""
    if not top_papers:
        return ""
    lines = [f"## {heading}", ""]
    for paper in top_papers:
        links = link_tags_markdown(paper)
        count_label = (
            "properties updated"
            if paper.get("updates_only")
            else "fields changed"
        )
        lines.append(
            f"### Paper {paper.get('paper_id')} ({paper.get('fields_changed')} {count_label}) {links}"
        )
        lines.append("")
        lines.append(f"*{paper.get('title') or ''}*")
        lines.append("")
        lines.append("| Characteristic | Before | After |")
        lines.append("|---|---|---|")
        for diff in paper.get("field_diffs") or []:
            before = str(diff.get("before") or "—").replace("|", "\\|")
            after = str(diff.get("after") or "—").replace("|", "\\|")
            lines.append(f"| `{diff.get('field')}` | {before} | {after} |")
        lines.append("")
    return "\n".join(lines)


def _render_top_paper_sections_html(payload: Dict[str, Any]) -> str:
    """Render both top-10 paper sections for HTML output."""
    sections = [
        _render_top_papers_html(
            payload.get("top_changed_papers") or [],
            heading="Top 10 most-changed papers (all)",
            anchor_prefix="paper",
        ),
        _render_top_papers_html(
            payload.get("top_changed_papers_prior_classification") or [],
            heading=PRE_EXISTING_TOP_CHANGED_HEADING,
            anchor_prefix="paper-prior",
        ),
    ]
    return "\n".join(section for section in sections if section)


def _render_top_paper_sections_markdown(payload: Dict[str, Any]) -> str:
    """Render both top-10 paper sections for Markdown output."""
    sections = [
        _render_top_papers_markdown(
            payload.get("top_changed_papers") or [],
            heading="Top 10 most-changed papers (all)",
        ),
        _render_top_papers_markdown(
            payload.get("top_changed_papers_prior_classification") or [],
            heading=PRE_EXISTING_TOP_CHANGED_HEADING,
        ),
    ]
    return "\n".join(section for section in sections if section)


def render_markdown(
    *,
    title: str,
    payload: Dict[str, Any],
    rows: List[Dict[str, Any]],
    cohort_path: Optional[str] = None,
) -> str:
    """Format blast-radius markdown report."""
    changed_fields = sum(1 for row in rows if row["papers_changed"] > 0)
    total_field_changes = sum(row["papers_changed"] for row in rows)
    cohort = payload.get("cohort_validation") or {}
    lines = [
        f"# Blast-radius report — {title}",
        "",
        f"- **Loop type:** `{payload.get('loop_type')}`",
        f"- **Patch id:** `{payload.get('patch_id')}`",
        f"- **Endpoint:** `{payload.get('endpoint_id') or '—'}`",
        f"- **Scope subnode:** `{payload.get('scope_subnode') or '—'}`",
        f"- **Maude build:** `{payload.get('maude_build_id')}`",
        f"- **Papers scanned:** {payload.get('papers_scanned')}",
        f"- **Papers changed:** {payload.get('papers_changed')}",
        f"- **Papers written:** {payload.get('papers_written')}",
        f"- **Papers pushed:** {payload.get('papers_pushed') if payload.get('papers_pushed') is not None else '—'}",
        f"- **Characteristics with ≥1 change:** {changed_fields} / {len(rows)}",
        f"- **Total field-level changes:** {total_field_changes}",
        f"- **Generated:** {payload.get('generated_at')}",
        "",
    ]
    if cohort:
        lines.extend([
            "## Similarity cohort validation",
            "",
            f"- **Cohort pool size:** {cohort.get('cohort_pool_size', '—')}",
            f"- **Routing match before:** {cohort.get('cohort_routing_match_before', '—')}",
            f"- **Routing match after:** {cohort.get('cohort_routing_match_after', '—')}",
            f"- **Cohort routing changed:** {cohort.get('cohort_papers_routing_changed', '—')}",
            f"- **Subnode routing changed:** {cohort.get('subnode_papers_routing_changed', '—')}",
            "",
        ])
        if cohort_path:
            lines.append(f"[Cohort detail HTML]({cohort_path})")
            lines.append("")
    lines.extend([
        "## Per-field changes (all characteristics)",
        "",
        "| Characteristic | Changed | Unchanged | % scanned | Sample paper IDs |",
        "|---|---:|---:|---:|---|",
    ])
    for row in rows:
        samples = row.get("sample_paper_ids") or []
        sample_txt = ", ".join(str(pid) for pid in samples[:10])
        if len(samples) > 10:
            sample_txt += f" (+{len(samples) - 10} more)"
        if not sample_txt:
            sample_txt = "—"
        lines.append(
            f"| `{row['field']}` | {row['papers_changed']} | {row.get('papers_unchanged', '—')} "
            f"| {row.get('pct_of_scanned', '—')} | {sample_txt} |"
        )
    lines.append("")
    lines.append(_render_top_paper_sections_markdown(payload))
    return "\n".join(lines) + "\n"


def render_html(
    *,
    title: str,
    payload: Dict[str, Any],
    rows: List[Dict[str, Any]],
    cohort_html_rel: Optional[str] = None,
) -> str:
    """Format blast-radius HTML report."""
    changed_fields = sum(1 for row in rows if row["papers_changed"] > 0)
    cohort = payload.get("cohort_validation") or {}
    body_rows = []
    for row in rows:
        css = ' class="changed"' if row["papers_changed"] > 0 else ""
        samples = row.get("sample_paper_ids") or []
        sample_links = []
        for pid in samples[:10]:
            sample_links.append(f'<a href="#paper-{pid}">{pid}</a>')
        sample_txt = ", ".join(sample_links) if sample_links else "—"
        body_rows.append(
            f"<tr{css}><td>{escape(row['field'])}</td>"
            f"<td>{row['papers_changed']}</td>"
            f"<td>{row.get('papers_unchanged', '—')}</td>"
            f"<td>{row.get('pct_of_scanned', '—')}</td>"
            f"<td>{sample_txt}</td></tr>"
        )
    cohort_block = ""
    if cohort:
        cohort_link = ""
        if cohort_html_rel:
            cohort_link = f' <a href="{escape(cohort_html_rel)}">detail</a>'
        cohort_block = (
            "<h2>Similarity cohort validation</h2>"
            "<ul>"
            f"<li>Cohort pool size: {cohort.get('cohort_pool_size', '—')}</li>"
            f"<li>Routing match before: {cohort.get('cohort_routing_match_before', '—')}</li>"
            f"<li>Routing match after: {cohort.get('cohort_routing_match_after', '—')}</li>"
            f"<li>Cohort routing changed: {cohort.get('cohort_papers_routing_changed', '—')}</li>"
            f"<li>Subnode routing changed: {cohort.get('subnode_papers_routing_changed', '—')}</li>"
            f"</ul>{cohort_link}"
        )
    pushed = payload.get("papers_pushed")
    pushed_txt = str(pushed) if pushed is not None else "—"
    top_papers_block = _render_top_paper_sections_html(payload)
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Blast radius — {escape(title)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px;line-height:1.45}"
        "table{border-collapse:collapse;width:100%;margin-bottom:24px;font-size:0.92rem}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}"
        "th{background:#f4f4f4}tr.changed td{background:#fff8e6}"
        ".meta{color:#555;margin-bottom:16px}"
        ".paper-title{color:#444;margin:-8px 0 12px 0;font-size:0.95rem}"
        "h3{margin-top:28px;margin-bottom:4px;font-size:1.05rem}"
        "h3 a{margin-left:8px;font-size:0.85rem}"
        "</style></head><body>"
        f"<h1>Blast-radius report</h1>"
        f"<p class=\"meta\">"
        f"<strong>Title:</strong> {escape(title)}<br>"
        f"<strong>Loop:</strong> {escape(str(payload.get('loop_type')))}<br>"
        f"<strong>Patch id:</strong> {escape(str(payload.get('patch_id')))}<br>"
        f"<strong>Endpoint:</strong> {escape(str(payload.get('endpoint_id') or '—'))}<br>"
        f"<strong>Scope:</strong> {escape(str(payload.get('scope_subnode') or '—'))}<br>"
        f"<strong>Maude build:</strong> {escape(str(payload.get('maude_build_id')))}<br>"
        f"<strong>Papers scanned:</strong> {payload.get('papers_scanned')}<br>"
        f"<strong>Papers changed:</strong> {payload.get('papers_changed')}<br>"
        f"<strong>Papers written:</strong> {payload.get('papers_written')}<br>"
        f"<strong>Papers pushed:</strong> {pushed_txt}<br>"
        f"<strong>Characteristics changed:</strong> {changed_fields} / {len(rows)}"
        f"</p>"
        f"{cohort_block}"
        "<h2>Per-field changes (all characteristics)</h2>"
        "<table><thead><tr>"
        "<th>Characteristic</th><th>Changed</th><th>Unchanged</th><th>% scanned</th><th>Sample IDs</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        f"{top_papers_block}"
        "</body></html>"
    )


def write_blast_radius_reports(ctx: PatchFinishContext) -> Dict[str, str]:
    """Write JSON, Markdown, and HTML blast-radius reports; return paths."""
    payload = normalize_blast_radius_payload(ctx)
    rows = build_field_rows(payload)
    report_dir = REPORT_ROOT / ctx.loop_type / ctx.patch_id
    report_dir.mkdir(parents=True, exist_ok=True)

    title = ctx.endpoint_id or ctx.scope_subnode or ctx.patch_id
    json_path = report_dir / "blast_radius.json"
    md_path = report_dir / "blast_radius.md"
    html_path = report_dir / "blast_radius.html"
    cohort_html = report_dir / "cohort_validation.html"

    cohort_rel = "cohort_validation.html" if ctx.cohort_validation and cohort_html.is_file() else None
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        render_markdown(title=title, payload=payload, rows=rows, cohort_path=cohort_rel),
        encoding="utf-8",
    )
    html_path.write_text(
        render_html(title=title, payload=payload, rows=rows, cohort_html_rel=cohort_rel),
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "html": str(html_path),
    }


def run_finish_reporting(
    *,
    loop_type: str,
    patch_id: str,
    reingest_summary: Dict[str, Any],
    scope_subnode: Optional[str] = None,
    endpoint_id: Optional[str] = None,
    push_summary: Optional[Dict[str, Any]] = None,
    sqlite_path: str = "cannabis_papers.db",
    run_cohort: bool = True,
) -> Dict[str, Any]:
    """
    Run cohort validation (when endpoint_id set) and write blast-radius reports.

    Returns normalized blast payload plus report paths.
    """
    import similarity_cohort_validation

    cohort_payload: Optional[Dict[str, Any]] = None
    if run_cohort and endpoint_id:
        cohort_payload = similarity_cohort_validation.run_and_write(
            endpoint_id,
            loop_type=loop_type,
            patch_id=patch_id,
            sqlite_path=sqlite_path,
            scope_subnode=scope_subnode,
        )

    ctx = PatchFinishContext(
        loop_type=loop_type,
        patch_id=patch_id,
        scope_subnode=scope_subnode or reingest_summary.get("scope_subnode"),
        endpoint_id=endpoint_id,
        reingest_summary=reingest_summary,
        push_summary=push_summary,
        cohort_validation=cohort_payload,
        sqlite_path=sqlite_path,
    )
    report_paths = write_blast_radius_reports(ctx)
    payload = normalize_blast_radius_payload(ctx)
    payload["report_paths"] = {
        key: local_report_file_url(path) if key == "html" else path
        for key, path in report_paths.items()
    }
    payload["report_paths"]["html_relative"] = report_paths.get("html")
    if cohort_payload and cohort_payload.get("report_paths"):
        payload["cohort_report_paths"] = cohort_payload["report_paths"]
    return payload


def load_reingest_from_artifact(artifact_dir: Path) -> Dict[str, Any]:
    """Load reingest stage summary from a golden cycle artifact directory."""
    cycle_report = artifact_dir / "cycle_report.json"
    if not cycle_report.is_file():
        return {}
    with open(cycle_report, encoding="utf-8") as handle:
        report = json.load(handle)
    reingest = (report.get("stages") or {}).get("reingest") or {}
    if reingest.get("field_change_counts") or reingest.get("written_paper_ids"):
        return reingest
    stdout_tail = reingest.get("stdout_tail") or ""
    if "GOLDEN_REINGEST_SUMMARY=" in stdout_tail:
        for line in stdout_tail.splitlines():
            if line.startswith("GOLDEN_REINGEST_SUMMARY="):
                try:
                    return json.loads(line.split("=", 1)[1])
                except json.JSONDecodeError:
                    pass
    for line in stdout_tail.splitlines():
        if line.strip().startswith("{") and "written_paper_ids" in line:
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    if stdout_tail.strip().startswith("{"):
        try:
            return json.loads(stdout_tail)
        except json.JSONDecodeError:
            pass
    return reingest


def regenerate_reports_from_artifact(
    *,
    artifact_dir: Path,
    loop_type: str,
    patch_id: str,
    endpoint_id: Optional[str] = None,
    scope_subnode: Optional[str] = None,
    sqlite_path: str = "cannabis_papers.db",
    cohort_json: Optional[Path] = None,
) -> Dict[str, str]:
    """Rebuild blast-radius HTML/MD/JSON from an existing cycle artifact."""
    reingest = load_reingest_from_artifact(artifact_dir)
    cohort: Optional[Dict[str, Any]] = None
    if cohort_json and cohort_json.is_file():
        cohort = json.loads(cohort_json.read_text(encoding="utf-8"))
    ctx = PatchFinishContext(
        loop_type=loop_type,
        patch_id=patch_id,
        scope_subnode=scope_subnode,
        endpoint_id=endpoint_id,
        reingest_summary=reingest,
        cohort_validation=cohort,
        sqlite_path=sqlite_path,
    )
    return write_blast_radius_reports(ctx)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Write blast-radius report after patch finish.")
    parser.add_argument("--loop-type", required=True, choices=["golden_b", "calibration_a", "manual_edit"])
    parser.add_argument("--patch-id", required=True)
    parser.add_argument("--scope-subnode", default=None)
    parser.add_argument("--endpoint-id", default=None)
    parser.add_argument("--sqlite-path", default="cannabis_papers.db")
    parser.add_argument("--no-postgres-before", action="store_true")
    parser.add_argument("--artifact-dir", default=None, help="Golden cycle artifact dir with cycle_report.json")
    parser.add_argument("--reingest-json", default=None, help="Explicit reingest summary JSON path")
    parser.add_argument("--push-json", default=None, help="Push summary JSON path")
    parser.add_argument("--cohort-json", default=None, help="Cohort validation JSON path")
    return parser


def main() -> None:
    """CLI entrypoint."""
    args = build_parser().parse_args()
    reingest: Dict[str, Any] = {}
    if args.reingest_json:
        with open(args.reingest_json, encoding="utf-8") as handle:
            reingest = json.load(handle)
    elif args.artifact_dir:
        reingest = load_reingest_from_artifact(Path(args.artifact_dir))

    push_summary: Optional[Dict[str, Any]] = None
    if args.push_json:
        with open(args.push_json, encoding="utf-8") as handle:
            push_summary = json.load(handle)

    cohort: Optional[Dict[str, Any]] = None
    cohort_path = Path(args.cohort_json) if args.cohort_json else None
    if cohort_path and cohort_path.is_file():
        with open(cohort_path, encoding="utf-8") as handle:
            cohort = json.load(handle)

    ctx = PatchFinishContext(
        loop_type=args.loop_type,
        patch_id=args.patch_id,
        scope_subnode=args.scope_subnode,
        endpoint_id=args.endpoint_id,
        reingest_summary=reingest,
        push_summary=push_summary,
        cohort_validation=cohort,
        sqlite_path=args.sqlite_path,
        use_postgres_before_fallback=not args.no_postgres_before,
    )
    paths = write_blast_radius_reports(ctx)
    print(json.dumps({"patch_id": args.patch_id, "reports": paths}, indent=2))


if __name__ == "__main__":
    main()
