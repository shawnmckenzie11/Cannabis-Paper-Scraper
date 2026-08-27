# Live paperscraper (no Fly.io)

The catalog is a Flask + SQLite dashboard. **Fly.io is not used.** Daily PubMed
updates run in GitHub Actions. The website runs on Render’s free web service
(or any small host) and reads a SQLite file.

```
GitHub Actions cron (14:00 UTC)
  -> download cannabis_papers.db from Cloudflare R2
  -> python3 scripts/ci_daily_harvest.py  (DATABASE_URL unset)
  -> upload SQLite back to R2
  -> POST /api/catalog/reload on the web host
Render (free) gunicorn serves search against local SQLite
Optional DNS: paperscraper.miladlab.ca -> the Render URL
```

Cold start on the free Render plan can take a minute after idle (the instance
sleeps after 15 minutes with no traffic, then downloads the catalog from R2 if
the disk was wiped). That is the trade for $0 instead of Fly’s $50+/month
Managed Postgres bill.

## One-time setup

### 1. Cloudflare R2 (free)

Create a bucket (for example `paperscraper-catalog`). Create an R2 API token
with object read/write. Note the S3 endpoint
`https://<accountid>.r2.cloudflarestorage.com`.

Seed it with the **real** catalog (about 415 MB, ~21k papers). The empty
Cloud-agent `cannabis_papers.db` in git is not the corpus. A snapshot taken
from the old Fly volume can be uploaded:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=auto
export R2_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
aws s3 cp cannabis_papers.db s3://paperscraper-catalog/cannabis_papers.db \
  --endpoint-url "$R2_ENDPOINT"
```

### 2. GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Purpose |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | R2 token access key |
| `AWS_SECRET_ACCESS_KEY` | R2 token secret |
| `R2_ENDPOINT` | `https://<accountid>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | bucket name |
| `ENTREZ_EMAIL` | NCBI contact email |
| `NCBI_API_KEY` | optional NCBI key |
| `CATALOG_RELOAD_URL` | `https://<your-service>.onrender.com/api/catalog/reload` |
| `CATALOG_RELOAD_TOKEN` | long random token; same value on Render |

Workflow: [`.github/workflows/daily-harvest.yml`](../.github/workflows/daily-harvest.yml)
(`0 14 * * *` plus **Run workflow**). It never sets `DATABASE_URL`.

### 3. Render (free web service)

1. [dashboard.render.com](https://dashboard.render.com) → New → Blueprint.
2. Connect `shawnmckenzie11/Cannabis-Paper-Scraper` and this branch / `main`.
3. Instance type: **Free**.
4. Set the env vars from `render.yaml` (`R2_*`, AWS keys, `CATALOG_RELOAD_TOKEN`,
   `ACCESS_PASSWORD`). Leave `DATABASE_URL` **unset**.

`scripts/start_web.sh` pulls SQLite from R2 on first boot, then runs gunicorn
on `$PORT`.

After each Actions harvest the workflow POSTs `{"pull_from_r2": true}` to
`/api/catalog/reload`. You can also run
[`scripts/pull_catalog_from_r2.sh`](../scripts/pull_catalog_from_r2.sh) on the
host.

### 4. Optional DNS

At GoDaddy (or Cloudflare DNS) for `miladlab.ca`, CNAME `paperscraper` to the
Render hostname (`something.onrender.com`).

## Verify

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://<your-service>.onrender.com/
curl -sS https://<your-service>.onrender.com/api/scheduler/status
```

Expect HTTP 200. `inprocess_harvest` should be `false`. `last_run_date`
advances on the next successful Actions run. `last_run_status` must not mention
`postgres://` or `fly.dev`.

## Local catch-up without R2

```bash
unset DATABASE_URL
export DATABASE_PATH="$PWD/cannabis_papers.db"
python3 scripts/ci_daily_harvest.py --skip-r2 --sqlite-path "$DATABASE_PATH"
```

## Abandoned hosts

- **Fly.io** — cancelled (Managed Postgres was ~$45/month by default).
- **Mac + Cloudflare Tunnel** — see [macos-public-site.md](macos-public-site.md).
