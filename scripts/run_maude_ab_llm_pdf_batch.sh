#!/usr/bin/env bash
# Pair all llm-pdf-reclassify papers with Maude on Fly.io (no Claude API calls).
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
MAX_CALLS="${MAX_CALLS:-586}"
OFFSET="${OFFSET:-0}"
PULL_LOCAL="${PULL_LOCAL:-1}"

echo "==> Pre-flight: ${APP}"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'"

echo "==> Maude A/B pairing for llm-pdf-reclassify papers: max=${MAX_CALLS} offset=${OFFSET}"
fly ssh console -a "${APP}" -C \
  "sh -c 'cd /app && python3 calibration_agent.py --maude-from-llm-pdf --max-calls ${MAX_CALLS} --offset ${OFFSET} --fetch-limit ${MAX_CALLS} && python3 calibration_metrics.py --build-dashboard'"

if [[ "${PULL_LOCAL}" == "1" ]]; then
  echo "==> Pulling latest llm_pdf_maude_ab artifact to scratch/calibration_runs/"
  mkdir -p scratch/calibration_runs
  LATEST=$(fly ssh console -a "${APP}" -C "sh -c 'ls -t /data/calibration_runs/llm_pdf_maude_ab_*.json | head -1'" 2>/dev/null | tr -d '\r')
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
