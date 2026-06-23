#!/usr/bin/env bash
# Nightly two-pass Maude reclassification for non-LLM original-research papers.
# Fast pass: abstract-only over full queue (~hours).
# Slow pass: PDF/PMC + disk cache, parallel workers (~subset overnight).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BATCH_SIZE="${BATCH_SIZE:-50}"
WORKERS="${WORKERS:-4}"
LOG="${LOG:-/data/maude_nightly_reclassify.log}"
REFRESH="${REFRESH:-1}"

ARGS=(
  python3 reingest_heuristic_papers.py
  --pass two-pass
  --maude-and-heuristic
  --batch-size "$BATCH_SIZE"
  --workers "$WORKERS"
)

if [[ "$REFRESH" == "1" ]]; then
  ARGS+=(--refresh-maude-confidence)
fi

echo "=== maude nightly two-pass started $(date -Iseconds) ===" | tee -a "$LOG"
echo "cmd: ${ARGS[*]}" | tee -a "$LOG"
"${ARGS[@]}" 2>&1 | tee -a "$LOG"
echo "=== finished $(date -Iseconds) ===" | tee -a "$LOG"
