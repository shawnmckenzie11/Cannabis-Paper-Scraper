#!/bin/sh
set -e

DB_PATH=${DATABASE_PATH:-/data/cannabis_papers.db}
SEED_SOURCE="cannabis_papers.db"
CALIBRATION_ARTIFACT_DIR="${CALIBRATION_ARTIFACT_DIR:-/data/calibration_runs}"
CHEAP_OPS_NORM=$(printf '%s' "${CHEAP_OPS:-0}" | tr '[:upper:]' '[:lower:]')

echo "Checking database status..."
mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$CALIBRATION_ARTIFACT_DIR"

APP_ENV_NORM=$(printf '%s' "${APP_ENV:-${FLASK_ENV:-}}" | tr '[:upper:]' '[:lower:]')
SEED_DEV_DB_NORM=$(printf '%s' "${SEED_DEV_DB:-0}" | tr '[:upper:]' '[:lower:]')

# Boot safety: never delete or overwrite an existing database.
# Seeding from the bundled SQLite file is a dev-only convenience and requires
# SEED_DEV_DB=1 plus APP_ENV/FLASK_ENV in dev/test, and only when no DB exists.
seed_allowed=0
case "$APP_ENV_NORM" in
    dev|development|local|test|testing)
        case "$SEED_DEV_DB_NORM" in
            1|true|yes|on) seed_allowed=1 ;;
        esac
        ;;
esac

if [ -n "$DATABASE_URL" ]; then
    echo "PostgreSQL mode (DATABASE_URL set): boot never seeds, drops or recreates the database."
elif [ -f "$DB_PATH" ]; then
    FILESIZE=$(stat -c%s "$DB_PATH" 2>/dev/null || stat -f%z "$DB_PATH" 2>/dev/null || echo 0)
    echo "Found existing database at $DB_PATH ($FILESIZE bytes); leaving it untouched."
elif [ "$seed_allowed" = "1" ] && [ -f "$SEED_SOURCE" ]; then
    echo "Dev seed: no database at $DB_PATH; copying $SEED_SOURCE..."
    cp "$SEED_SOURCE" "$DB_PATH.seed-tmp"
    mv -n "$DB_PATH.seed-tmp" "$DB_PATH" 2>/dev/null || true
    rm -f "$DB_PATH.seed-tmp"
    echo "Database seeded (dev)."
else
    echo "No database at $DB_PATH; seeding skipped (requires SEED_DEV_DB=1 and APP_ENV in dev/test)."
fi

if [ -f "$DB_PATH" ]; then
    echo "Running database schema migrations via Alembic..."
    if ! python3 -m alembic upgrade head; then
        echo "Warning: database migrations did not complete; continuing startup."
    fi
fi

# Start gunicorn in background so the port is immediately available
"$@" &
GUNICORN_PID=$!

# Give gunicorn a moment to bind the port before proceeding
sleep 2

if [ "$CHEAP_OPS_NORM" = "1" ] || [ "$CHEAP_OPS_NORM" = "true" ] || [ "$CHEAP_OPS_NORM" = "yes" ] || [ "$CHEAP_OPS_NORM" = "on" ]; then
    echo "CHEAP_OPS: skipping tab-flag backfill, stale harvest upgrade, and heuristic reclassify."
else
    if [ -f "$DB_PATH" ] && [ -z "$DATABASE_URL" ]; then
        echo "Scheduling indexed tab membership backfill in background (local SQLite only)..."
        nohup python3 ensure_tab_flags.py > /tmp/tab_flags_backfill.log 2>&1 &
    elif [ -n "$DATABASE_URL" ]; then
        echo "PostgreSQL mode: repairing recent harvest tab flags in background..."
        nohup python3 scripts/repair_recent_tab_flags.py --since-harvested 2026-07-17 \
            > /tmp/repair_recent_tab_flags.log 2>&1 &
        echo "Scheduling stale daily-harvest Maude version upgrade in background..."
        nohup python3 scripts/upgrade_stale_harvest_classifications.py --since-date 2026-06-01 \
            > /tmp/upgrade_stale_harvest.log 2>&1 &
    fi

    # Run heuristic reclassification in background if needed (non-blocking)
    if [ -f "$DB_PATH" ]; then
        echo "Checking if heuristic reclassification is needed..."
        RECLASSIFIED=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
count = conn.execute(\"SELECT COUNT(*) FROM papers WHERE classifier_version LIKE 'heuristic-reclassify-%'\").fetchone()[0]
conn.close()
print(count)
")
        if [ "$RECLASSIFIED" = "0" ]; then
            echo "Database needs reclassification. Reclassifying in background..."
            nohup python3 reclassify_metadata.py > /tmp/reclassify.log 2>&1 &
        else
            echo "Database already reclassified. Skipping."
        fi
    fi
fi

# Wait for gunicorn to finish
wait $GUNICORN_PID
