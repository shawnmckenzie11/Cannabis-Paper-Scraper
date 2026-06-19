#!/usr/bin/env bash
# Run a bounded Node 1 Maude+LLM calibration batch on Fly.io production (default: 40 papers).
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
MAX_CALLS="${MAX_CALLS:-40}"
VARIANTS="${VARIANTS:-control}"
MODE="${MODE:-node1_routing}"
PULL_LOCAL="${PULL_LOCAL:-1}"

echo "==> Pre-flight: ${APP}"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'"

echo "==> Running calibration: ${MAX_CALLS} papers · mode=${MODE} · variants=${VARIANTS}"
fly ssh console -a "${APP}" -C \
  "sh -c 'cd /app && python3 calibration_agent.py --max-calls ${MAX_CALLS} --mode ${MODE} --variants ${VARIANTS} --abstract-only && python3 calibration_metrics.py --build-dashboard'"

if [[ "${PULL_LOCAL}" == "1" ]]; then
  echo "==> Pulling latest batch artifact to scratch/calibration_runs/"
  mkdir -p scratch/calibration_runs
  LATEST=$(fly ssh console -a "${APP}" -C "sh -c 'ls -t /data/calibration_runs/node1_calibration_*.json | head -1'" 2>/dev/null | tr -d '\r')
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
