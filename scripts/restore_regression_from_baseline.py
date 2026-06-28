#!/usr/bin/env python3
"""Restore papers regressed by a full-subnode reingest from stored baselines."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import classification_regression_guard as guard
import patch_blast_radius
from db_manager import DatabaseManager
from local_sync import load_baseline_row, mark_papers_dirty
from reingest_heuristic_papers import TRACK_FIELDS, UPDATE_COLUMNS, _locked_fields, serialize

logger = logging.getLogger(__name__)

DEFAULT_BLAST_PATHS = (
    ROOT
    / "scratch/patch_reports/golden_b/node2b.animal_models_mouse.injection_cannabinoids_20260628_180346/blast_radius.json",
    ROOT
    / "scratch/patch_reports/golden_b/node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media_20260628_161206/blast_radius.json",
)

DEFAULT_CYCLE_REPORTS = (
    ROOT
    / "scratch/golden_dataset/cycles/node2b.animal_models_mouse.injection_cannabinoids/node2b.animal_models_mouse.injection_cannabinoids_20260628_180346/cycle_report.json",
    ROOT
    / "scratch/golden_dataset/cycles/node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media/node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media_20260628_161206/cycle_report.json",
)


def _load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON artifact from disk."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _written_paper_ids_from_cycle(cycle_report_path: Path) -> List[int]:
    """Return written paper ids recorded in a golden cycle reingest summary."""
    if not cycle_report_path.is_file():
        return []
    report = _load_json(cycle_report_path)
    reingest = (report.get("stages") or {}).get("reingest") or {}
    written = reingest.get("written_paper_ids") or []
    return sorted({int(pid) for pid in written})


def _paper_ids_from_blast(blast_path: Path) -> List[int]:
    """Collect paper ids from blast-radius top-changed entries and field samples."""
    payload = _load_json(blast_path)
    ids: Set[int] = set()
    for key in ("top_changed_papers", "top_changed_papers_prior_classification"):
        for entry in payload.get(key) or []:
            pid = entry.get("paper_id")
            if pid is not None:
                ids.add(int(pid))
    for samples in (payload.get("field_change_samples") or {}).values():
        for pid in samples or []:
            ids.add(int(pid))
    written = payload.get("written_paper_ids")
    if isinstance(written, list) and written:
        ids.update(int(pid) for pid in written)
    return sorted(ids)


def _load_pre_reingest_snapshots(cycle_report_path: Optional[Path]) -> Dict[int, Dict[str, Any]]:
    """Load pre-reingest snapshots from a golden cycle report when available."""
    if not cycle_report_path or not cycle_report_path.is_file():
        return {}
    report = _load_json(cycle_report_path)
    reingest = (report.get("stages") or {}).get("reingest") or {}
    raw = reingest.get("pre_reingest_snapshots") or {}
    return {int(pid): snap for pid, snap in raw.items()}


def _fetch_paper_row(conn: sqlite3.Connection, paper_id: int) -> Optional[Dict[str, Any]]:
    """Load one paper row as a dict."""
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _baseline_for_paper(
    conn: sqlite3.Connection,
    paper_id: int,
    *,
    pre_reingest_snapshots: Dict[int, Dict[str, Any]],
    track_fields: List[str],
) -> tuple[Dict[str, Any], str]:
    """Resolve the best before-state for one paper."""
    stored = load_baseline_row(conn, paper_id)
    pre = pre_reingest_snapshots.get(paper_id)
    baseline, source = patch_blast_radius.resolve_before_state(
        stored_baseline=stored,
        pre_reingest_snapshot=pre,
        postgres_snapshot=None,
        track_fields=track_fields,
    )
    return baseline, source


def find_regression_candidates(
    sqlite_path: str,
    blast_paths: List[Path],
    *,
    cycle_report_paths: Optional[List[Path]] = None,
) -> List[Dict[str, Any]]:
    """Return papers whose current SQLite row regressed vs baseline."""
    conn = sqlite3.connect(sqlite_path)
    candidates: List[Dict[str, Any]] = []
    seen: Set[int] = set()
    cycle_paths = list(cycle_report_paths or [])
    pre_snapshots: Dict[int, Dict[str, Any]] = {}
    for cycle_path in cycle_paths:
        pre_snapshots.update(_load_pre_reingest_snapshots(cycle_path))

    paper_ids: Set[int] = set()
    for cycle_path in cycle_paths:
        paper_ids.update(_written_paper_ids_from_cycle(cycle_path))
    for blast_path in blast_paths:
        if blast_path.is_file():
            paper_ids.update(_paper_ids_from_blast(blast_path))

    track_fields = list(TRACK_FIELDS)
    for blast_path in blast_paths:
        if blast_path.is_file():
            track_fields = list(_load_json(blast_path).get("track_fields") or track_fields)
            break

    for paper_id in sorted(paper_ids):
        if paper_id in seen:
            continue
        seen.add(paper_id)
        current = _fetch_paper_row(conn, paper_id)
        if not current:
            continue
        baseline, source = _baseline_for_paper(
            conn,
            paper_id,
            pre_reingest_snapshots=pre_snapshots,
            track_fields=track_fields,
        )
        blocked, reasons = guard.would_regress_classification(
            baseline,
            current,
            current.get("title") or "",
            current.get("abstract") or "",
        )
        if not blocked:
            continue
        candidates.append({
            "paper_id": paper_id,
            "baseline_source": source,
            "reasons": reasons,
            "prior_property_count": guard.count_extractable_properties(baseline),
            "current_property_count": guard.count_extractable_properties(current),
            "title": (current.get("title") or "")[:120],
        })
    conn.close()
    candidates.sort(
        key=lambda item: (
            item["prior_property_count"] - item["current_property_count"],
            item["paper_id"],
        ),
        reverse=True,
    )
    return candidates


def restore_candidates(
    sqlite_path: str,
    candidates: List[Dict[str, Any]],
    *,
    blast_paths: List[Path],
    cycle_report_paths: Optional[List[Path]] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Restore baseline classification fields for regression candidates."""
    pre_snapshots: Dict[int, Dict[str, Any]] = {}
    for cycle_path in cycle_report_paths or []:
        pre_snapshots.update(_load_pre_reingest_snapshots(cycle_path))

    track_fields = list(TRACK_FIELDS)
    for blast_path in blast_paths:
        if blast_path.is_file():
            track_fields = list(_load_json(blast_path).get("track_fields") or track_fields)
            break

    db = DatabaseManager()
    conn = db.get_connection()
    restored: List[Dict[str, Any]] = []

    for candidate in candidates:
        paper_id = int(candidate["paper_id"])
        current = _fetch_paper_row(conn, paper_id)
        if not current:
            continue
        baseline, source = _baseline_for_paper(
            conn,
            paper_id,
            pre_reingest_snapshots=pre_snapshots,
            track_fields=track_fields,
        )
        merged, meta = guard.merge_regression_safe(
            baseline,
            current,
            title=current.get("title") or "",
            abstract=current.get("abstract") or "",
        )
        locked = _locked_fields(current)
        set_parts: List[str] = []
        params: List[Any] = []
        changed_fields: List[str] = []
        for col in UPDATE_COLUMNS:
            if col in locked:
                continue
            if col == "classification_timestamp":
                continue
            if patch_blast_radius.norm(current.get(col)) == patch_blast_radius.norm(merged.get(col)):
                continue
            set_parts.append(f"{col} = ?")
            params.append(serialize(col, merged.get(col)))
            changed_fields.append(col)

        if not set_parts:
            continue

        entry = {
            "paper_id": paper_id,
            "baseline_source": source,
            "changed_fields": changed_fields,
            "merge_meta": meta,
        }
        restored.append(entry)

        if dry_run:
            continue

        params.append(paper_id)
        conn.execute(f"UPDATE papers SET {', '.join(set_parts)} WHERE id = ?", params)
        try:
            mark_papers_dirty(conn, [paper_id])
        except Exception:
            pass

    if not dry_run:
        conn.commit()
    conn.close()

    return {
        "dry_run": dry_run,
        "candidates": len(candidates),
        "restored_count": len(restored),
        "restored": restored[:100],
        "restored_paper_ids": sorted({int(item["paper_id"]) for item in restored}),
    }


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Restore regressed Maude classifications.")
    parser.add_argument(
        "--sqlite-path",
        default="cannabis_papers.db",
        help="Path to local SQLite database.",
    )
    parser.add_argument(
        "--blast-json",
        action="append",
        dest="blast_paths",
        help="Blast-radius JSON path (repeatable). Defaults to node2b + node2c golden reports.",
    )
    parser.add_argument(
        "--cycle-report",
        action="append",
        dest="cycle_reports",
        help="Optional cycle_report.json for pre-reingest snapshots.",
    )
    parser.add_argument("--apply", action="store_true", help="Write restores to SQLite.")
    parser.add_argument(
        "--report-path",
        help="Optional output JSON report path.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    blast_paths = [Path(p) for p in args.blast_paths] if args.blast_paths else list(DEFAULT_BLAST_PATHS)
    cycle_paths = [Path(p) for p in args.cycle_reports] if args.cycle_reports else list(DEFAULT_CYCLE_REPORTS)

    candidates = find_regression_candidates(
        args.sqlite_path,
        blast_paths,
        cycle_report_paths=cycle_paths,
    )
    logger.info("Found %s regression candidates", len(candidates))
    for item in candidates[:20]:
        logger.info(
            "  paper %s props %s→%s %s",
            item["paper_id"],
            item["prior_property_count"],
            item["current_property_count"],
            item["title"][:80],
        )

    result = restore_candidates(
        args.sqlite_path,
        candidates,
        blast_paths=blast_paths,
        cycle_report_paths=cycle_paths,
        dry_run=not args.apply,
    )
    result["candidates_preview"] = candidates[:20]
    result["generated_at"] = datetime.now(timezone.utc).isoformat()

    report_path = Path(args.report_path) if args.report_path else (
        ROOT / "scratch/patch_reports" / f"regression_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    logger.info("Wrote report: %s", report_path)
    print(json.dumps({"restored_count": result["restored_count"], "dry_run": result["dry_run"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
