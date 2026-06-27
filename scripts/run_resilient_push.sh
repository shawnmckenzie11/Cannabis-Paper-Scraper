#!/usr/bin/env bash
# Resilient delta push: fly proxy + stall-aware push with auto-resume until complete.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROXY_PORT="${PROXY_PORT:-15432}"
PROXY_PID_FILE="${PROXY_PID_FILE:-scratch/fly_postgres_proxy.pid}"
LOG="${LOG:-scratch/local_reingest_cycle.log}"
SQLITE_PATH="${SQLITE_PATH:-cannabis_papers.db}"
PUSH_BATCH_SIZE="${PUSH_BATCH_SIZE:-10}"
PUSH_BATCH_PAUSE="${PUSH_BATCH_PAUSE:-0.5}"
PUSH_STALL_SECONDS="${PUSH_STALL_SECONDS:-90}"
PUSH_COMMIT_TIMEOUT_SECONDS="${PUSH_COMMIT_TIMEOUT_SECONDS:-60}"
PUSH_STATEMENT_TIMEOUT_MS="${PUSH_STATEMENT_TIMEOUT_MS:-30000}"
MAX_PUSH_ATTEMPTS="${MAX_PUSH_ATTEMPTS:-200}"
RETRY_SLEEP_SECONDS="${RETRY_SLEEP_SECONDS:-45}"
STALL_RETRY_SLEEP_SECONDS="${STALL_RETRY_SLEEP_SECONDS:-30}"
STARTED_PROXY=0

cleanup() {
  if [[ "$STARTED_PROXY" == "1" && -f "$PROXY_PID_FILE" ]]; then
    kill "$(cat "$PROXY_PID_FILE")" 2>/dev/null || true
    rm -f "$PROXY_PID_FILE"
  fi
}
trap cleanup EXIT

mkdir -p scratch "$(dirname "$LOG")"

if ! python3 - <<'PY' >/dev/null 2>&1
import os, socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(os.environ.get("PROXY_PORT", "15432"))))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
then
  echo "Starting fly proxy on 127.0.0.1:${PROXY_PORT} -> cannabis-papers-db:5432"
  fly proxy "${PROXY_PORT}:5432" -a cannabis-papers-db >/dev/null 2>&1 &
  echo $! > "$PROXY_PID_FILE"
  STARTED_PROXY=1
  sleep 2
fi

export DATABASE_URL="$(
python3 <<'PY'
import os
import subprocess
import sys
from urllib.parse import quote, unquote, urlparse, urlunparse

port = int(os.environ.get("PROXY_PORT", "15432"))
proc = subprocess.run(
    [
        "fly", "ssh", "console",
        "-a", "cannabis-paper-scraper",
        "-C", "printenv DATABASE_URL",
    ],
    capture_output=True,
    text=True,
    check=False,
)
if proc.returncode != 0:
    sys.stderr.write(proc.stderr or proc.stdout)
    raise SystemExit(proc.returncode)
lines = [
    line.strip()
    for line in proc.stdout.splitlines()
    if line.strip() and not line.startswith("Connecting to ")
]
if not lines:
    raise SystemExit("Could not read DATABASE_URL from Fly")
url = lines[-1]
parsed = urlparse(url)
username = unquote(parsed.username or "")
password = unquote(parsed.password or "")
auth = quote(username, safe="")
if password:
    auth = f"{auth}:{quote(password, safe='')}"
netloc = f"{auth}@127.0.0.1:{port}" if auth else f"127.0.0.1:{port}"
query = parsed.query
if "sslmode=" not in query:
    query = f"{query}&sslmode=disable" if query else "sslmode=disable"
print(urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, parsed.fragment)))
PY
)"

export PUSH_STALL_SECONDS PUSH_COMMIT_TIMEOUT_SECONDS PUSH_STATEMENT_TIMEOUT_MS

attempt=0
while [[ "$attempt" -lt "$MAX_PUSH_ATTEMPTS" ]]; do
  attempt=$((attempt + 1))
  echo "=== resilient push attempt ${attempt}/$MAX_PUSH_ATTEMPTS $(date -Iseconds) ===" | tee -a "$LOG"

  set +e
  python3 scripts/push_classification_deltas.py \
    --sqlite-path "$SQLITE_PATH" \
    --batch-size "$PUSH_BATCH_SIZE" \
    --batch-pause-seconds "$PUSH_BATCH_PAUSE" \
    --stall-seconds "$PUSH_STALL_SECONDS" \
    --commit-timeout-seconds "$PUSH_COMMIT_TIMEOUT_SECONDS" \
    --statement-timeout-ms "$PUSH_STATEMENT_TIMEOUT_MS" \
    2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}
  set -e

  if [[ "$code" -eq 0 ]]; then
    echo "=== resilient push complete on attempt ${attempt} $(date -Iseconds) ===" | tee -a "$LOG"
    exit 0
  fi

  if [[ "$code" -eq 2 ]]; then
    echo "=== push stalled or partial (exit 2); retrying in ${STALL_RETRY_SLEEP_SECONDS}s ===" | tee -a "$LOG"
    sleep "$STALL_RETRY_SLEEP_SECONDS"
    continue
  fi

  echo "=== push failed (exit ${code}); retrying in ${RETRY_SLEEP_SECONDS}s ===" | tee -a "$LOG"
  sleep "$RETRY_SLEEP_SECONDS"
done

echo "ERROR: resilient push exceeded ${MAX_PUSH_ATTEMPTS} attempts" | tee -a "$LOG"
exit 1
