#!/usr/bin/env bash
# Run a bounded sub-node Maude+LLM calibration batch on Fly.io production.
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
SUBNODE="${SUBNODE:-node2b}"
MAX_CALLS="${MAX_CALLS:-40}"
VARIANTS="${VARIANTS:-control}"
PULL_LOCAL="${PULL_LOCAL:-1}"

case "${SUBNODE}" in
  node2a) MODE="node2a_clinical" ;;
  node2b) MODE="node2b_in_vivo" ;;
  node2c) MODE="node2c_in_vitro" ;;
  *)
    echo "Unsupported SUBNODE=${SUBNODE}. Use node2a, node2b, or node2c."
    exit 1
    ;;
esac

echo "==> Pre-flight: ${APP}"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'"

echo "==> Running sub-node calibration: ${SUBNODE} · ${MAX_CALLS} papers · mode=${MODE}"
fly ssh console -a "${APP}" -C \
  "sh -c 'cd /app && python3 - <<\"PY\"
import calibration_coordinator as cc
from calibration_agent import build_arg_parser, run_calibration
parser = build_arg_parser()
args = parser.parse_args([
    \"--max-calls\", \"${MAX_CALLS}\",
    \"--mode\", \"${MODE}\",
    \"--target-subnode\", \"${SUBNODE}\",
    \"--variants\", \"${VARIANTS}\",
    \"--abstract-only\",
])
cc.acquire_lock(\"running_batch\", \"subnode-${SUBNODE}\", subnode=\"${SUBNODE}\")
try:
    paths = run_calibration(args)
    print(\"JSON:\", paths[0])
    print(\"Walkthrough:\", paths[1])
finally:
    cc.release_lock()
PY
python3 calibration_metrics.py --build-dashboard'"

if [[ "${PULL_LOCAL}" == "1" ]]; then
  echo "==> Pulling latest batch artifact to scratch/calibration_runs/"
  mkdir -p scratch/calibration_runs
  LATEST=$(fly ssh console -a "${APP}" -C "sh -c 'ls -t /data/calibration_runs/${SUBNODE}_calibration_*.json 2>/dev/null | head -1'" 2>/dev/null | tr -d '\r')
  if [[ -n "${LATEST}" ]]; then
    BASENAME=$(basename "${LATEST}")
    fly ssh sftp get -a "${APP}" "${LATEST}" "scratch/calibration_runs/${BASENAME}"
    fly ssh sftp get -a "${APP}" "/data/calibration_runs/calibration_dashboard_data.json" "scratch/calibration_runs/calibration_dashboard_data.json" || true
    fly ssh sftp get -a "${APP}" "/data/calibration_runs/dashboard.html" "scratch/calibration_runs/dashboard.html" || true
    echo "Pulled ${BASENAME}"
    python3 calibration_metrics.py --build-dashboard
  fi
fi

echo "==> Done. Dashboard: https://${APP}.fly.dev/calibration/dashboard"
