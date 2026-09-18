#!/usr/bin/env bash
# Restore a pg_dump into a scratch Postgres. Never wipes prod by default.
#
# Dry-run (safe, no writes):
#   ./scripts/restore_postgres.sh --dry-run scratch/postgres_backups/foo.dump
#
# Restore drill (localhost scratch):
#   docker run --rm -e POSTGRES_PASSWORD=scratch -p 5433:5432 postgres:16
#   ./scripts/restore_postgres.sh \
#     --target-url postgresql://postgres:scratch@127.0.0.1:5433/postgres \
#     scratch/postgres_backups/foo.dump
#
# Live Fly restore requires BOTH --allow-prod and
# RESTORE_CONFIRM_PROD=I_UNDERSTAND_THIS_OVERWRITES_PRODUCTION.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 --dry-run DUMP_PATH" >&2
  echo "       $0 --target-url URL DUMP_PATH" >&2
  echo "See docs/ops/postgres-backup-restore.md" >&2
  exit 2
fi

DRY_RUN=0
TARGET_URL=""
FORWARD=()
DUMP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --target-url)
      TARGET_URL="${2:-}"
      shift 2
      ;;
    --allow-prod|--verify|--no-clean)
      FORWARD+=("$1")
      shift
      ;;
    --format)
      FORWARD+=("$1" "${2:-}")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
    *)
      DUMP="$1"
      shift
      ;;
  esac
done

if [[ -z "$DUMP" && $# -gt 0 ]]; then
  DUMP="$1"
fi
if [[ -z "$DUMP" ]]; then
  echo "ERROR: dump path is required." >&2
  exit 2
fi

if [[ "$DRY_RUN" == "1" && -z "$TARGET_URL" ]]; then
  echo "==> Restore drill dry-run: listing dump TOC (no writes)"
  exec python3 scripts/postgres_backup.py list "$DUMP" "${FORWARD[@]}"
fi

if [[ -z "$TARGET_URL" ]]; then
  echo "ERROR: --target-url is required for an actual restore." >&2
  echo "Refusing to guess a destination (this never defaults to production)." >&2
  echo "For a no-write check: $0 --dry-run $DUMP" >&2
  exit 2
fi

ARGS=(python3 scripts/postgres_backup.py restore "$DUMP" --target-url "$TARGET_URL")
if [[ "$DRY_RUN" == "1" ]]; then
  ARGS+=(--dry-run)
fi
ARGS+=("${FORWARD[@]}")
exec "${ARGS[@]}"
