# calibration_metrics.py
"""Aggregate calibration run artifacts into learning metrics for dashboards and agents."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HIGH_LEVEL_FIELDS = (
    "study_type",
    "exposure_method",
    "cannabis_type",
    "outcome_domain",
    "publication_type",
)

DEFAULT_OUTPUT_DIR = Path("scratch/calibration_runs")
DEFAULT_RULES_PATH = Path("rules_config.json")


def load_rules_config(path: Path = DEFAULT_RULES_PATH) -> Dict[str, Any]:
    """Loads rules configuration used for automation readiness checks."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_calibration_batch(path: Path) -> Dict[str, Any]:
    """Loads a single calibration JSON artifact."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def discover_calibration_batches(output_dir: Path = DEFAULT_OUTPUT_DIR) -> List[Path]:
    """Returns calibration JSON artifacts sorted by batch timestamp."""
    if not output_dir.exists():
        return []
    batches = sorted(output_dir.glob("calibration_*.json"))
    return [path for path in batches if not path.name.endswith("_data.json")]


def count_high_level_changes(changes: Optional[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Counts how many high-level fields changed in a result diff."""
    if not changes:
        return 0, []
    changed = [field for field in HIGH_LEVEL_FIELDS if field in changes]
    return len(changed), changed


def summarize_variant_results(results: Sequence[Dict[str, Any]], variant: str) -> Dict[str, Any]:
    """Summarizes metrics for one prompt variant within a batch."""
    variant_results = [row for row in results if row.get("variant") == variant]
    confidences: List[float] = []
    total_cost = 0.0
    high_level_changed = 0
    field_counts: Dict[str, int] = {}
    high_level_field_counts: Dict[str, int] = {}

    for result in variant_results:
        if result.get("after_confidence") is not None:
            confidences.append(float(result["after_confidence"]))
        metrics = result.get("llm_metrics") or {}
        total_cost += float(metrics.get("cost") or 0.0)

        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count:
            high_level_changed += 1
        for field in hl_fields:
            high_level_field_counts[field] = high_level_field_counts.get(field, 0) + 1

        for field in (result.get("changes") or {}).keys():
            field_counts[field] = field_counts.get(field, 0) + 1

    paper_count = len(variant_results)
    return {
        "variant": variant,
        "paper_count": paper_count,
        "updates_applied": sum(1 for row in variant_results if row.get("status") == "updated"),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "high_level_changed": high_level_changed,
        "high_level_change_rate": round(high_level_changed / paper_count, 3) if paper_count else 0.0,
        "total_cost": round(total_cost, 4),
        "avg_cost": round(total_cost / paper_count, 4) if paper_count else 0.0,
        "field_change_counts": dict(sorted(field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_field_counts": dict(
            sorted(high_level_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
    }


def summarize_batch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarizes one calibration batch payload."""
    results = payload.get("results") or []
    variants = payload.get("variants") or sorted({row.get("variant", "unknown") for row in results})
    variant_summaries = [summarize_variant_results(results, variant) for variant in variants]

    total_cost = 0.0
    field_counts: Dict[str, int] = {}
    high_level_field_counts: Dict[str, int] = {}
    high_level_changed = 0
    confidences: List[float] = []

    for result in results:
        if result.get("after_confidence") is not None:
            confidences.append(float(result["after_confidence"]))
        total_cost += float((result.get("llm_metrics") or {}).get("cost") or 0.0)
        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count:
            high_level_changed += 1
        for field in hl_fields:
            high_level_field_counts[field] = high_level_field_counts.get(field, 0) + 1
        for field in (result.get("changes") or {}).keys():
            field_counts[field] = field_counts.get(field, 0) + 1

    return {
        "batch_id": payload.get("batch_id"),
        "created_at": payload.get("created_at"),
        "rules_version": payload.get("rules_version"),
        "mode": payload.get("mode"),
        "variants": variants,
        "calls_attempted": payload.get("calls_attempted", 0),
        "updates_applied": payload.get("updates_applied", 0),
        "dry_run": payload.get("dry_run", False),
        "abstract_only": payload.get("abstract_only", True),
        "paper_count": len(results),
        "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "high_level_changed": high_level_changed,
        "high_level_change_rate": round(high_level_changed / len(results), 3) if results else 0.0,
        "total_cost": round(total_cost, 4),
        "field_change_counts": dict(sorted(field_counts.items(), key=lambda item: item[1], reverse=True)),
        "high_level_field_counts": dict(
            sorted(high_level_field_counts.items(), key=lambda item: item[1], reverse=True)
        ),
        "variant_summaries": variant_summaries,
    }


def build_review_candidates(
    results: Sequence[Dict[str, Any]],
    confidence_threshold: float = 0.72,
) -> List[Dict[str, Any]]:
    """Builds prioritized expert-review candidates from calibration results."""
    candidates: List[Dict[str, Any]] = []
    for result in results:
        hl_count, hl_fields = count_high_level_changes(result.get("changes"))
        if hl_count == 0:
            continue
        confidence = result.get("after_confidence")
        candidates.append({
            "paper_id": result.get("paper_id"),
            "pmid": result.get("pmid"),
            "title": result.get("title"),
            "variant": result.get("variant"),
            "confidence": confidence,
            "in_review_queue": confidence is not None and float(confidence) <= confidence_threshold,
            "high_level_fields": hl_fields,
            "high_level_field_count": hl_count,
            "changes": {
                field: result["changes"][field]
                for field in hl_fields
                if field in (result.get("changes") or {})
            },
        })

    candidates.sort(
        key=lambda row: (
            row.get("confidence") if row.get("confidence") is not None else 1.0,
            -row.get("high_level_field_count", 0),
        )
    )
    return candidates


def parse_review_markdown(review_path: Path) -> Dict[str, Any]:
    """Parses a calibration review markdown artifact for expert notes."""
    if not review_path.exists():
        return {"expert_notes": {}, "queue_overlap_count": None}

    text = review_path.read_text(encoding="utf-8")
    expert_notes: Dict[int, str] = {}
    sections = re.split(r"\n### Paper (\d+) \|", text)
    for index in range(1, len(sections), 2):
        paper_id = int(sections[index])
        body = sections[index + 1]
        note_match = re.search(r"- Expert status:\s*(.+)", body)
        if note_match:
            expert_notes[paper_id] = note_match.group(1).strip()

    overlap_match = re.search(r"overlap with calibration batch: `(\d+)`", text)
    return {
        "expert_notes": expert_notes,
        "queue_overlap_count": int(overlap_match.group(1)) if overlap_match else None,
    }


def compare_variants_across_batches(batch_summaries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregates A/B variant metrics across all calibration batches."""
    aggregate: Dict[str, Dict[str, Any]] = {}
    for batch in batch_summaries:
        for variant_summary in batch.get("variant_summaries") or []:
            variant = variant_summary["variant"]
            bucket = aggregate.setdefault(
                variant,
                {
                    "variant": variant,
                    "paper_count": 0,
                    "high_level_changed": 0,
                    "total_cost": 0.0,
                    "confidence_sum": 0.0,
                    "confidence_count": 0,
                    "high_level_field_counts": {},
                },
            )
            bucket["paper_count"] += variant_summary["paper_count"]
            bucket["high_level_changed"] += variant_summary["high_level_changed"]
            bucket["total_cost"] += variant_summary["total_cost"]
            if variant_summary.get("avg_confidence") is not None:
                bucket["confidence_sum"] += variant_summary["avg_confidence"] * variant_summary["paper_count"]
                bucket["confidence_count"] += variant_summary["paper_count"]
            for field, count in (variant_summary.get("high_level_field_counts") or {}).items():
                bucket["high_level_field_counts"][field] = (
                    bucket["high_level_field_counts"].get(field, 0) + count
                )

    comparison: List[Dict[str, Any]] = []
    for variant, bucket in sorted(aggregate.items()):
        paper_count = bucket["paper_count"]
        comparison.append({
            "variant": variant,
            "paper_count": paper_count,
            "avg_confidence": round(bucket["confidence_sum"] / bucket["confidence_count"], 3)
            if bucket["confidence_count"]
            else None,
            "high_level_changed": bucket["high_level_changed"],
            "high_level_change_rate": round(bucket["high_level_changed"] / paper_count, 3)
            if paper_count
            else 0.0,
            "total_cost": round(bucket["total_cost"], 4),
            "avg_cost": round(bucket["total_cost"] / paper_count, 4) if paper_count else 0.0,
            "high_level_field_counts": dict(
                sorted(bucket["high_level_field_counts"].items(), key=lambda item: item[1], reverse=True)
            ),
        })
    return {"variants": comparison}


def build_automation_readiness(
    batch_summaries: Sequence[Dict[str, Any]],
    rules_config: Dict[str, Any],
    expert_notes: Dict[int, str],
) -> Dict[str, Any]:
    """Builds automation readiness checklist items for pre-expert-guideline phase."""
    agent_cfg = rules_config.get("agent_automation") or {}
    decision_boundaries = rules_config.get("decision_boundaries") or {}
    live_batches = [batch for batch in batch_summaries if not batch.get("dry_run")]
    total_papers = sum(batch.get("paper_count", 0) for batch in live_batches)

    checklist = [
        {
            "id": "calibration_runner",
            "label": "Bounded calibration runner exercised",
            "status": "complete" if live_batches else "pending",
            "detail": f"{len(live_batches)} live batch(es), {total_papers} papers",
        },
        {
            "id": "variant_ab",
            "label": "Prompt variant A/B comparison captured",
            "status": "complete"
            if live_batches and all(len(batch.get("variants") or []) >= 2 for batch in live_batches)
            else "pending",
            "detail": "control vs decision_checklist",
        },
        {
            "id": "review_artifacts",
            "label": "High-level review walkthroughs produced",
            "status": "complete" if expert_notes else "in_progress",
            "detail": f"{len(expert_notes)} expert note(s) captured",
        },
        {
            "id": "decision_chart",
            "label": "Expert decision chart received",
            "status": "pending"
            if "awaiting" in str(agent_cfg.get("decision_chart_status", "")).lower()
            else "complete",
            "detail": agent_cfg.get("decision_chart_status", "unknown"),
        },
        {
            "id": "decision_boundaries",
            "label": "Learned decision boundaries encoded in rules_config",
            "status": "in_progress" if decision_boundaries else "pending",
            "detail": f"{len(decision_boundaries)} boundary rule(s)",
        },
        {
            "id": "expert_corrections",
            "label": "Expert-approved corrections applied via edit-classification",
            "status": "in_progress" if expert_notes else "pending",
            "detail": "Use /api/papers/<paper_id>/edit-classification after review",
        },
    ]

    completed = sum(1 for item in checklist if item["status"] == "complete")
    return {
        "checklist": checklist,
        "completed_count": completed,
        "total_count": len(checklist),
        "ready_for_full_automation": completed == len(checklist),
    }


def build_dashboard_metrics(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rules_config: Optional[Dict[str, Any]] = None,
    confidence_threshold: float = 0.72,
) -> Dict[str, Any]:
    """Builds aggregated learning metrics from all calibration artifacts."""
    rules_config = rules_config or load_rules_config()
    batch_paths = discover_calibration_batches(output_dir)

    batches: List[Dict[str, Any]] = []
    all_candidates: List[Dict[str, Any]] = []
    expert_notes: Dict[int, str] = {}
    queue_overlap_total = 0

    aggregate_field_counts: Dict[str, int] = {}
    aggregate_hl_field_counts: Dict[str, int] = {}
    total_papers = 0
    total_cost = 0.0
    total_high_level_changed = 0

    for path in batch_paths:
        payload = load_calibration_batch(path)
        summary = summarize_batch(payload)
        review_meta = parse_review_markdown(path.with_name(f"{path.stem}_review.md"))
        summary["review_path"] = str(path.with_name(f"{path.stem}_review.md"))
        summary["walkthrough_path"] = str(path.with_name(f"{path.stem}_walkthrough.md"))
        summary["artifact_path"] = str(path)
        summary["expert_notes"] = review_meta["expert_notes"]
        expert_notes.update(review_meta["expert_notes"])
        if review_meta.get("queue_overlap_count") is not None:
            queue_overlap_total += review_meta["queue_overlap_count"]

        candidates = build_review_candidates(payload.get("results") or [], confidence_threshold)
        for candidate in candidates:
            note = expert_notes.get(candidate["paper_id"])
            if note:
                candidate["expert_status"] = note
        summary["review_candidates"] = candidates[:15]
        all_candidates.extend(candidates)
        batches.append(summary)

        total_papers += summary["paper_count"]
        total_cost += summary["total_cost"]
        total_high_level_changed += summary["high_level_changed"]
        for field, count in summary["field_change_counts"].items():
            aggregate_field_counts[field] = aggregate_field_counts.get(field, 0) + count
        for field, count in summary["high_level_field_counts"].items():
            aggregate_hl_field_counts[field] = aggregate_hl_field_counts.get(field, 0) + count

    deduped_candidates: Dict[int, Dict[str, Any]] = {}
    for candidate in all_candidates:
        paper_id = candidate["paper_id"]
        existing = deduped_candidates.get(paper_id)
        if existing is None or (candidate.get("confidence") or 1.0) < (existing.get("confidence") or 1.0):
            deduped_candidates[paper_id] = candidate
    priority_review = sorted(
        deduped_candidates.values(),
        key=lambda row: (
            row.get("confidence") if row.get("confidence") is not None else 1.0,
            -row.get("high_level_field_count", 0),
        ),
    )[:20]

    rules_version = rules_config.get("version")
    if batches and batches[-1].get("rules_version"):
        rules_version = batches[-1]["rules_version"]

    return {
        "generated_at": datetime.now().isoformat(),
        "rules_version": rules_version,
        "confidence_threshold": confidence_threshold,
        "summary": {
            "batch_count": len(batches),
            "total_papers": total_papers,
            "total_cost": round(total_cost, 4),
            "high_level_changed": total_high_level_changed,
            "high_level_change_rate": round(total_high_level_changed / total_papers, 3)
            if total_papers
            else 0.0,
            "queue_overlap_total": queue_overlap_total,
            "priority_review_count": len(priority_review),
            "expert_notes_count": len(expert_notes),
        },
        "batches": batches,
        "variant_comparison": compare_variants_across_batches(batches),
        "field_change_totals": {
            "all_fields": dict(sorted(aggregate_field_counts.items(), key=lambda item: item[1], reverse=True)),
            "high_level_fields": dict(
                sorted(aggregate_hl_field_counts.items(), key=lambda item: item[1], reverse=True)
            ),
        },
        "priority_review": priority_review,
        "decision_boundaries": rules_config.get("decision_boundaries") or {},
        "calibration_variants": rules_config.get("calibration_variants") or {},
        "automation_readiness": build_automation_readiness(batches, rules_config, expert_notes),
    }


def write_dashboard_data(metrics: Dict[str, Any], output_path: Path) -> Path:
    """Writes dashboard metrics JSON for static or API consumption."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, indent=2, default=str)
    return output_path


def write_dashboard_html(metrics: Dict[str, Any], output_path: Path) -> Path:
    """Writes a standalone HTML dashboard with embedded metrics."""
    payload = json.dumps(metrics, default=str)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Calibration Learning Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2"></script>
  <style>
    :root {{
      --bg: #0b0d14;
      --panel: rgba(255,255,255,0.03);
      --border: rgba(255,255,255,0.08);
      --text: #e8edf2;
      --muted: #90a4ae;
      --cyan: #22d3ee;
      --green: #34d399;
      --amber: #fbbf24;
      --red: #f87171;
      --indigo: #818cf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, sans-serif;
      background: radial-gradient(circle at top, #121826, var(--bg));
      color: var(--text);
      min-height: 100vh;
    }}
    header {{
      padding: 28px 32px 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
      align-items: center;
    }}
    header h1 {{
      margin: 0;
      font-family: Outfit, sans-serif;
      font-size: 1.6rem;
    }}
    header p {{ margin: 6px 0 0; color: var(--muted); font-size: 0.92rem; }}
    .wrap {{ padding: 24px 32px 48px; display: flex; flex-direction: column; gap: 20px; }}
    .grid {{ display: grid; gap: 16px; }}
    .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
    .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
    }}
    .stat-label {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      font-weight: 600;
    }}
    .stat-value {{
      margin-top: 8px;
      font-size: 1.8rem;
      font-weight: 800;
      font-family: Outfit, sans-serif;
    }}
    h2 {{
      margin: 0 0 14px;
      font-family: Outfit, sans-serif;
      font-size: 1.05rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.86rem;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .chart-box {{ height: 280px; position: relative; }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
    }}
    .badge-complete {{ background: rgba(52,211,153,0.15); color: var(--green); }}
    .badge-progress {{ background: rgba(251,191,36,0.15); color: var(--amber); }}
    .badge-pending {{ background: rgba(248,113,113,0.12); color: var(--red); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }}
    .muted {{ color: var(--muted); }}
    .paper-title {{ max-width: 520px; }}
    .checklist-item {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
    }}
    .checklist-item:last-child {{ border-bottom: none; }}
    footer {{
      padding: 0 32px 32px;
      color: var(--muted);
      font-size: 0.82rem;
    }}
    code {{ color: var(--cyan); }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Calibration Learning Dashboard</h1>
      <p>Manual learning batches · rules v<span id="rules-version"></span> · generated <span id="generated-at"></span></p>
    </div>
    <div class="mono muted">Refresh: <code>python3 calibration_metrics.py --build-dashboard</code></div>
  </header>
  <div class="wrap">
    <div class="grid grid-4" id="summary-cards"></div>

    <div class="grid grid-2">
      <div class="card">
        <h2>Variant A/B Comparison</h2>
        <div class="chart-box"><canvas id="chart-variant-confidence"></canvas></div>
      </div>
      <div class="card">
        <h2>High-Level Field Changes (All Batches)</h2>
        <div class="chart-box"><canvas id="chart-hl-fields"></canvas></div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h2>Batch Timeline</h2>
        <div style="overflow-x:auto">
          <table id="batch-table">
            <thead>
              <tr>
                <th>Batch</th>
                <th>Papers</th>
                <th>HL Changed</th>
                <th>Avg Conf</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <h2>Automation Readiness</h2>
        <div id="readiness-list"></div>
      </div>
    </div>

    <div class="card">
      <h2>Learned Decision Boundaries</h2>
      <div id="boundaries"></div>
    </div>

    <div class="card">
      <h2>Priority Expert Review Queue</h2>
      <div style="overflow-x:auto">
        <table id="review-table">
          <thead>
            <tr>
              <th>Paper</th>
              <th>Conf</th>
              <th>Variant</th>
              <th>HL Fields</th>
              <th>Title</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
  <footer>
    API endpoint when app is running: <code>/api/calibration/dashboard-metrics</code>
  </footer>
  <script>
    const METRICS = {payload};

    function pct(value) {{
      return value == null ? '—' : (value * 100).toFixed(1) + '%';
    }}

    function badge(status) {{
      const cls = status === 'complete' ? 'badge-complete' : (status === 'in_progress' ? 'badge-progress' : 'badge-pending');
      return `<span class="badge ${{cls}}">${{status.replace('_', ' ')}}</span>`;
    }}

    function renderSummary() {{
      const s = METRICS.summary;
      document.getElementById('rules-version').textContent = METRICS.rules_version || '—';
      document.getElementById('generated-at').textContent = (METRICS.generated_at || '').replace('T', ' ').slice(0, 19);
      const cards = [
        ['Calibration Batches', s.batch_count, 'var(--indigo)'],
        ['Papers Processed', s.total_papers, 'var(--cyan)'],
        ['HL Field Changes', s.high_level_changed, 'var(--amber)'],
        ['Total API Cost', '$' + (s.total_cost || 0).toFixed(2), 'var(--green)'],
      ];
      document.getElementById('summary-cards').innerHTML = cards.map(([label, value, color]) => `
        <div class="card">
          <div class="stat-label">${{label}}</div>
          <div class="stat-value" style="color:${{color}}">${{value}}</div>
        </div>`).join('');
    }}

    function renderBatchTable() {{
      const tbody = document.querySelector('#batch-table tbody');
      tbody.innerHTML = METRICS.batches.map(batch => `
        <tr>
          <td class="mono">${{batch.batch_id.replace('calibration_', '')}}</td>
          <td>${{batch.paper_count}}</td>
          <td>${{batch.high_level_changed}} (${{pct(batch.high_level_change_rate)}})</td>
          <td>${{pct(batch.avg_confidence)}}</td>
          <td>$${{(batch.total_cost || 0).toFixed(2)}}</td>
        </tr>`).join('');
    }}

    function renderReadiness() {{
      const readiness = METRICS.automation_readiness;
      document.getElementById('readiness-list').innerHTML = `
        <div class="muted" style="margin-bottom:12px">${{readiness.completed_count}} / ${{readiness.total_count}} complete · full automation ${{readiness.ready_for_full_automation ? 'ready' : 'blocked pending expert guidelines'}}</div>
        ${{readiness.checklist.map(item => `
          <div class="checklist-item">
            <div>
              <div>${{item.label}}</div>
              <div class="muted" style="font-size:0.82rem;margin-top:4px">${{item.detail}}</div>
            </div>
            ${{badge(item.status)}}
          </div>`).join('')}}`;
    }}

    function renderBoundaries() {{
      const boundaries = METRICS.decision_boundaries || {{}};
      const entries = Object.entries(boundaries);
      if (!entries.length) {{
        document.getElementById('boundaries').innerHTML = '<div class="muted">No decision boundaries encoded yet.</div>';
        return;
      }}
      document.getElementById('boundaries').innerHTML = entries.map(([key, rule]) => `
        <div style="padding:12px 0;border-bottom:1px solid var(--border)">
          <div class="mono" style="color:var(--cyan);margin-bottom:6px">${{key}}</div>
          <div>${{rule.rule || ''}}</div>
          <div class="muted" style="margin-top:6px;font-size:0.82rem">Example: ${{rule.example || '—'}} · Source: ${{rule.source || '—'}}</div>
        </div>`).join('');
    }}

    function renderReviewTable() {{
      const tbody = document.querySelector('#review-table tbody');
      tbody.innerHTML = (METRICS.priority_review || []).map(row => `
        <tr>
          <td class="mono">${{row.paper_id}}<div class="muted">${{row.pmid || ''}}</div></td>
          <td>${{pct(row.confidence)}}${{row.in_review_queue ? ' <span class="badge badge-progress">queue</span>' : ''}}</td>
          <td class="mono">${{row.variant}}</td>
          <td class="mono">${{(row.high_level_fields || []).join(', ')}}</td>
          <td class="paper-title">${{row.title || ''}}${{row.expert_status ? `<div class="muted" style="margin-top:4px">${{row.expert_status}}</div>` : ''}}</td>
        </tr>`).join('');
    }}

    function renderCharts() {{
      const variants = (METRICS.variant_comparison && METRICS.variant_comparison.variants) || [];
      new Chart(document.getElementById('chart-variant-confidence'), {{
        type: 'bar',
        data: {{
          labels: variants.map(v => v.variant),
          datasets: [
            {{
              label: 'Avg Confidence',
              data: variants.map(v => v.avg_confidence || 0),
              backgroundColor: 'rgba(34,211,238,0.5)',
              borderColor: '#22d3ee',
              borderWidth: 1,
              yAxisID: 'y',
            }},
            {{
              label: 'HL Change Rate',
              data: variants.map(v => v.high_level_change_rate || 0),
              backgroundColor: 'rgba(129,140,248,0.45)',
              borderColor: '#818cf8',
              borderWidth: 1,
              yAxisID: 'y1',
            }},
          ],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          scales: {{
            y: {{ beginAtZero: true, max: 1, ticks: {{ callback: v => (v * 100) + '%' }} }},
            y1: {{ beginAtZero: true, max: 1, position: 'right', grid: {{ drawOnChartArea: false }}, ticks: {{ callback: v => (v * 100) + '%' }} }},
          }},
        }},
      }});

      const hlFields = METRICS.field_change_totals.high_level_fields || {{}};
      const labels = Object.keys(hlFields);
      new Chart(document.getElementById('chart-hl-fields'), {{
        type: 'bar',
        data: {{
          labels,
          datasets: [{{
            label: 'Changes',
            data: labels.map(label => hlFields[label]),
            backgroundColor: 'rgba(251,191,36,0.55)',
            borderColor: '#fbbf24',
            borderWidth: 1,
          }}],
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{ legend: {{ display: false }} }},
        }},
      }});
    }}

    renderSummary();
    renderBatchTable();
    renderReadiness();
    renderBoundaries();
    renderReviewTable();
    renderCharts();
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def build_dashboard(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    rules_path: Path = DEFAULT_RULES_PATH,
    confidence_threshold: float = 0.72,
) -> Tuple[Path, Path]:
    """Builds JSON metrics and standalone HTML dashboard artifacts."""
    rules_config = load_rules_config(rules_path)
    metrics = build_dashboard_metrics(
        output_dir=output_dir,
        rules_config=rules_config,
        confidence_threshold=confidence_threshold,
    )
    data_path = write_dashboard_data(metrics, output_dir / "calibration_dashboard_data.json")
    html_path = write_dashboard_html(metrics, output_dir / "dashboard.html")
    return data_path, html_path


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds CLI parser for calibration dashboard generation."""
    parser = argparse.ArgumentParser(description="Build calibration learning dashboard artifacts.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--rules-path", default=str(DEFAULT_RULES_PATH))
    parser.add_argument("--confidence-threshold", type=float, default=0.72)
    parser.add_argument("--build-dashboard", action="store_true", help="Write JSON + HTML dashboard artifacts.")
    parser.add_argument("--print-json", action="store_true", help="Print aggregated metrics JSON to stdout.")
    return parser


def main() -> None:
    """CLI entry point for dashboard generation."""
    parser = build_arg_parser()
    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    if args.build_dashboard:
        data_path, html_path = build_dashboard(
            output_dir=output_dir,
            rules_path=Path(args.rules_path),
            confidence_threshold=args.confidence_threshold,
        )
        print(f"Dashboard data: {data_path}")
        print(f"Dashboard HTML: {html_path}")
        return

    metrics = build_dashboard_metrics(
        output_dir=output_dir,
        rules_config=load_rules_config(Path(args.rules_path)),
        confidence_threshold=args.confidence_threshold,
    )
    if args.print_json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
