#!/bin/bash
# Serve the dashboard from local SQLite. Never inherit a leftover Fly DATABASE_URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PYTHON="${ROOT}/venv/bin/python"
GUNICORN="${ROOT}/venv/bin/gunicorn"
DB_PATH="${DATABASE_PATH:-${ROOT}/cannabis_papers.db}"
BIND="${MACOS_SITE_BIND:-127.0.0.1:8080}"
LOG_DIR="${ROOT}/logs"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing venv python at ${PYTHON}. Create it with: python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if [[ ! -x "${GUNICORN}" ]]; then
  echo "Missing gunicorn at ${GUNICORN}. Install deps: venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if [[ ! -f "${DB_PATH}" ]]; then
  echo "SQLite catalog not found at ${DB_PATH}. Copy cannabis_papers.db into the repo root first." >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
unset DATABASE_URL
export DATABASE_PATH="${DB_PATH}"
export PYTHONUNBUFFERED=1

# One worker so the in-process daily harvest thread stays in this process.
exec "${GUNICORN}" \
  --workers 1 \
  --timeout 120 \
  --bind "${BIND}" \
  --access-logfile "${LOG_DIR}/macos_site.access.log" \
  --error-logfile "${LOG_DIR}/macos_site.error.log" \
  app:app
