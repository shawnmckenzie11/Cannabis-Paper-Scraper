#!/usr/bin/env bash
# Run manual expert-edit RL cycle: detect drawer corrections → patch → cues → version/confidence bump.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SQLITE_PATH="${SQLITE_PATH:-cannabis_papers.db}"
SINCE="${SINCE:-last-cycle}"
OUTPUT_DIR="${OUTPUT_DIR:-scratch/manual_edit_runs}"
LOG="${LOG:-scratch/manual_edit_runs/manual_edit_cycle.log}"

mkdir -p "$(dirname "$LOG")" "$OUTPUT_DIR"

ARGS=(
  python3 manual_edit_cycle.py
  --sqlite-path "$SQLITE_PATH"
  --since "$SINCE"
  --output-dir "$OUTPUT_DIR"
)

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  ARGS+=(--dry-run)
fi
if [[ "${NO_CUES:-0}" == "1" ]]; then
  ARGS+=(--no-cues)
fi
if [[ "${NO_VERSION_BUMP:-0}" == "1" ]]; then
  ARGS+=(--no-version-bump)
fi
if [[ -n "${PAPER_ID:-}" ]]; then
  ARGS+=(--paper-id "$PAPER_ID")
fi

echo "=== manual edit cycle started $(date -Iseconds) ===" | tee -a "$LOG"
echo "run: ${ARGS[*]}" | tee -a "$LOG"
"${ARGS[@]}" 2>&1 | tee -a "$LOG"
echo "=== manual edit cycle finished $(date -Iseconds) ===" | tee -a "$LOG"
