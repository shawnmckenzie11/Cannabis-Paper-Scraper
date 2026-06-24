#!/usr/bin/env bash
# Nightly two-pass Maude reclassification for non-LLM original-research papers.
# Fast pass: abstract-only over full queue (~hours).
# Slow pass: PDF/PMC + disk cache, parallel workers (~subset overnight).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH_SIZE="${BATCH_SIZE:-25}"
WORKERS="${WORKERS:-1}"
WORKERS_FAST="${WORKERS_FAST:-2}"
PREWARM_CACHE="${PREWARM_CACHE:-1}"
REINGEST_BATCH_PAUSE_SECONDS="${REINGEST_BATCH_PAUSE_SECONDS:-0.15}"
LOG="${LOG:-/data/maude_nightly_reclassify.log}"
REFRESH="${REFRESH:-1}"

ARGS=(
  python3 reingest_heuristic_papers.py
  --pass two-pass
  --maude-and-heuristic
  --batch-size "$BATCH_SIZE"
  --workers "$WORKERS"
  --workers-fast "$WORKERS_FAST"
)

if [[ "$PREWARM_CACHE" == "1" ]]; then
  : # prewarm is default inside run_two_pass_reingest
else
  ARGS+=(--no-prewarm-cache)
fi

if [[ "$REFRESH" == "1" ]]; then
  ARGS+=(--refresh-maude-confidence)
fi

echo "=== maude nightly two-pass started $(date -Iseconds) ===" | tee -a "$LOG"
echo "cmd: REINGEST_BATCH_PAUSE_SECONDS=$REINGEST_BATCH_PAUSE_SECONDS ${ARGS[*]}" | tee -a "$LOG"
export REINGEST_BATCH_PAUSE_SECONDS
"${ARGS[@]}" 2>&1 | tee -a "$LOG"
echo "=== finished $(date -Iseconds) ===" | tee -a "$LOG"
