#!/usr/bin/env bash
# Archive calibration batch artifacts and rebuild an empty RL progress dashboard.
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
OUTPUT_DIR="${OUTPUT_DIR:-}"
LABEL="${LABEL:-}"

if [[ "${1:-local}" == "fly" ]]; then
  echo "==> Resetting calibration dashboard on Fly: ${APP}"
  fly ssh console -a "${APP}" -C \
    "sh -c 'cd /app && python3 calibration_reset.py --label fly_${LABEL:-reset} && python3 calibration_metrics.py --build-dashboard'"
  echo "==> Done. Dashboard: https://${APP}.fly.dev/calibration/dashboard"
  exit 0
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ARGS=(python3 calibration_reset.py)
if [[ -n "${OUTPUT_DIR}" ]]; then
  ARGS+=(--output-dir "${OUTPUT_DIR}")
fi
if [[ -n "${LABEL}" ]]; then
  ARGS+=(--label "${LABEL}")
fi

echo "==> Archiving calibration artifacts and rebuilding empty RL dashboard"
"${ARGS[@]}"
echo "==> Open scratch/calibration_runs/dashboard.html locally, or deploy and use /calibration/dashboard on Fly."
