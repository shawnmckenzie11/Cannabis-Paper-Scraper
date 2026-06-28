#!/usr/bin/env bash
# Loop A finish: subnode reingest + blast-radius + optional cohort validation.
# Run AFTER implement → deploy → refresh-maude-from-batch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SUBNODE="${SUBNODE:-node2b}"
PATCH_ID="${PATCH_ID:-$(grep MAUDE_CLASSIFIER_BUILD_ID calibration_build.py | cut -d'"' -f2)}"
SQLITE_PATH="${DATABASE_PATH:-cannabis_papers.db}"
ENDPOINT_ID="${ENDPOINT_ID:-}"
PUSH="${PUSH:-0}"
DRY_RUN="${DRY_RUN:-0}"

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "==> Preflight: sync feedback_audit from Postgres → local SQLite"
  python3 - <<PY
from feedback_audit_sync import sync_feedback_audit_from_postgres
import json
print(json.dumps(sync_feedback_audit_from_postgres("${SQLITE_PATH}"), indent=2))
PY
fi

echo "==> Loop A finish: subnode=${SUBNODE} patch_id=${PATCH_ID}"

REINGEST_ARGS=(
  python3 reingest_heuristic_papers.py
  --pass two-pass
  --no-skip-current
  --scope-subnode "$SUBNODE"
  --batch-size "${REINGEST_BATCH_SIZE:-50}"
  --workers "${REINGEST_WORKERS:-4}"
  --refresh-maude-confidence
)
if [[ "$DRY_RUN" == "1" ]]; then
  REINGEST_ARGS+=(--dry-run)
fi

"${REINGEST_ARGS[@]}" | tee "scratch/patch_reports/calibration_a/${PATCH_ID}_reingest.log"

REINGEST_JSON="scratch/patch_reports/calibration_a/${PATCH_ID}/reingest_summary.json"
mkdir -p "scratch/patch_reports/calibration_a/${PATCH_ID}"
python3 - <<PY
import json, re, sys
from pathlib import Path
log = Path("scratch/patch_reports/calibration_a/${PATCH_ID}_reingest.log").read_text(encoding="utf-8")
summary = {}
for line in log.splitlines():
    if line.startswith("GOLDEN_REINGEST_SUMMARY="):
        summary = json.loads(line.split("=", 1)[1])
        break
    if line.strip().startswith("{") and "field_change_counts" in line:
        try:
            summary = json.loads(line)
        except json.JSONDecodeError:
            pass
if not summary:
    m = re.search(r"Maude re-ingestion complete: (\{.*\})", log)
    if m:
        import ast
        summary = ast.literal_eval(m.group(1))
Path("${REINGEST_JSON}").write_text(json.dumps(summary, indent=2), encoding="utf-8")
if not summary.get("field_change_counts") and not summary.get("papers_processed"):
    sys.exit("Could not parse reingest summary from log")
PY

PUSH_JSON=""
if [[ "$PUSH" == "1" ]]; then
  python3 scripts/push_classification_deltas.py --sqlite-path "$SQLITE_PATH" | tee "scratch/patch_reports/calibration_a/${PATCH_ID}_push.log"
  PUSH_JSON="scratch/patch_reports/calibration_a/${PATCH_ID}/push_summary.json"
  python3 - <<PY
import json, re
from pathlib import Path
log = Path("scratch/patch_reports/calibration_a/${PATCH_ID}_push.log").read_text(encoding="utf-8")
summary = {}
m = re.search(r"Found (\d+) classification delta", log)
if m:
    summary["delta_count"] = int(m.group(1))
    summary["papers_pushed"] = int(m.group(1))
Path("${PUSH_JSON}").write_text(json.dumps(summary, indent=2), encoding="utf-8")
PY
fi

COHORT_ARGS=()
if [[ -n "$ENDPOINT_ID" ]]; then
  python3 similarity_cohort_validation.py \
    --endpoint-id "$ENDPOINT_ID" \
    --loop-type calibration_a \
    --patch-id "$PATCH_ID" \
    --sqlite-path "$SQLITE_PATH" \
    --scope-subnode "$SUBNODE"
fi

BLAST_ARGS=(
  python3 patch_blast_radius.py
  --loop-type calibration_a
  --patch-id "$PATCH_ID"
  --scope-subnode "$SUBNODE"
  --reingest-json "$REINGEST_JSON"
)
if [[ -n "$ENDPOINT_ID" ]]; then
  BLAST_ARGS+=(--endpoint-id "$ENDPOINT_ID")
  BLAST_ARGS+=(--cohort-json "scratch/patch_reports/calibration_a/${PATCH_ID}/cohort_validation.json")
fi
if [[ -n "$PUSH_JSON" && -f "$PUSH_JSON" ]]; then
  BLAST_ARGS+=(--push-json "$PUSH_JSON")
fi
"${BLAST_ARGS[@]}"

python3 - <<PY
import json
from datetime import datetime
from pathlib import Path
import handoff_learning_log

patch_id = "${PATCH_ID}"
subnode = "${SUBNODE}"
blast = json.loads(Path(f"scratch/patch_reports/calibration_a/{patch_id}/blast_radius.json").read_text())
notes = [
    f"Loop A finish: reingested subnode {subnode} after deploy.",
    f"Papers scanned {blast.get('papers_scanned')}, changed {blast.get('papers_changed')}, pushed {blast.get('papers_pushed') or '—'}.",
    f"Blast-radius report: scratch/patch_reports/calibration_a/{patch_id}/blast_radius.html",
]
cohort = blast.get("cohort_validation") or {}
if cohort:
    notes.append(
        f"Cohort routing match {cohort.get('cohort_routing_match_before')} → {cohort.get('cohort_routing_match_after')} (Δ {cohort.get('cohort_routing_delta')})."
    )
while len(notes) < 3:
    notes.append("Review per-field change table in blast-radius HTML.")
handoff_learning_log.append_handoff_entry({
    "entry_type": "loop_a_finish",
    "source_subnode": subnode,
    "beneficiary_nodes": [subnode],
    "summary_title": f"Loop A finish blast-radius ({patch_id})",
    "blast_radius_report_path": f"scratch/patch_reports/calibration_a/{patch_id}/blast_radius.html",
    "blast_radius_json": f"scratch/patch_reports/calibration_a/{patch_id}/blast_radius.json",
    "papers_scanned": blast.get("papers_scanned"),
    "papers_changed": blast.get("papers_changed"),
    "papers_pushed": blast.get("papers_pushed"),
    "learning_notes": notes[:8],
})
print("Appended loop A finish to handoff_learning_log.json")
PY

echo "==> Loop A finish complete: scratch/patch_reports/calibration_a/${PATCH_ID}/blast_radius.html"
