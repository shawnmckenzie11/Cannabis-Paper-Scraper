#!/usr/bin/env bash
# Full golden-endpoint automation for one table row (see golden-endpoint-row-runner agent).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ROW_INDEX="${ROW_INDEX:-}"
ENDPOINT_ID="${ENDPOINT_ID:-}"
export GOLDEN_HANDOFF_CLAUDE="${GOLDEN_HANDOFF_CLAUDE:-0}"
export GOLDEN_FULL_SUBNODE_REINGEST="${GOLDEN_FULL_SUBNODE_REINGEST:-1}"

ARGS=(python3 scripts/golden_endpoint_automate_row.py)
if [[ -n "$ROW_INDEX" ]]; then
  ARGS+=(--row-index "$ROW_INDEX")
fi
if [[ -n "$ENDPOINT_ID" ]]; then
  ARGS+=(--endpoint-id "$ENDPOINT_ID")
fi
if [[ "${NO_PULL:-0}" == "1" ]]; then ARGS+=(--no-pull); fi
if [[ "${SKIP_PUSH:-0}" == "1" || "${NO_PUSH:-0}" == "1" ]]; then ARGS+=(--no-push); fi
if [[ "${NO_FLY_PROXY:-0}" == "1" ]]; then ARGS+=(--no-fly-proxy); fi
if [[ "${AUTO_ADVANCE:-0}" == "1" ]]; then ARGS+=(--auto-advance); fi
if [[ "${SKIP_PRIOR_GUARD_CHECK:-0}" == "1" ]]; then ARGS+=(--skip-prior-guard-check); fi
if [[ -n "${MIN_POOL_SIZE:-}" ]]; then ARGS+=(--min-pool-size "$MIN_POOL_SIZE"); fi
if [[ "${SKIP_COMPLETED:-0}" == "1" ]]; then ARGS+=(--skip-completed); fi

exec "${ARGS[@]}"
