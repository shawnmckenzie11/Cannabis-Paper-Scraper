#!/usr/bin/env bash
# Golden endpoint RL cycle: pull → LLM → promote → patch guard → reingest → push.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SQLITE_PATH="${SQLITE_PATH:-cannabis_papers.db}"
ENDPOINT_ID="${ENDPOINT_ID:-}"
ROW_INDEX="${ROW_INDEX:-0}"
PULL="${PULL:-1}"
LLM="${LLM:-1}"
PROMOTE="${PROMOTE:-1}"
FEEDBACK="${FEEDBACK:-1}"
GOLDEN_LOCAL_FEEDBACK="${GOLDEN_LOCAL_FEEDBACK:-0}"
GOLDEN_GUARD="${GOLDEN_GUARD:-1}"
REINGEST="${REINGEST:-1}"
PUSH="${PUSH:-1}"
DRY_RUN_PUSH="${DRY_RUN_PUSH:-0}"
SKIP_PATCH_REQUIRE_PASS="${SKIP_PATCH_REQUIRE_PASS:-0}"
GUARD_ONLY="${GUARD_ONLY:-0}"
ARTIFACT_DIR="${ARTIFACT_DIR:-}"
LOG="${LOG:-scratch/golden_dataset/golden_endpoint_cycle.log}"

mkdir -p "$(dirname "$LOG")"

if [[ "$GUARD_ONLY" == "1" ]]; then
  ARGS=(python3 scripts/golden_endpoint_cycle.py --guard-only)
  if [[ -n "$ENDPOINT_ID" ]]; then
    ARGS+=(--endpoint-id "$ENDPOINT_ID")
  fi
  if [[ -n "$ARTIFACT_DIR" ]]; then
    ARGS+=(--artifact-dir "$ARTIFACT_DIR")
  fi
  echo "guard-only: ${ARGS[*]}" | tee -a "$LOG"
  "${ARGS[@]}" 2>&1 | tee -a "$LOG"
  exit 0
fi

if [[ "$PULL" == "1" || "$PUSH" == "1" ]]; then
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL must be set for pull/push steps." >&2
    exit 1
  fi
fi

SAVED_DATABASE_URL="${DATABASE_URL:-}"

ARGS=(
  python3 scripts/golden_endpoint_cycle.py
  --sqlite-path "$SQLITE_PATH"
)

if [[ -n "$ENDPOINT_ID" ]]; then
  ARGS+=(--endpoint-id "$ENDPOINT_ID")
else
  ARGS+=(--row-index "$ROW_INDEX")
fi

if [[ "$PULL" != "1" ]]; then ARGS+=(--no-pull); fi
if [[ "$LLM" != "1" ]]; then ARGS+=(--no-llm); fi
if [[ "$PROMOTE" != "1" ]]; then ARGS+=(--no-promote); fi
if [[ "$FEEDBACK" != "1" ]]; then ARGS+=(--no-feedback); fi
if [[ "$GOLDEN_GUARD" != "1" ]]; then ARGS+=(--no-golden-guard); fi
if [[ "$REINGEST" != "1" ]]; then ARGS+=(--no-reingest); fi
if [[ "$PUSH" != "1" ]]; then ARGS+=(--no-push); fi
if [[ "$DRY_RUN_PUSH" == "1" ]]; then ARGS+=(--dry-run-push); fi
if [[ "$SKIP_PATCH_REQUIRE_PASS" == "1" ]]; then ARGS+=(--skip-patch-require-pass); fi

if [[ "${BOOTSTRAP_LLM:-0}" == "1" ]]; then ARGS+=(--bootstrap-llm); fi
if [[ "$GOLDEN_LOCAL_FEEDBACK" == "1" ]]; then ARGS+=(--local-feedback); fi

echo "=== golden endpoint cycle started $(date -Iseconds) ===" | tee -a "$LOG"
echo "cycle: ${ARGS[*]}" | tee -a "$LOG"

if [[ "$PULL" == "1" ]]; then
  export DATABASE_URL="$SAVED_DATABASE_URL"
fi

"${ARGS[@]}" 2>&1 | tee -a "$LOG"
echo "=== golden endpoint cycle finished $(date -Iseconds) ===" | tee -a "$LOG"
