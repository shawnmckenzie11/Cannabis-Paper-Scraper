#!/usr/bin/env bash
# Logical backup of Fly Postgres (cannabis-papers-db) via pg_dump.
# Postgres is the sole production source of truth. This does not dump SQLite.
#
# Usage:
#   DATABASE_URL=... ./scripts/backup_postgres.sh
#   ./scripts/backup_postgres.sh --via-fly-proxy
#   ./scripts/backup_postgres.sh --format plain
#   ./scripts/backup_postgres.sh --dry-run
#   ./scripts/backup_postgres.sh --list-fly-snapshots
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VIA_FLY_PROXY=0
LIST_FLY=0
FORWARD=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --via-fly-proxy)
      VIA_FLY_PROXY=1
      shift
      ;;
    --list-fly-snapshots)
      LIST_FLY=1
      shift
      ;;
    *)
      FORWARD+=("$1")
      shift
      ;;
  esac
done

if [[ "$LIST_FLY" == "1" ]]; then
  exec python3 scripts/postgres_backup.py list-fly-snapshots "${FORWARD[@]}"
fi

if [[ "$VIA_FLY_PROXY" == "1" ]]; then
  exec bash scripts/run_with_fly_proxy_venv.sh \
    python3 scripts/postgres_backup.py dump "${FORWARD[@]}"
fi

exec python3 scripts/postgres_backup.py dump "${FORWARD[@]}"
