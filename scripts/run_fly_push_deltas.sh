#!/usr/bin/env bash
# Push local classification deltas via compact JSONL (fits Fly /data volume).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="${FLY_APP:-cannabis-paper-scraper}"
SQLITE_PATH="${SQLITE_PATH:-cannabis_papers.db}"
DELTA_JSONL="${DELTA_JSONL:-scratch/classification_deltas.jsonl}"
REMOTE_JSONL="${REMOTE_JSONL:-/data/classification_deltas.jsonl}"
PUSH_BATCH_SIZE="${PUSH_BATCH_SIZE:-25}"
PUSH_BATCH_PAUSE="${PUSH_BATCH_PAUSE:-0.25}"
LOG="${LOG:-scratch/local_reingest_cycle.log}"

if [[ ! -f "$SQLITE_PATH" ]]; then
  echo "ERROR: missing $SQLITE_PATH" >&2
  exit 1
fi

echo "=== fly push deltas preflight $(date -Iseconds) ===" | tee -a "$LOG"
if ! fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 fly_db_check.py'" 2>&1 | tee -a "$LOG"; then
  echo "ERROR: Postgres is not healthy. Aborting." | tee -a "$LOG"
  exit 1
fi

echo "Cleaning partial uploads on Fly /data..." | tee -a "$LOG"
fly ssh console -a "${APP}" -C "sh -c 'rm -f /data/local_push_source.db /data/classification_deltas.jsonl /data/apply_classification_deltas.py /data/push_resilience.py /data/local_sync.py'" 2>&1 | tee -a "$LOG" || true

echo "Exporting compact deltas locally..." | tee -a "$LOG"
python3 scripts/export_classification_deltas.py \
  --sqlite-path "$SQLITE_PATH" \
  --output "$DELTA_JSONL" 2>&1 | tee -a "$LOG"

echo "Uploading ${DELTA_JSONL} -> ${REMOTE_JSONL}..." | tee -a "$LOG"
fly ssh sftp put -a "${APP}" "${DELTA_JSONL}" "${REMOTE_JSONL}" 2>&1 | tee -a "$LOG"
fly ssh sftp put -a "${APP}" "local_sync.py" "/data/local_sync.py" 2>&1 | tee -a "$LOG"
fly ssh sftp put -a "${APP}" "scripts/apply_classification_deltas.py" "/data/apply_classification_deltas.py" 2>&1 | tee -a "$LOG"
fly ssh sftp put -a "${APP}" "push_resilience.py" "/data/push_resilience.py" 2>&1 | tee -a "$LOG"

echo "Applying deltas on Fly..." | tee -a "$LOG"
fly ssh console -a "${APP}" -C \
  "sh -c 'cd /app && PYTHONPATH=/data:/app python3 /data/apply_classification_deltas.py ${REMOTE_JSONL} --batch-size ${PUSH_BATCH_SIZE} --batch-pause-seconds ${PUSH_BATCH_PAUSE}'" \
  2>&1 | tee -a "$LOG"

echo "Backfilling indexed tab flags on Fly..." | tee -a "$LOG"
fly ssh console -a "${APP}" -C "sh -c 'cd /app && python3 ensure_tab_flags.py'" 2>&1 | tee -a "$LOG" || {
  echo "WARN: tab flag backfill did not complete cleanly" | tee -a "$LOG"
}

echo "=== fly push deltas finished $(date -Iseconds) ===" | tee -a "$LOG"
