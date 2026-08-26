# Live paperscraper on paperscraper.miladlab.ca

The catalog is a Flask + SQLite dashboard. Daily PubMed updates run in **GitHub
Actions**, not inside gunicorn, not on a Mac, and not on Fly Managed Postgres.

```
GitHub Actions cron (14:00 UTC)
  -> download cannabis_papers.db from Cloudflare R2
  -> python3 scripts/ci_daily_harvest.py  (DATABASE_URL unset)
  -> upload SQLite back to R2
  -> POST /api/catalog/reload on the VPS
gunicorn on a small always-on VPS serves search against local SQLite
GoDaddy / Cloudflare DNS: paperscraper.miladlab.ca -> VPS
```

## One-time setup

### 1. Cloudflare R2

Create a bucket (for example `paperscraper-catalog`). Create an R2 API token
with object read/write. Note the S3 endpoint
`https://<accountid>.r2.cloudflarestorage.com`.

Upload the **real** Mac catalog once (the empty Cloud-agent `cannabis_papers.db`
is not the corpus):

```bash
unset DATABASE_URL
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=auto
export R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
aws s3 cp "$HOME/Documents/Cannabis Paper Scraper/cannabis_papers.db" \
  s3://paperscraper-catalog/cannabis_papers.db \
  --endpoint-url "$R2_ENDPOINT"
```

### 2. GitHub Actions secrets

On the repo: Settings → Secrets and variables → Actions.

| Secret | Purpose |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | R2 token access key |
| `AWS_SECRET_ACCESS_KEY` | R2 token secret |
| `R2_ENDPOINT` | `https://<accountid>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | bucket name |
| `ENTREZ_EMAIL` | NCBI contact email |
| `NCBI_API_KEY` | optional NCBI key |
| `CATALOG_RELOAD_URL` | `https://paperscraper.miladlab.ca/api/catalog/reload` |
| `CATALOG_RELOAD_TOKEN` | long random token; same value on the VPS |

Workflow: [`.github/workflows/daily-harvest.yml`](../.github/workflows/daily-harvest.yml)
(`0 14 * * *` plus **Run workflow** for catch-up). It never sets `DATABASE_URL`.

### 3. VPS (Hetzner / DigitalOcean class)

Clone this repo, create `venv`, `pip install -r requirements.txt`, install
`awscli`. systemd example (`INPROCESS_DAILY_HARVEST` stays unset/off):

```
[Service]
WorkingDirectory=/opt/paperscraper
Environment=DATABASE_PATH=/opt/paperscraper/cannabis_papers.db
Environment=CATALOG_RELOAD_TOKEN=same-as-github-secret
Environment=R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
Environment=R2_BUCKET=paperscraper-catalog
Environment=AWS_ACCESS_KEY_ID=...
Environment=AWS_SECRET_ACCESS_KEY=...
Environment=AWS_DEFAULT_REGION=auto
ExecStart=/opt/paperscraper/venv/bin/gunicorn --workers 1 --bind 127.0.0.1:8080 app:app
```

Put nginx or Caddy in front with TLS for `paperscraper.miladlab.ca`. Pull the
seed catalog from R2 onto `DATABASE_PATH` before the first start.

After each Actions harvest the workflow POSTs `{"pull_from_r2": true}` to
`/api/catalog/reload` with header `X-Catalog-Reload-Token`. You can also run
[`scripts/pull_catalog_from_r2.sh`](../scripts/pull_catalog_from_r2.sh) on the
box.

### 4. DNS

At GoDaddy (or Cloudflare DNS) for `miladlab.ca`:

| Type | Name | Value |
| --- | --- | --- |
| A | `paperscraper` | VPS IPv4 |
| AAAA | `paperscraper` | VPS IPv6 (if any) |

No Cloudflare Tunnel. No LaunchAgent. Keep the VPS billed and running; it only
serves HTTP. Harvest still runs if the site is briefly down; reload retries on
the next workflow or a manual `pull_catalog_from_r2.sh`.

## Verify

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://paperscraper.miladlab.ca/
curl -sS https://paperscraper.miladlab.ca/api/scheduler/status
```

Expect HTTP 200. `inprocess_harvest` should be `false`. `last_run_date` advances
on the next successful Actions run. `last_run_status` must not mention
`postgres://`.

## Local catch-up without R2

```bash
unset DATABASE_URL
export DATABASE_PATH="$PWD/cannabis_papers.db"
python3 scripts/ci_daily_harvest.py --skip-r2 --sqlite-path "$DATABASE_PATH"
```

## Abandoned: Mac as server

See [macos-public-site.md](macos-public-site.md). Do not run LaunchAgents or
Cloudflare Tunnel on the laptop for production.
