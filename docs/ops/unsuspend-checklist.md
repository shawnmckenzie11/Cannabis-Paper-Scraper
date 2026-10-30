# CRN unsuspend checklist (local-only)

App: Fly `cannabis-paper-scraper`. Verified against `origin/main` @ `c84dc9a` (2026-10-29).
Extends `release-and-incident-runbook.md`. **Only Shawn runs Fly commands or sets secrets. Bots never do.**

> Note: `entrypoint.sh:16-41` never deletes or reseeds an existing DB, and `redis` is not in `requirements.txt` (a `redis://` URI would crash boot); the runbook reflects this.

## 1. Pre-flight secrets (Shawn)

Prod refuses to boot unless these are set and non-empty (`boot_safety.py:9`, `:47-58`, called at `app.py:45`; secret key also checked at `app.py:61-75`):

- [ ] `FLASK_SECRET_KEY` set
- [ ] `GOOGLE_CLIENT_ID` set
- [ ] `GOOGLE_CLIENT_SECRET` set

Rate limiter (`boot_safety.py:26-44`, used at `app.py:49`). Without one of these, boot fails:

- [ ] `RATE_LIMIT_SINGLE_INSTANCE=1` set
- [ ] `RATELIMIT_STORAGE_URI` **unset** (any non-`memory://` value is used as-is; `redis` package isn't installed, so `redis://` crashes)
- [ ] Still 1 machine, 1 gunicorn worker (`Dockerfile:32`). Memory limits are per process.

Must stay / must not be set:

- [ ] `CHEAP_OPS=1` stays (`fly.toml:12`; skips boot background jobs, `entrypoint.sh:57-58`)
- [ ] `APP_ENV` and `FLASK_ENV` **not set** to a dev/test value. A dev value skips every check above (`boot_safety.py:8,19-23,49`), switches to a random secret key (`app.py:66-71`), and with `test` can turn CSRF off (`app.py:122-125,136`)
- [ ] `SEED_DEV_DB` **not set** (`entrypoint.sh:14-26`)
- [ ] `ALLOW_RECLASSIFY_PURGE` not set (reclassify DELETEs papers; `entrypoint.sh:71-80`)
- [ ] `GOOGLE_FAKE_LOGIN` not set (`app.py:1018-1020`)
- [ ] `WTF_CSRF_ENABLED`, `SESSION_COOKIE_SECURE`, `RATELIMIT_ENABLED` not set to `0` (`app.py:92,130,136`)

Optional, read at boot or runtime (not required to boot):
- [ ] `GOOGLE_REDIRECT_URI` (OAuth, `app.py:984,1078`) is the prod callback URL
- [ ] `ADMIN_EMAILS` (`app.py:200`), `SENTRY_DSN` (`sentry_setup.py:219`), `DATABASE_URL` (Postgres mode, `entrypoint.sh:28`, `db_manager.py:351`), `ANTHROPIC_API_KEY`, `RESEND_API_KEY` or `SMTP_*` (`email_service.py:30-44`), `DONATE_URL`, `RATE_LIMIT_ANALYZE`

## 2. Backup first (Shawn, on your own machine)

Fly Postgres backups are **not** enabled (by choice, to keep it lightweight). So take one manual dump before unsuspend:

- [ ] `pg_dump -Fc` the prod DB through a local `fly proxy` (the repo helper `scripts/postgres_backup.py` wraps this)
- [ ] Encrypt it (e.g. `age` or `gpg -c`), then delete the plaintext dump
- [ ] Keep it on your machine only: **never** in git, never on the shared bot box
- [ ] Check it isn't empty: `pg_restore -l <file> | head`

## 3. Unsuspend (Shawn)

- [ ] `fly secrets list -a cannabis-paper-scraper`: names match §1 (values hidden)
- [ ] `fly scale count 1 -a cannabis-paper-scraper` (or resume however it was suspended)
- [ ] `fly logs -a cannabis-paper-scraper`: no `Refusing to start`; you should see the `RATE_LIMIT_SINGLE_INSTANCE=1` warning and `CHEAP_OPS: skipping...`

## 4. Smoke checks (base `https://cannabis-paper-scraper.fly.dev`)

- [ ] `curl -s $BASE/health` gives `{"ok":true}` (`app.py:788-791`)
- [ ] `curl -sI $BASE/login` gives 200 and the page shows the Google button (`app.py:794`)
- [ ] `curl -s "$BASE/api/search?limit=5000" | jq '.papers|length'` gives ≤100 (clamped, not rejected; `app.py:542,1528`)
- [ ] CSRF is on: `curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/login` gives 400, not 200 or 302 (`app.py:136-138`)
- [ ] Rate-limit headers show up: `curl -sI $BASE/api/search | grep -i x-ratelimit` (`app.py:98`)
- [ ] Real Google sign-in works in a browser

## 5. Rollback

- [ ] Re-suspend: `fly scale count 0 -a cannabis-paper-scraper` (note: `auto_start_machines=true`, `fly.toml:21`, so traffic restarts a stopped machine. Scale to 0, don't just stop it)
- [ ] Check `fly status` shows 0 running machines and the URL no longer serves
- [ ] Read logs for the cause (missing var name, `Refusing to start`, OAuth errors)
- [ ] If data looks wrong: restore from your §2 dump (`pg_restore`; `scripts/postgres_backup.py` requires `RESTORE_CONFIRM_PROD` for prod, `:271`). Don't change anything until you've compared the data.
