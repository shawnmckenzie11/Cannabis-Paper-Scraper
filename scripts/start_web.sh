#!/usr/bin/env bash
# Start the public Flask catalog without Fly or Postgres.
#
# Required for a first boot (unless a full cannabis_papers.db is already on disk):
#   R2_ENDPOINT, R2_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# Optional:
#   DATABASE_PATH  default ./data/cannabis_papers.db
#   PORT           default 8080 (Render/Koyeb inject this)
set -euo pipefail

unset DATABASE_URL || true
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"
export DATABASE_PATH="${DATABASE_PATH:-$PWD/data/cannabis_papers.db}"
mkdir -p "$(dirname "$DATABASE_PATH")"

python3 - <<'PY'
import os
import sys

from catalog_reload import CatalogReloadError, ensure_local_catalog

try:
    path = ensure_local_catalog(os.environ["DATABASE_PATH"])
except CatalogReloadError as exc:
    print(f"Cannot start catalog: {exc}", file=sys.stderr)
    sys.exit(1)
print(f"SQLite catalog ready at {path}")
PY

PORT="${PORT:-8080}"
exec gunicorn --workers 1 --timeout 120 --bind "0.0.0.0:${PORT}" app:app
