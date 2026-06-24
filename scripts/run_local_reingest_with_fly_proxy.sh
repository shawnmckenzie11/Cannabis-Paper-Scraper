#!/usr/bin/env bash
# Start fly proxy (if needed) and run the local reingest cycle against production Postgres.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROXY_PORT="${PROXY_PORT:-15432}"
PROXY_PID_FILE="${PROXY_PID_FILE:-scratch/fly_postgres_proxy.pid}"
STARTED_PROXY=0

cleanup() {
  if [[ "$STARTED_PROXY" == "1" && -f "$PROXY_PID_FILE" ]]; then
    kill "$(cat "$PROXY_PID_FILE")" 2>/dev/null || true
    rm -f "$PROXY_PID_FILE"
  fi
}
trap cleanup EXIT

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

exec bash scripts/run_local_reingest_cycle.sh
