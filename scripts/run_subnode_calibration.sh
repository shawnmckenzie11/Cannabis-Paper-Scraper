#!/usr/bin/env bash
# Run a bounded sub-node Maude+LLM calibration batch on Fly.io production.
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
SUBNODE="${SUBNODE:-node2b}"
MAX_CALLS="${MAX_CALLS:-20}"
VARIANTS="${VARIANTS:-control}"
PULL_LOCAL="${PULL_LOCAL:-1}"
CONTENT_TIER="${CONTENT_TIER:-pdf_extracted}"
OFFSET="${OFFSET:-0}"
DEPLOY_FIRST="${DEPLOY_FIRST:-1}"

if [[ "${DEPLOY_FIRST}" == "1" ]]; then
  echo "==> Deploying latest classifier code to ${APP} (set DEPLOY_FIRST=0 to skip)"
  fly deploy --remote-only -a "${APP}"
fi

if [[ -f scratch/calibration_runs/handoff_learning_log.json ]]; then
  echo "==> Syncing handoff learning log to ${APP}"
  fly ssh console -a "${APP}" -C "sh -c 'cat > /data/calibration_runs/handoff_learning_log.json'" \
    < scratch/calibration_runs/handoff_learning_log.json || true
fi

case "${SUBNODE}" in
  node2a) MODE="node2a_clinical" ;;
  node2b) MODE="node2b_in_vivo" ;;
  node2c) MODE="node2c_in_vitro" ;;
  *)
    echo "Unsupported SUBNODE=${SUBNODE}. Use node2a, node2b, or node2c."
    exit 1
    ;;
esac

USE_PDF_MAUDE_AB=1
if [[ "${SUBNODE}" == "node2a" || "${SUBNODE}" == "node2b" || "${SUBNODE}" == "node2c" ]]; then
  USE_PDF_MAUDE_AB=1
fi

echo "==> Pre-flight: ${APP}"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'"

if [[ "${USE_PDF_MAUDE_AB}" == "1" ]]; then
  echo "==> Running sub-node PDF Maude A/B: ${SUBNODE} · ${MAX_CALLS} papers · offset=${OFFSET} · tier=${CONTENT_TIER} · mode=${MODE}"
  fly ssh console -a "${APP}" -C \
    "sh -c 'cd /app && python3 - <<\"PY\"
from calibration_agent import build_arg_parser, run_subnode_pdf_maude_ab
parser = build_arg_parser()
args = parser.parse_args([
    \"--subnode-pdf-maude-ab\",
    \"--max-calls\", \"${MAX_CALLS}\",
    \"--offset\", \"${OFFSET}\",
    \"--mode\", \"${MODE}\",
    \"--target-subnode\", \"${SUBNODE}\",
    \"--content-tier\", \"${CONTENT_TIER}\",
    \"--full-extraction\",
    \"--lock-owner\", \"subnode-${SUBNODE}-pdf-o${OFFSET}\",
])
paths = run_subnode_pdf_maude_ab(args)
print(\"JSON:\", paths[0])
print(\"Walkthrough:\", paths[1])
PY
python3 calibration_metrics.py --build-dashboard'"
else
  echo "==> Running sub-node calibration (live Claude): ${SUBNODE} · ${MAX_CALLS} papers · mode=${MODE}"
  fly ssh console -a "${APP}" -C \
    "sh -c 'cd /app && python3 - <<\"PY\"
from calibration_agent import build_arg_parser, run_calibration
parser = build_arg_parser()
args = parser.parse_args([
    \"--max-calls\", \"${MAX_CALLS}\",
    \"--mode\", \"${MODE}\",
    \"--target-subnode\", \"${SUBNODE}\",
    \"--variants\", \"${VARIANTS}\",
    \"--abstract-only\",
    \"--lock-owner\", \"subnode-${SUBNODE}\",
])
paths = run_calibration(args)
print(\"JSON:\", paths[0])
print(\"Walkthrough:\", paths[1])
PY
python3 calibration_metrics.py --build-dashboard'"
fi

if [[ "${PULL_LOCAL}" == "1" ]]; then
  echo "==> Pulling latest batch artifact to scratch/calibration_runs/"
  mkdir -p scratch/calibration_runs
  LATEST=$(fly ssh console -a "${APP}" -C "sh -c 'ls -t /data/calibration_runs/${SUBNODE}_calibration_*.json 2>/dev/null | head -1'" 2>/dev/null | tr -d '\r')
  if [[ -n "${LATEST}" ]]; then
    BASENAME=$(basename "${LATEST}")
    fly ssh sftp get -a "${APP}" "${LATEST}" "scratch/calibration_runs/${BASENAME}"
  rm -f scratch/calibration_runs/calibration_dashboard_data.json scratch/calibration_runs/dashboard.html
  fly ssh sftp get -a "${APP}" "/data/calibration_runs/calibration_dashboard_data.json" \
    "scratch/calibration_runs/calibration_dashboard_data.json" || true
  fly ssh sftp get -a "${APP}" "/data/calibration_runs/dashboard.html" \
    "scratch/calibration_runs/dashboard.html" || true
    echo "Pulled ${BASENAME}"
    python3 calibration_metrics.py --build-dashboard
  fi
fi

echo "==> Done. Dashboard: https://${APP}.fly.dev/calibration/dashboard"

if [[ -n "${LATEST:-}" && "${RUN_FEEDBACK:-1}" == "1" ]]; then
  LOCAL_BATCH="scratch/calibration_runs/${BASENAME}"
  echo "==> RL feedback on ${BASENAME} (LOCAL_FEEDBACK=${LOCAL_FEEDBACK:-1}, no in-cycle PDF refresh)"
  python3 - <<PY || true
from pathlib import Path
import calibration_feedback_agent as cfa
batch = Path("${LOCAL_BATCH}")
local_only = "${LOCAL_FEEDBACK:-1}" == "1"
if batch.exists():
    result = cfa.run_feedback_cycle(batch, skip_lock=True, local_only=local_only, skip_refresh=True)
    print("Feedback status:", result.get("status"))
    if result.get("staged_patch_path"):
        print("Staged patch:", result.get("staged_patch_path"))
    if result.get("agent_handoff_prompt"):
        print("\\n--- Agent handoff prompt ---\\n")
        print(result["agent_handoff_prompt"][:4000])
PY
  echo "==> Implement staged patch, bump calibration_build.py, deploy, then refresh holdout:"
  echo "    python3 calibration_agent.py --refresh-maude-from-batch ${LOCAL_BATCH}"
fi
