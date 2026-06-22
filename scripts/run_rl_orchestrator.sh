#!/usr/bin/env bash
# Run the sub-node Maude RL orchestrator on Fly.io production.
# By default deploys latest classifier code first (DEPLOY_FIRST=1).
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
SUBNODE="${SUBNODE:-node2b}"
MAX_CALLS="${MAX_CALLS:-20}"
MAX_CYCLES="${MAX_CYCLES:-3}"
CONTENT_TIER="${CONTENT_TIER:-pdf_extracted}"
PULL_LOCAL="${PULL_LOCAL:-1}"
DEPLOY_FIRST="${DEPLOY_FIRST:-1}"

if [[ "${DEPLOY_FIRST}" == "1" ]]; then
  echo "==> Deploying latest Maude/classifier code to ${APP} (set DEPLOY_FIRST=0 to skip)"
  fly deploy --remote-only -a "${APP}"
fi

echo "==> Pre-flight: ${APP}"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'"

echo "==> RL orchestrator: ${SUBNODE} · ${MAX_CALLS} papers/cycle · ${MAX_CYCLES} cycles · tier=${CONTENT_TIER}"
fly ssh console -a "${APP}" -C \
  "sh -c 'cd /app && SUBNODE=${SUBNODE} MAX_CALLS=${MAX_CALLS} MAX_CYCLES=${MAX_CYCLES} CONTENT_TIER=${CONTENT_TIER} python3 fly_run_rl_orchestrator.py'"

if [[ "${PULL_LOCAL}" == "1" ]]; then
  echo "==> Pulling dashboard artifacts"
  mkdir -p scratch/calibration_runs
  rm -f scratch/calibration_runs/calibration_dashboard_data.json scratch/calibration_runs/dashboard.html
  fly ssh sftp get -a "${APP}" "/data/calibration_runs/calibration_dashboard_data.json" \
    "scratch/calibration_runs/calibration_dashboard_data.json" || true
  fly ssh sftp get -a "${APP}" "/data/calibration_runs/dashboard.html" \
    "scratch/calibration_runs/dashboard.html" || true
  LATEST=$(fly ssh console -a "${APP}" -C "sh -c 'ls -t /data/calibration_runs/${SUBNODE}_calibration_*.json 2>/dev/null | head -1'" 2>/dev/null | tr -d '\r')
  if [[ -n "${LATEST}" ]]; then
    fly ssh sftp get -a "${APP}" "${LATEST}" "scratch/calibration_runs/$(basename "${LATEST}")" || true
  fi
  python3 calibration_metrics.py --build-dashboard 2>/dev/null || true
fi

echo "==> Done. Dashboard: https://${APP}.fly.dev/calibration/dashboard"
