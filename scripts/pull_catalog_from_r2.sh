#!/usr/bin/env bash
# Pull the harvested SQLite catalog from Cloudflare R2 and ask gunicorn to swap it in.
#
# Required env:
#   R2_ENDPOINT   e.g. https://<accountid>.r2.cloudflarestorage.com
#   R2_BUCKET
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  (R2 API token)
#   CATALOG_RELOAD_TOKEN
#
# Optional:
#   R2_OBJECT              default cannabis_papers.db
#   CATALOG_STAGING_PATH   default /var/lib/paperscraper/cannabis_papers.db.new
#   SITE_URL               default http://127.0.0.1:8080
#   AWS_DEFAULT_REGION     default auto
set -euo pipefail

: "${R2_ENDPOINT:?R2_ENDPOINT is required}"
: "${R2_BUCKET:?R2_BUCKET is required}"
: "${CATALOG_RELOAD_TOKEN:?CATALOG_RELOAD_TOKEN is required}"

R2_OBJECT="${R2_OBJECT:-cannabis_papers.db}"
STAGING="${CATALOG_STAGING_PATH:-/var/lib/paperscraper/cannabis_papers.db.new}"
SITE_URL="${SITE_URL:-http://127.0.0.1:8080}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"

mkdir -p "$(dirname "$STAGING")"
aws s3 cp "s3://${R2_BUCKET}/${R2_OBJECT}" "${STAGING}" --endpoint-url "${R2_ENDPOINT}"

payload=$(STAGING="$STAGING" python3 -c 'import json, os; print(json.dumps({"staging_path": os.environ["STAGING"]}))')
curl -fsS -X POST \
  -H "X-Catalog-Reload-Token: ${CATALOG_RELOAD_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "${payload}" \
  "${SITE_URL%/}/api/catalog/reload"
