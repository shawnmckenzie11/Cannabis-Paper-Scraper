#!/usr/bin/env bash
# Local helper: start Fly Postgres proxy, export DATABASE_URL, run remaining args with venv python.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export FLY_API_TOKEN="${FLY_API_TOKEN:-$(python3 - <<'PY'
from pathlib import Path
import re
text = (Path.home() / ".fly" / "config.yml").read_text()
m = re.search(r"(?m)^access_token:\s*(.+)$", text)
print(m.group(1).strip().strip('"').strip("'"))
PY
)}"

export PATH="$ROOT/venv/bin:$PATH"
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

if ! python3 - <<PY >/dev/null 2>&1
import socket
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int("${PROXY_PORT}")))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
then
  echo "Starting fly proxy on 127.0.0.1:${PROXY_PORT}"
  lsof -ti:"${PROXY_PORT}" | xargs kill -9 2>/dev/null || true
  fly proxy "${PROXY_PORT}:5432" -a cannabis-papers-db > scratch/fly_postgres_proxy.log 2>&1 &
  echo $! > "$PROXY_PID_FILE"
  STARTED_PROXY=1
  sleep 3
fi

export DATABASE_URL="$(
python3 - <<PY
import subprocess
from urllib.parse import quote, unquote, urlparse, urlunparse

port = int("${PROXY_PORT}")
proc = subprocess.run(
    ["fly", "ssh", "console", "-a", "cannabis-paper-scraper", "-C", "printenv DATABASE_URL"],
    capture_output=True, text=True, check=False,
)
lines = [l.strip() for l in proc.stdout.splitlines() if l.strip() and not l.startswith("Connecting to ")]
if not lines:
    raise SystemExit("Could not read DATABASE_URL from Fly")
parsed = urlparse(lines[-1])
username = unquote(parsed.username or "")
password = unquote(parsed.password or "")
auth = quote(username, safe="")
if password:
    auth = f"{auth}:{quote(password, safe='')}"
netloc = f"{auth}@127.0.0.1:{port}"
query = parsed.query
if "sslmode=" not in query:
    query = f"{query}&sslmode=disable" if query else "sslmode=disable"
print(urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, parsed.fragment)))
PY
)"

python3 - <<'PY'
import os
import psycopg2
psycopg2.connect(os.environ["DATABASE_URL"]).close()
print("proxy auth OK")
PY

if [[ "$#" -gt 0 ]]; then
  exec "$@"
fi
