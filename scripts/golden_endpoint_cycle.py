#!/usr/bin/env python3
"""Per-endpoint golden RL cycle: pull → LLM → promote → patch guard → reingest → push."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibration_feedback_agent as cfa
import calibration_pdf
import classification_schema
import content_tiers
import golden_confirmed_store
import golden_dataset_paths
import subnode_field_scopes
from golden_endpoint_status import patch_from_cycle_report, update_endpoint_status
from golden_dataset_paths import TreePathEndpoint

logger = logging.getLogger(__name__)

DEFAULT_GOLDEN_JSON = Path("scratch/golden_dataset/tree_path_golden.json")
DEFAULT_CYCLE_LOG = Path("scratch/golden_dataset/golden_endpoint_cycle_log.jsonl")
GOLDEN_GUARD_MAX_ITERATIONS = int(os.getenv("GOLDEN_GUARD_MAX_ITERATIONS", "10"))
GOLDEN_MIN_ALIGNMENT_PCT = float(os.getenv("GOLDEN_MIN_ALIGNMENT_PCT", "90"))

SUBNODE_TO_MODE = {
    "node2a": "node2a_clinical",
    "node2b": "node2b_in_vivo",
    "node2c": "node2c_in_vitro",
}

SUBNODE_TO_TAB = {
    "node2a": "clinical",
    "node2b": "preclinical",
    "node2c": "preclinical",
}


def _rules_version() -> str:
    """Loads rules_config.json version."""
    rules_path = ROOT / "rules_config.json"
    if rules_path.exists():
        try:
            with open(rules_path, encoding="utf-8") as handle:
                return str(json.load(handle).get("version") or "1.0.0")
        except Exception:
            pass
    return "1.0.0"


def load_tree_path_golden(path: Path = DEFAULT_GOLDEN_JSON) -> Dict[str, Any]:
    """Loads tree_path_golden.json candidate dataset."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def endpoint_block_from_golden(
    golden: Dict[str, Any],
    endpoint_id: str,
) -> Optional[Dict[str, Any]]:
    """Returns the endpoint block for an endpoint_id from tree_path_golden.json."""
    for block in golden.get("endpoints") or []:
        if block.get("endpoint_id") == endpoint_id:
            return block
    return None


def sorted_endpoint_ids_from_golden(golden: Dict[str, Any]) -> List[str]:
    """Returns endpoint ids sorted by PDF classification pool size (desc)."""
    blocks = list(golden.get("endpoints") or [])
    blocks.sort(
        key=lambda block: (
            -(block.get("pool_size_pdf_classification") or 0),
            -(block.get("pool_size_full_text_classification") or 0),
            str(block.get("endpoint_id") or ""),
        ),
    )
    return [str(block["endpoint_id"]) for block in blocks if block.get("endpoint_id")]


def resolve_endpoint_id(
    *,
    endpoint_id: Optional[str] = None,
    row_index: Optional[int] = None,
    golden_path: Path = DEFAULT_GOLDEN_JSON,
) -> str:
    """Resolves endpoint id from explicit id or sorted row index."""
    if endpoint_id:
        return endpoint_id
    golden = load_tree_path_golden(golden_path)
    ordered = sorted_endpoint_ids_from_golden(golden)
    if row_index is None:
        row_index = 0
    if row_index < 0 or row_index >= len(ordered):
        raise ValueError(f"row_index {row_index} out of range (0..{len(ordered) - 1})")
    return ordered[row_index]


def row_index_for_endpoint(
    endpoint_id: str,
    golden_path: Path = DEFAULT_GOLDEN_JSON,
) -> Optional[int]:
    """Return golden table row index for an endpoint id, or None if not in the table."""
    ordered = sorted_endpoint_ids_from_golden(load_tree_path_golden(golden_path))
    try:
        return ordered.index(endpoint_id)
    except ValueError:
        return None


def candidate_paper_ids(endpoint_block: Dict[str, Any]) -> List[int]:
    """Returns paper ids for candidate papers on an endpoint."""
    ids: List[int] = []
    for paper in endpoint_block.get("papers") or []:
        paper_id = paper.get("paper_id")
        if paper_id is not None:
            ids.append(int(paper_id))
    return ids


def cycle_artifact_dir(endpoint_id: str, cycle_id: str) -> Path:
    """Returns artifact directory for one cycle run."""
    return Path("scratch/golden_dataset/cycles") / endpoint_id / cycle_id


def make_cycle_id(endpoint_id: str) -> str:
    """Builds a unique cycle id timestamp suffix."""
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{endpoint_id}_{stamp}"


def build_golden_disagreement_batch(
    llm_results: Dict[str, Any],
    endpoint: TreePathEndpoint,
    *,
    cycle_id: str,
) -> Dict[str, Any]:
    """Builds a calibration-style batch JSON from LLM vs local Maude disagreements."""
    rules_version = llm_results.get("rules_version") or _rules_version()
    target_subnode = endpoint.scope_subnode
    mode = SUBNODE_TO_MODE.get(target_subnode, target_subnode)
    fields_in_scope = subnode_field_scopes.fields_in_scope(target_subnode)

    results: List[Dict[str, Any]] = []
    for item in llm_results.get("results") or []:
        paper_id = item.get("paper_id")
        llm = dict(item.get("ground_truth") or {})
        for field, value in list(llm.items()):
            llm[field] = golden_confirmed_store._parse_field_value(field, value)
        llm["classifier_version"] = item.get("classifier_version")
        llm["classification_confidence"] = item.get("classification_confidence")
        item["content_tier"] = content_tiers.infer_content_tier({
            **llm,
            "classifier_version": llm.get("classifier_version"),
        })

        title = str(item.get("title") or "")
        text_blob = item.get("text")
        text_source = str(item.get("text_source") or "")
        abstract = ""
        import paper_text_cache

        substantial = paper_text_cache.is_substantial_full_text(text_blob) or text_source in {
            "pdf_cache",
            "pdf",
            "fulltext",
        }
        if text_blob and not substantial:
            parts = str(text_blob).split("\n\n", 1)
            if len(parts) == 2:
                title = parts[0].strip() or title
                abstract = parts[1].strip()

        # Reuse disk cache / substantial blobs; never treat short title_abstract as full_text
        # (that previously forced full_text_link re-downloads on every disagreement/guard pass).
        maude_out, pdf_used = calibration_pdf.classify_maude_for_calibration(
            title,
            abstract,
            full_text=str(text_blob) if substantial and text_blob else None,
            full_text_link=item.get("full_text_link"),
            pmid=item.get("pmid"),
            doi=item.get("doi"),
            paper_id=int(paper_id) if paper_id is not None else None,
            rules_version=rules_version,
            text_source_hint=text_source or None,
        )
        content_tier = content_tiers.infer_content_tier({
            **llm,
            "classifier_version": llm.get("classifier_version"),
        })
        scope_fields = content_tiers.alignment_fields_in_scope_for_tier(
            target_subnode,
            content_tier,
            llm,
        )
        scoped = subnode_field_scopes.compare_scoped_fields(
            maude_out,
            llm,
            target_subnode,
            classification_schema.compare_field_values,
            scope_fields=scope_fields,
        )
        disagreement = scoped or {"fields": {}, "agreed_fields": {}, "disagreement_count": 0}

        results.append({
            "paper_id": paper_id,
            "pmid": item.get("pmid"),
            "title": item.get("title"),
            "pdf_text_used": pdf_used,
            "variant": "llm-golden",
            "dry_run": True,
            "status": "maude_paired",
            "content_tier": content_tier,
            "routing_subnode": target_subnode,
            "llm": llm,
            "maude": maude_out,
            "scoped_disagreement": disagreement,
            "disagreement": disagreement,
        })

    batch_id = f"golden_{cycle_id}"
    return {
        "batch_id": batch_id,
        "created_at": datetime.utcnow().isoformat(),
        "rules_version": rules_version,
        "mode": mode,
        "automation_node": target_subnode,
        "target_subnode": target_subnode,
        "endpoint_id": endpoint.id,
        "fields_in_scope": fields_in_scope,
        "calibration_label": subnode_field_scopes.SUBNODE_TO_CALIBRATION_LABEL.get(
            target_subnode,
            f"{target_subnode}-golden",
        ),
        "variants": ["llm-golden"],
        "candidate_count": len(results),
        "paired_count": len(results),
        "maude_only": True,
        "results": results,
        "golden_cycle_id": cycle_id,
        "golden_endpoint_id": endpoint.id,
    }


def enrich_llm_results_with_text(
    llm_results: Dict[str, Any],
    endpoint_block: Dict[str, Any],
) -> Dict[str, Any]:
    """Adds text blobs from paper_cache first, then tree_path_golden candidates."""
    import paper_text_cache

    text_by_id = {
        int(paper["paper_id"]): paper.get("text")
        for paper in endpoint_block.get("papers") or []
        if paper.get("paper_id") is not None
    }
    for item in llm_results.get("results") or []:
        paper_id = item.get("paper_id")
        if paper_id is None:
            continue
        pid = int(paper_id)
        cached_text, cached_source = paper_text_cache.lookup_cached_text_for_paper(pid)
        if cached_text:
            item["text"] = cached_text
            item["text_source"] = "pdf_cache"
            item.setdefault("cached_text_source", cached_source)
        elif not item.get("text"):
            item["text"] = text_by_id.get(pid)
        for paper in endpoint_block.get("papers") or []:
            if int(paper.get("paper_id")) == pid:
                item.setdefault("full_text_link", paper.get("full_text_link"))
                item.setdefault("doi", paper.get("doi"))
                break
    return llm_results


def promote_top_confirmed(
    llm_results: Dict[str, Any],
    endpoint: TreePathEndpoint,
    endpoint_block: Dict[str, Any],
    *,
    top_n: int = 5,
    cycle_id: str,
    confirmed_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Promotes top-N LLM results by characteristics count into golden_confirmed.json."""
    scope_fields = list(endpoint_block.get("scope_fields") or golden_dataset_paths.scope_fields_for_endpoint(endpoint))
    ranked = sorted(
        llm_results.get("results") or [],
        key=lambda item: (
            -(item.get("characteristics_identified_count") or 0),
            int(item.get("paper_id") or 0),
        ),
    )
    promoted: List[Dict[str, Any]] = []
    for item in ranked[:top_n]:
        paper_id = item.get("paper_id")
        if paper_id is None:
            continue
        text_blob = item.get("text")
        content_tier = item.get("content_tier") or ""
        if content_tier == content_tiers.CONTENT_TIER_ABSTRACT_ONLY:
            title_text = str(item.get("title") or "")
            abstract_text = ""
            for candidate in endpoint_block.get("papers") or []:
                if int(candidate.get("paper_id")) == int(paper_id):
                    parts = str(candidate.get("text") or "").split("\n\n", 1)
                    if len(parts) == 2:
                        abstract_text = parts[1].strip()
                    break
            text_blob = f"{title_text}\n\n{abstract_text}".strip()
        elif not text_blob:
            for candidate in endpoint_block.get("papers") or []:
                if int(candidate.get("paper_id")) == int(paper_id):
                    text_blob = candidate.get("text")
                    break
        record = {
            "paper_id": int(paper_id),
            "pmid": item.get("pmid"),
            "doi": item.get("doi"),
            "title": item.get("title"),
            "abstract": item.get("abstract") or "",
            "endpoint_id": endpoint.id,
            "scope_subnode": endpoint.scope_subnode,
            "scope_key": endpoint.scope_key,
            "scope_fields": scope_fields,
            "ground_truth": item.get("ground_truth") or {},
            "text_source": item.get("text_source") or "pdf_cache",
            "content_tier": item.get("content_tier") or "",
            "text": text_blob,
            "full_text_link": item.get("full_text_link"),
            "characteristics_identified_count": item.get("characteristics_identified_count"),
            "confirmed_at": golden_confirmed_store.utc_now_iso(),
            "llm_classifier_version": item.get("classifier_version"),
            "classification_confidence": item.get("classification_confidence"),
            "cycle_id": cycle_id,
        }
        promoted.append(record)
    golden_confirmed_store.replace_endpoint_papers(endpoint.id, promoted, path=confirmed_path)
    return promoted


def bootstrap_llm_results_from_candidates(
    endpoint: TreePathEndpoint,
    endpoint_block: Dict[str, Any],
    *,
    sqlite_path: str,
    cycle_id: str,
) -> Dict[str, Any]:
    """Writes candidate ground_truth into SQLite as llm-golden labels (no Claude API)."""
    import sqlite3

    import calibration_metrics

    from scripts.golden_llm_classify import golden_classifier_version

    rules_version = _rules_version()
    classifier_version = golden_classifier_version(endpoint.id, rules_version)
    scope_fields = list(
        endpoint_block.get("scope_fields") or golden_dataset_paths.scope_fields_for_endpoint(endpoint),
    )

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    results: List[Dict[str, Any]] = []
    updated_ids: List[int] = []

    for candidate in endpoint_block.get("papers") or []:
        paper_id = candidate.get("paper_id")
        if paper_id is None:
            continue
        cursor.execute("SELECT * FROM papers WHERE id = ?", (int(paper_id),))
        row = cursor.fetchone()
        if not row:
            continue
        paper = dict(row)
        ground_truth = candidate.get("ground_truth") or golden_confirmed_store.build_ground_truth_from_row(
            paper,
            scope_fields,
        )
        set_clauses = []
        params: List[Any] = []
        for field, value in ground_truth.items():
            if field not in paper:
                continue
            set_clauses.append(f"{field} = ?")
            if isinstance(value, (list, dict)):
                params.append(json.dumps(value))
            else:
                params.append(value)
        set_clauses.append("classifier_version = ?")
        params.append(classifier_version)
        set_clauses.append("classification_confidence = ?")
        params.append(0.9)
        set_clauses.append("classification_timestamp = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(int(paper_id))
        if set_clauses:
            cursor.execute(
                f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        char_count = candidate.get("characteristics_identified_count") or sum(
            1 for field in scope_fields if calibration_metrics.field_is_populated(ground_truth.get(field))
        )
        results.append({
            "paper_id": int(paper_id),
            "pmid": candidate.get("pmid"),
            "title": candidate.get("title"),
            "endpoint_id": endpoint.id,
            "scope_subnode": endpoint.scope_subnode,
            "scope_key": endpoint.scope_key,
            "scope_fields": scope_fields,
            "classifier_version": classifier_version,
            "classification_confidence": 0.9,
            "text_source": "pdf_cache",
            "text": candidate.get("text"),
            "full_text_link": candidate.get("full_text_link"),
            "doi": candidate.get("doi"),
            "characteristics_identified_count": char_count,
            "characteristics_identified": candidate.get("characteristics_identified") or ground_truth,
            "ground_truth": ground_truth,
        })
        updated_ids.append(int(paper_id))

    conn.commit()
    conn.close()
    return {
        "endpoint_id": endpoint.id,
        "scope_subnode": endpoint.scope_subnode,
        "rules_version": rules_version,
        "classifier_version": classifier_version,
        "classification_confidence": 0.9,
        "bootstrap_from_candidates": True,
        "papers_updated": updated_ids,
        "results": results,
    }


def run_pull(
    sqlite_path: str,
    paper_ids: Sequence[int],
    *,
    reingest_only: bool = True,
) -> None:
    """Pull candidate papers from Postgres into SQLite and seed push baselines."""
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL required for pull.")

    base = [
        sys.executable,
        str(ROOT / "scripts/pull_papers_from_postgres.py"),
        "--sqlite-path",
        sqlite_path,
        "--skip-init",
    ]
    if paper_ids:
        cmd = list(base)
        for paper_id in paper_ids:
            cmd.extend(["--paper-id", str(paper_id)])
        subprocess.run(cmd, check=True)
        return

    pull_args = base + ["--batch-size", "500", "--replace-all-baseline"]
    if reingest_only:
        pull_args.append("--reingest-only")
    subprocess.run(pull_args, check=True)


def run_reingest(
    sqlite_path: str,
    scope_subnode: str,
    *,
    paper_ids: Optional[List[int]] = None,
    batch_size: int = 50,
    workers: int = 4,
    row_index: Optional[int] = None,
    full_subnode: bool = False,
) -> Dict[str, Any]:
    """Runs subnode-scoped local Maude two-pass reingest."""
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["DATABASE_PATH"] = sqlite_path
    env.setdefault("PAPER_TEXT_CACHE_DIR", "scratch/paper_cache")
    if row_index is not None:
        env["GOLDEN_ROW_INDEX"] = str(int(row_index))
    else:
        env.pop("GOLDEN_ROW_INDEX", None)

    cmd = [
        sys.executable,
        str(ROOT / "reingest_heuristic_papers.py"),
        "--pass",
        "two-pass",
        "--no-skip-current",
        "--batch-size",
        str(batch_size),
        "--workers",
        str(workers),
        "--refresh-maude-confidence",
    ]
    if full_subnode:
        cmd.extend(["--scope-subnode", scope_subnode])
    elif paper_ids:
        cmd.extend(
            ["--paper-ids", ",".join(str(int(pid)) for pid in sorted(set(paper_ids)))],
        )
    else:
        tab = SUBNODE_TO_TAB.get(scope_subnode, "clinical")
        cmd.extend(["--tabs", tab])
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("Reingest failed: %s", proc.stderr)
        raise RuntimeError(f"Reingest failed with code {proc.returncode}")

    summary: Dict[str, Any] = {"stdout_tail": proc.stdout[-4000:]}
    for line in proc.stdout.splitlines():
        if line.startswith("GOLDEN_REINGEST_SUMMARY="):
            try:
                summary = json.loads(line.split("=", 1)[1])
                break
            except json.JSONDecodeError:
                pass
    if "field_change_counts" not in summary:
        for line in proc.stdout.splitlines():
            if line.strip().startswith("{") and "written_paper_ids" in line:
                try:
                    summary = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
    if full_subnode:
        summary["full_subnode"] = True
    return summary


def run_push(
    sqlite_path: str,
    *,
    dry_run: bool = False,
    batch_size: int = 25,
) -> Dict[str, Any]:
    """Pushes classification deltas from SQLite to Postgres."""
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL required for push.")

    cmd = [
        sys.executable,
        str(ROOT / "scripts/push_classification_deltas.py"),
        "--sqlite-path",
        sqlite_path,
        "--batch-size",
        str(batch_size),
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("Push failed: %s", proc.stderr)
        raise RuntimeError(f"Push failed with code {proc.returncode}")
    return {"stdout": proc.stdout, "dry_run": dry_run}


def run_golden_guard(
    scope_subnode: str,
    artifact_dir: Path,
    *,
    endpoint_id: Optional[str] = None,
    max_iterations: int = GOLDEN_GUARD_MAX_ITERATIONS,
) -> Tuple[Dict[str, Any], int]:
    """Runs golden regression once; tracks cumulative attempts across guard-only re-runs.

    Defaults to endpoint-scoped checks when ``endpoint_id`` is provided so one endpoint's
    cycle does not fail on unrelated confirmed papers in the same subnode.
    """
    regression_script = ROOT / "scripts/golden_confirmed_regression.py"
    attempt_file = artifact_dir / "guard_attempts.json"
    attempts = 0
    if attempt_file.exists():
        try:
            with open(attempt_file, encoding="utf-8") as handle:
                attempts = int(json.load(handle).get("attempts") or 0)
        except Exception:
            attempts = 0

    attempts += 1
    with open(attempt_file, "w", encoding="utf-8") as handle:
        json.dump({"attempts": attempts, "max": max_iterations}, handle)

    out_path = artifact_dir / f"golden_regression_iter_{attempts}.json"
    cmd = [
        sys.executable,
        str(regression_script),
        "--scope-subnode",
        scope_subnode,
        "--output",
        str(out_path),
        "--min-alignment-pct",
        str(GOLDEN_MIN_ALIGNMENT_PCT),
    ]
    if endpoint_id:
        cmd.extend(["--endpoint-id", endpoint_id])
    # Stream stdout/stderr (avoid capture_output deadlock on long PDF/cache runs).
    logger.info("Golden guard attempt %s: %s", attempts, " ".join(cmd))
    proc = subprocess.run(cmd, text=True)
    report: Dict[str, Any] = {}
    if out_path.exists():
        with open(out_path, encoding="utf-8") as handle:
            report = json.load(handle)
    else:
        report = {
            "passed": False,
            "error": f"guard output missing (exit {proc.returncode})",
        }

    if not report.get("passed"):
        failures_path = artifact_dir / f"golden_regression_failures_iter_{attempts}.json"
        with open(failures_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)

    return report, attempts


def run_feedback_on_batch(
    batch_path: Path,
    artifact_dir: Path,
    *,
    llm_results: Optional[Dict[str, Any]] = None,
    local_only: bool = False,
) -> Dict[str, Any]:
    """Runs golden Claude patch feedback (or local-only fallback) on a disagreement batch."""
    if local_only:
        result = cfa.run_feedback_cycle(
            batch_path,
            output_dir=artifact_dir,
            skip_lock=True,
            local_only=True,
            skip_refresh=True,
        )
        feedback_path = artifact_dir / f"{batch_path.stem}_feedback_report.json"
    else:
        result = cfa.run_golden_feedback_cycle(
            batch_path,
            output_dir=artifact_dir,
            llm_results=llm_results,
            skip_lock=True,
            skip_refresh=True,
        )
        feedback_path = artifact_dir / f"{batch_path.stem}_golden_feedback_report.json"
    with open(feedback_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
    return result


def append_cycle_log(entry: Dict[str, Any], log_path: Path = DEFAULT_CYCLE_LOG) -> None:
    """Appends one JSON line to the golden endpoint cycle log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_cycle(
    *,
    endpoint_id: Optional[str] = None,
    row_index: Optional[int] = None,
    sqlite_path: str = "cannabis_papers.db",
    pull: bool = True,
    llm: bool = True,
    promote: bool = True,
    patch_feedback: bool = True,
    golden_guard: bool = True,
    reingest: bool = True,
    push: bool = True,
    dry_run_push: bool = False,
    skip_patch_require_pass: bool = False,
    bootstrap_llm: bool = False,
    golden_local_feedback: bool = False,
    golden_path: Path = DEFAULT_GOLDEN_JSON,
    confirmed_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Executes the full golden endpoint RL cycle."""
    resolved_endpoint_id = resolve_endpoint_id(
        endpoint_id=endpoint_id,
        row_index=row_index,
        golden_path=golden_path,
    )
    endpoint = golden_dataset_paths.endpoint_by_id(resolved_endpoint_id)
    if endpoint is None:
        raise ValueError(f"Unknown endpoint: {resolved_endpoint_id}")

    golden = load_tree_path_golden(golden_path)
    endpoint_block = endpoint_block_from_golden(golden, resolved_endpoint_id)
    if not endpoint_block:
        raise ValueError(f"Endpoint block not found in golden JSON: {resolved_endpoint_id}")

    cycle_id = make_cycle_id(resolved_endpoint_id)
    artifact_dir = cycle_artifact_dir(resolved_endpoint_id, cycle_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    paper_ids = candidate_paper_ids(endpoint_block)
    saved_database_url = os.environ.pop("DATABASE_URL", None)
    report: Dict[str, Any] = {
        "cycle_id": cycle_id,
        "endpoint_id": resolved_endpoint_id,
        "scope_subnode": endpoint.scope_subnode,
        "sqlite_path": sqlite_path,
        "paper_ids": paper_ids,
        "stages": {},
    }

    if pull:
        if saved_database_url:
            os.environ["DATABASE_URL"] = saved_database_url
        run_pull(sqlite_path, paper_ids)
        saved_database_url = os.environ.pop("DATABASE_URL", saved_database_url)
        report["stages"]["pull"] = {"ok": True, "paper_count": len(paper_ids)}
    elif llm and paper_ids:
        # Cycle always classifies against local SQLite (DATABASE_URL is popped).
        # Fail fast when --no-pull points at an empty/stale DB instead of silent skips.
        import sqlite3

        missing: List[int] = []
        try:
            conn = sqlite3.connect(sqlite_path)
            try:
                for pid in paper_ids:
                    row = conn.execute(
                        "SELECT 1 FROM papers WHERE id = ?",
                        (int(pid),),
                    ).fetchone()
                    if not row:
                        missing.append(int(pid))
            finally:
                conn.close()
        except sqlite3.Error as exc:
            if saved_database_url:
                os.environ["DATABASE_URL"] = saved_database_url
            raise RuntimeError(
                f"SQLite preflight failed for {sqlite_path}: {exc}. "
                "Use --sqlite-path to a populated DB (e.g. /data/cannabis_papers.db) "
                "or omit --no-pull so papers are pulled from Postgres."
            ) from exc
        if missing:
            if saved_database_url:
                os.environ["DATABASE_URL"] = saved_database_url
            raise RuntimeError(
                f"--no-pull but {len(missing)}/{len(paper_ids)} candidate paper ids "
                f"missing from {sqlite_path} (e.g. {missing[:5]}). "
                "Pass --sqlite-path /data/cannabis_papers.db on Fly, or enable pull."
            )

    llm_results: Dict[str, Any] = {}
    if llm:
        if bootstrap_llm:
            llm_results = bootstrap_llm_results_from_candidates(
                endpoint,
                endpoint_block,
                sqlite_path=sqlite_path,
                cycle_id=cycle_id,
            )
        else:
            from scripts.golden_llm_classify import classify_papers_for_endpoint

            llm_results = classify_papers_for_endpoint(
                paper_ids,
                resolved_endpoint_id,
                sqlite_path=sqlite_path,
            )
        llm_results = enrich_llm_results_with_text(llm_results, endpoint_block)
        llm_path = artifact_dir / "llm_results.json"
        with open(llm_path, "w", encoding="utf-8") as handle:
            json.dump(llm_results, handle, indent=2, ensure_ascii=False)
        report["stages"]["llm"] = {
            "ok": True,
            "papers_updated": llm_results.get("papers_updated"),
            "classifier_version": llm_results.get("classifier_version"),
            "bootstrap": bool(llm_results.get("bootstrap_from_candidates")),
        }
    else:
        llm_path = artifact_dir / "llm_results.json"
        if llm_path.exists():
            with open(llm_path, encoding="utf-8") as handle:
                llm_results = json.load(handle)

    promoted: List[Dict[str, Any]] = []
    if promote and llm_results:
        promoted = promote_top_confirmed(
            llm_results,
            endpoint,
            endpoint_block,
            cycle_id=cycle_id,
            confirmed_path=confirmed_path,
        )
        report["stages"]["promote"] = {
            "ok": True,
            "promoted_count": len(promoted),
            "paper_ids": [p["paper_id"] for p in promoted],
        }

    batch_payload: Optional[Dict[str, Any]] = None
    if patch_feedback and llm_results:
        batch_payload = build_golden_disagreement_batch(llm_results, endpoint, cycle_id=cycle_id)
        batch_path = artifact_dir / f"golden_disagreement_{cycle_id}.json"
        with open(batch_path, "w", encoding="utf-8") as handle:
            json.dump(batch_payload, handle, indent=2, ensure_ascii=False)
        feedback = run_feedback_on_batch(
            batch_path,
            artifact_dir,
            llm_results=llm_results,
            local_only=golden_local_feedback,
        )
        report["stages"]["feedback"] = {
            "ok": feedback.get("status") == "completed",
            "batch_path": str(batch_path),
            "feedback_status": feedback.get("status"),
            "golden_claude_feedback": not golden_local_feedback,
            "staged_patch_path": feedback.get("staged_patch_path"),
            "agent_handoff_prompt": feedback.get("agent_handoff_prompt"),
        }

    guard_report: Dict[str, Any] = {}
    guard_iterations = 0
    if golden_guard:
        guard_report, guard_iterations = run_golden_guard(
            endpoint.scope_subnode,
            artifact_dir,
            endpoint_id=endpoint.id,
        )
        report["stages"]["golden_guard"] = {
            "passed": guard_report.get("passed"),
            "iterations": guard_iterations,
            "endpoint_id": endpoint.id,
            "papers_checked": guard_report.get("papers_checked"),
            "papers_failed": guard_report.get("papers_failed"),
            "batch_alignment_pct": guard_report.get("batch_alignment_pct"),
            "min_alignment_pct": guard_report.get("min_alignment_pct"),
        }
        if not guard_report.get("passed") and not skip_patch_require_pass:
            attempts = guard_iterations
            if attempts < GOLDEN_GUARD_MAX_ITERATIONS:
                report["status"] = "blocked_golden_guard"
                report["guard_retry_hint"] = (
                    f"Implement Maude patch then re-run with --guard-only "
                    f"(attempt {attempts}/{GOLDEN_GUARD_MAX_ITERATIONS})"
                )
            else:
                report["status"] = "blocked_golden_guard_max_attempts"
            report_path = artifact_dir / "cycle_report.json"
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
            append_cycle_log(report)
            if saved_database_url:
                os.environ["DATABASE_URL"] = saved_database_url
            return report

    reingest_summary: Dict[str, Any] = {}
    if reingest:
        confirmed_store = golden_confirmed_store.load_confirmed(confirmed_path)
        reingest_ids = list({
            int(pid)
            for pid in paper_ids
            + [
                p.get("paper_id")
                for p in confirmed_store.get("papers") or []
                if p.get("endpoint_id") == resolved_endpoint_id and p.get("paper_id") is not None
            ]
        })
        full_subnode = os.getenv("GOLDEN_FULL_SUBNODE_REINGEST", "1") == "1"
        reingest_summary = run_reingest(
            sqlite_path,
            endpoint.scope_subnode,
            paper_ids=None if full_subnode else reingest_ids,
            row_index=(
                row_index
                if row_index is not None
                else row_index_for_endpoint(resolved_endpoint_id, golden_path)
            ),
            full_subnode=full_subnode,
        )
        if not full_subnode:
            reingest_summary["paper_ids"] = reingest_ids
        else:
            reingest_summary["scope_subnode"] = endpoint.scope_subnode
        report["stages"]["reingest"] = reingest_summary

    push_summary: Dict[str, Any] = {}
    if push:
        if saved_database_url:
            os.environ["DATABASE_URL"] = saved_database_url
        push_summary = run_push(sqlite_path, dry_run=dry_run_push)
        report["stages"]["push"] = push_summary
    elif saved_database_url:
        os.environ["DATABASE_URL"] = saved_database_url

    report["status"] = "completed"
    report_path = artifact_dir / "cycle_report.json"
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    append_cycle_log(report)
    _record_endpoint_status(resolved_endpoint_id, report)
    return report


def _record_endpoint_status(endpoint_id: str, report: Dict[str, Any]) -> None:
    """Updates golden_endpoint_status.json from a finished cycle report."""
    try:
        from calibration_build import MAUDE_CLASSIFIER_BUILD_ID
        build_id = MAUDE_CLASSIFIER_BUILD_ID
    except Exception:
        build_id = None

    update_endpoint_status(
        endpoint_id,
        {
            **patch_from_cycle_report(report),
            "maude_build_id": build_id,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Golden endpoint RL cycle orchestrator.")
    parser.add_argument("--endpoint-id", default=None, help="Tree path endpoint id.")
    parser.add_argument("--row-index", type=int, default=None, help="Sorted row index.")
    parser.add_argument("--sqlite-path", default="cannabis_papers.db")
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument("--no-feedback", action="store_true")
    parser.add_argument("--no-golden-guard", action="store_true")
    parser.add_argument("--no-reingest", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--dry-run-push", action="store_true")
    parser.add_argument(
        "--skip-patch-require-pass",
        action="store_true",
        help="Continue even if golden guard fails (not recommended).",
    )
    parser.add_argument(
        "--guard-only",
        action="store_true",
        help="Only run golden regression guard (after manual patch).",
    )
    parser.add_argument(
        "--artifact-dir",
        default=None,
        help="Existing cycle artifact dir for --guard-only retries.",
    )
    parser.add_argument(
        "--bootstrap-llm",
        action="store_true",
        help="Stamp candidate ground_truth as llm-golden without Claude API.",
    )
    parser.add_argument(
        "--local-feedback",
        action="store_true",
        help="Skip Claude golden patch feedback (local-only disagreement summary).",
    )
    return parser


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()

    if args.guard_only:
        if not args.artifact_dir:
            raise SystemExit("--artifact-dir required with --guard-only")
        artifact_dir = Path(args.artifact_dir)
        scope_subnode = args.endpoint_id and golden_dataset_paths.endpoint_by_id(
            args.endpoint_id,
        )
        if scope_subnode is None and args.endpoint_id:
            raise SystemExit(f"Unknown endpoint-id: {args.endpoint_id}")
        subnode = scope_subnode.scope_subnode if scope_subnode else "node2a"
        guard_report, attempts = run_golden_guard(
            subnode,
            artifact_dir,
            endpoint_id=args.endpoint_id,
        )
        print(json.dumps({"guard": guard_report, "attempts": attempts}, indent=2))
        if not guard_report.get("passed"):
            if attempts >= GOLDEN_GUARD_MAX_ITERATIONS:
                raise SystemExit(3)
            raise SystemExit(2)
        return

    report = run_cycle(
        endpoint_id=args.endpoint_id,
        row_index=args.row_index,
        sqlite_path=args.sqlite_path,
        pull=not args.no_pull,
        llm=not args.no_llm,
        promote=not args.no_promote,
        patch_feedback=not args.no_feedback,
        golden_guard=not args.no_golden_guard,
        reingest=not args.no_reingest,
        push=not args.no_push,
        dry_run_push=args.dry_run_push,
        skip_patch_require_pass=args.skip_patch_require_pass,
        bootstrap_llm=args.bootstrap_llm,
        golden_local_feedback=args.local_feedback,
    )
    print(json.dumps(report, indent=2))
    if report.get("status") == "blocked_golden_guard":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
