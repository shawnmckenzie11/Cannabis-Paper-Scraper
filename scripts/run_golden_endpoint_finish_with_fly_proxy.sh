#!/usr/bin/env bash
# Loop B finish (reingest + blast + push) with Fly Postgres proxy + feedback_audit preflight.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ROW_INDEX="${ROW_INDEX:-}"
ENDPOINT_ID="${ENDPOINT_ID:-}"
ARGS=(python3 scripts/golden_endpoint_automate_row.py --finish)
if [[ -n "$ROW_INDEX" ]]; then
  ARGS+=(--row-index "$ROW_INDEX")
fi
if [[ -n "$ENDPOINT_ID" ]]; then
  ARGS+=(--endpoint-id "$ENDPOINT_ID")
fi
if [[ "${NO_PUSH:-0}" == "1" || "${SKIP_PUSH:-0}" == "1" ]]; then
  ARGS+=(--no-push)
fi

exec bash scripts/run_golden_endpoint_with_fly_proxy.sh "${ARGS[@]}"
