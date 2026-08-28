#!/bin/sh
set -e

# Hugging Face Docker Spaces must bind 7860. The image default is Fly's 8080.
if [ -n "${SPACE_ID:-}" ]; then
    export PORT=7860
    if [ -z "${DATABASE_PATH:-}" ] || [ "$DATABASE_PATH" = "/data/cannabis_papers.db" ]; then
        export DATABASE_PATH=/tmp/cannabis_papers.db
    fi
    export CHEAP_OPS="${CHEAP_OPS:-1}"
    export CATALOG_DATASET_ID="${CATALOG_DATASET_ID:-mckenziansolutions/cannabis-papers-catalog}"
    export INPROCESS_DAILY_HARVEST="${INPROCESS_DAILY_HARVEST:-0}"
    echo "Hugging Face Space runtime: PORT=$PORT DATABASE_PATH=$DATABASE_PATH"
fi

DB_PATH=${DATABASE_PATH:-/data/cannabis_papers.db}
SEED_SOURCE="cannabis_papers.db"
CALIBRATION_ARTIFACT_DIR="/data/calibration_runs"
CHEAP_OPS_NORM=$(printf '%s' "${CHEAP_OPS:-0}" | tr '[:upper:]' '[:lower:]')

echo "Checking database status..."
mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$CALIBRATION_ARTIFACT_DIR"

# Hugging Face Space (and optional R2): pull the catalog before serving.
if [ -n "${SPACE_ID:-}" ] || [ -n "${CATALOG_DATASET_ID:-}" ] || [ -n "${R2_BUCKET:-}" ]; then
    echo "Pulling catalog from remote store into $DB_PATH..."
    if python3 -c "from catalog_store import ensure_local_catalog; ensure_local_catalog()"; then
        echo "Catalog pull succeeded."
        SKIP_TINY_DELETE=1
    else
        echo "Warning: catalog pull failed; continuing with local file if present."
    fi
fi

# If database exists but is incomplete (less than 50MB), delete it so we can re-seed
# Skip this wipe on Spaces — a freshly pulled Hub catalog may be rebuilt in stages.
if [ -f "$DB_PATH" ] && [ "${SKIP_TINY_DELETE:-0}" != "1" ]; then
    # Cross-platform file size retrieval (Linux/macOS)
    FILESIZE=$(stat -c%s "$DB_PATH" 2>/dev/null || stat -f%z "$DB_PATH" 2>/dev/null || echo 0)
    echo "Found existing database at $DB_PATH ($FILESIZE bytes)."
    if [ "$FILESIZE" -lt 50000000 ]; then
        echo "Database file is too small or incomplete. Deleting to trigger re-seed..."
        rm -f "$DB_PATH"
    fi
fi

# Seed the database if it doesn't exist
if [ -f "$SEED_SOURCE" ] && [ ! -f "$DB_PATH" ]; then
    echo "Seeding database from $SEED_SOURCE to $DB_PATH..."
    mkdir -p "$(dirname "$DB_PATH")"
    cp "$SEED_SOURCE" "$DB_PATH"
    echo "Database seeded successfully."
fi

if [ -n "${SPACE_ID:-}" ]; then
    echo "Hugging Face Space: skipping Alembic (SQLite catalog uses schema.sql)."
elif [ -f "$DB_PATH" ]; then
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
