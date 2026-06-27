#!/usr/bin/env bash
# Local-first Maude cycle: pull Postgres → SQLite reingest → push classification deltas.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SQLITE_PATH="${SQLITE_PATH:-cannabis_papers.db}"
PULL="${PULL:-1}"
REINGEST="${REINGEST:-1}"
PUSH="${PUSH:-1}"
DRY_RUN_PUSH="${DRY_RUN_PUSH:-0}"
REINGEST_ONLY_PULL="${REINGEST_ONLY_PULL:-1}"
SKIP_PULL_INIT="${SKIP_PULL_INIT:-0}"
BATCH_SIZE="${BATCH_SIZE:-50}"
WORKERS="${WORKERS:-4}"
WORKERS_FAST="${WORKERS_FAST:-4}"
PREWARM_CACHE="${PREWARM_CACHE:-1}"
REFRESH_CONFIDENCE="${REFRESH_CONFIDENCE:-1}"
PUSH_BATCH_SIZE="${PUSH_BATCH_SIZE:-25}"
PUSH_BATCH_PAUSE="${PUSH_BATCH_PAUSE:-0.15}"
LOG="${LOG:-scratch/local_reingest_cycle.log}"

mkdir -p "$(dirname "$LOG")"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL must be set for pull/push steps." >&2
  exit 1
fi

SAVED_DATABASE_URL="$DATABASE_URL"

echo "=== local reingest cycle started $(date -Iseconds) ===" | tee -a "$LOG"

if [[ "$PULL" == "1" ]]; then
  PULL_ARGS=(python3 scripts/pull_papers_from_postgres.py --sqlite-path "$SQLITE_PATH" --batch-size 500)
  if [[ "$REINGEST_ONLY_PULL" == "1" ]]; then
    PULL_ARGS+=(--reingest-only)
  fi
  if [[ "$SKIP_PULL_INIT" == "1" ]]; then
    PULL_ARGS+=(--skip-init)
  fi
  echo "pull: ${PULL_ARGS[*]}" | tee -a "$LOG"
  "${PULL_ARGS[@]}" 2>&1 | tee -a "$LOG"
fi

if [[ "$REINGEST" == "1" ]]; then
  unset DATABASE_URL
  export PAPER_TEXT_CACHE_DIR="${PAPER_TEXT_CACHE_DIR:-scratch/paper_cache}"
  REINGEST_ARGS=(
    python3 reingest_heuristic_papers.py
    --pass two-pass
    --maude-and-heuristic
    --batch-size "$BATCH_SIZE"
    --workers "$WORKERS"
    --workers-fast "$WORKERS_FAST"
  )
  if [[ "$PREWARM_CACHE" != "1" ]]; then
    REINGEST_ARGS+=(--no-prewarm-cache)
  fi
  if [[ "$REFRESH_CONFIDENCE" == "1" ]]; then
    REINGEST_ARGS+=(--refresh-maude-confidence)
  fi
  echo "reingest (sqlite): ${REINGEST_ARGS[*]}" | tee -a "$LOG"
  "${REINGEST_ARGS[@]}" 2>&1 | tee -a "$LOG"
fi

if [[ "$PUSH" == "1" ]]; then
  export DATABASE_URL="$SAVED_DATABASE_URL"
  if [[ "${RESILIENT_PUSH:-0}" == "1" ]]; then
    echo "push: scripts/run_resilient_push.sh (RESILIENT_PUSH=1)" | tee -a "$LOG"
    RESILIENT_PUSH=1 SQLITE_PATH="$SQLITE_PATH" LOG="$LOG" \
      PUSH_BATCH_SIZE="$PUSH_BATCH_SIZE" \
      PUSH_BATCH_PAUSE="$PUSH_BATCH_PAUSE" \
      bash scripts/run_resilient_push.sh 2>&1 | tee -a "$LOG"
  else
    PUSH_ARGS=(
      python3 scripts/push_classification_deltas.py
      --sqlite-path "$SQLITE_PATH"
      --batch-size "$PUSH_BATCH_SIZE"
      --batch-pause-seconds "$PUSH_BATCH_PAUSE"
      --stall-seconds "${PUSH_STALL_SECONDS:-90}"
      --commit-timeout-seconds "${PUSH_COMMIT_TIMEOUT_SECONDS:-60}"
      --statement-timeout-ms "${PUSH_STATEMENT_TIMEOUT_MS:-30000}"
    )
    if [[ "$DRY_RUN_PUSH" == "1" ]]; then
      PUSH_ARGS+=(--dry-run)
    fi
    echo "push: ${PUSH_ARGS[*]}" | tee -a "$LOG"
    "${PUSH_ARGS[@]}" 2>&1 | tee -a "$LOG"
  fi
fi

echo "=== local reingest cycle finished $(date -Iseconds) ===" | tee -a "$LOG"
