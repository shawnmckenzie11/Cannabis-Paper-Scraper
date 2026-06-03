#!/bin/sh
set -e

DB_PATH=${DATABASE_PATH:-/data/cannabis_papers.db}
SEED_SOURCE="cannabis_papers.db"

echo "Checking database status..."

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

# Run the command passed to Docker (gunicorn)
exec "$@"
