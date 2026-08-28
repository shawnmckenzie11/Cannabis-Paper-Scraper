#!/bin/sh
# One-shot daily PubMed harvest (Maude classify). Used by Fly SSH / GitHub Actions.
set -e
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
exec python3 -m daily_harvest "$@"
