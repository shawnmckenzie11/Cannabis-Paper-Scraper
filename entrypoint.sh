#!/bin/sh
set -e

DB_PATH=${DATABASE_PATH:-/data/cannabis_papers.db}
SEED_SOURCE="cannabis_papers.db"
CALIBRATION_ARTIFACT_DIR="/data/calibration_runs"

echo "Checking database status..."
mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$CALIBRATION_ARTIFACT_DIR"

# If database exists but is incomplete (less than 50MB), delete it so we can re-seed
if [ -f "$DB_PATH" ]; then
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

# Start gunicorn in background so the port is immediately available
"$@" &
GUNICORN_PID=$!

# Give gunicorn a moment to bind the port before proceeding
sleep 2

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

# Wait for gunicorn to finish
wait $GUNICORN_PID
