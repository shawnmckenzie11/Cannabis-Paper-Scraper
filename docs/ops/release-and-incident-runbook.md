# CRN release and incident runbook (draft)

App: Fly app `cannabis-paper-scraper` (fly.toml), region `yyz`, 1 gunicorn worker (Dockerfile `CMD gunicorn --workers 1`), volume `mckenzian_db_volume` mounted at `/data`.
Steps marked **SHAWN:** are done by Shawn only. Everything else is read-only or local.

## 1. Google OAuth setup

Code paths (app.py):
- `/auth/google`: starts the OAuth flow, scope `openid email profile`.
- `/auth/google/callback` (GET and POST): OAuth code callback and the One Tap `credential` POST.
- One Tap `data-login_uri` (templates/login.html:341) = `{{ request.scheme }}://{{ request.host }}/auth/google/callback`.
- Redirect URI = `GOOGLE_REDIRECT_URI` if set, otherwise `<scheme>://<host>/auth/google/callback`.

Google Cloud console → APIs & Services → Credentials → OAuth client ID (Web application):
- Authorized JavaScript origins: `https://cannabis-paper-scraper.fly.dev` (and any custom domain you serve from).
- Authorized redirect URIs: `https://cannabis-paper-scraper.fly.dev/auth/google/callback` (same path on any custom domain).
- Set `GOOGLE_REDIRECT_URI` to that exact URI. Behind Fly's proxy, `request.scheme` may read `http`. An explicit value avoids a redirect_uri_mismatch.
- Env vars the code reads: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.

WARNING: the mock login `/auth/google/callback?email=...` (signs in as any email, including admins) is enabled only when `GOOGLE_FAKE_LOGIN` is truthy AND `APP_ENV`/`FLASK_ENV` is dev/test (app.py:1018). Missing Google secrets do not enable it. `GOOGLE_FAKE_LOGIN` must stay unset in prod.

## 2. Secrets

**SHAWN:** only Shawn runs these. Values below are placeholders.

```
fly secrets set -a cannabis-paper-scraper \
  FLASK_SECRET_KEY='<random 64+ chars>' \
  GOOGLE_CLIENT_ID='<client-id>.apps.googleusercontent.com' \
  GOOGLE_CLIENT_SECRET='<client-secret>' \
  GOOGLE_REDIRECT_URI='https://cannabis-paper-scraper.fly.dev/auth/google/callback' \
  ADMIN_EMAILS='<admin1@example.com>,<admin2@example.com>' \
  ANTHROPIC_API_KEY='<key>' \
  DATABASE_URL='<postgres://... if using Postgres>'
```

Email delivery (email_service.py), set one of these:
`RESEND_API_KEY` + `EMAIL_FROM`, or `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`, `SMTP_USE_SSL`.

Optional: `ENTREZ_EMAIL`, `DONATE_URL`, `RATELIMIT_STORAGE_URI`, `RATE_LIMIT_GUEST_READ` (default `60 per minute`), `RATE_LIMIT_ANALYZE`, `RATELIMIT_ENABLED` (keep it on).
Sentry (only once MCK-102 lands): `SENTRY_DSN`, optional `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`.
Already set in fly.toml `[env]`, not secrets: `CHEAP_OPS`, `AUTO_HARVEST_CLASSIFY`. Set in the Dockerfile: `DATABASE_PATH=/data/cannabis_papers.db`.

Never: `ALLOW_DEV_VERIFICATION_CODE` in prod.
Note: `FLASK_SECRET_KEY` has no fallback; outside dev/test the app refuses to boot without it (app.py:62-75).

Check names only, not values: `fly secrets list -a cannabis-paper-scraper`

## 2a. Backup before first unsuspend

**SHAWN:** do this before any unsuspend, deploy, or machine restart. `entrypoint.sh` (lines 16-41) never deletes, overwrites or re-seeds an existing DB (see `tests/test_boot_preserves_db.py`), but the volume copy may still be the only copy, so back it up.

1. Pull an encrypted copy to Shawn's machine only:
   - Primary, Postgres (prod uses Fly Postgres app `cannabis-papers-db` via `DATABASE_URL`): `fly proxy 15432:5432 -a cannabis-papers-db` then `pg_dump "postgres://...@localhost:15432/<db>" -Fc | gpg -c > crn-$(date +%F).dump.gpg` (`scripts/postgres_backup.py` wraps this).
   - Legacy, SQLite volume (only if a `/data/cannabis_papers.db` still exists and matters): `fly ssh sftp get -a cannabis-paper-scraper /data/cannabis_papers.db ./crn-$(date +%F).db`, then `gpg -c` (or `age -p`) and securely delete the plaintext.
2. Verify by restoring into a scratch local DB and counting rows:
   - Postgres: `pg_restore -l <file> | head` must list tables; then `createdb crn_verify && gpg -d crn-<date>.dump.gpg | pg_restore -d crn_verify --no-owner && psql crn_verify -c "SELECT (SELECT COUNT(*) FROM papers) papers, (SELECT COUNT(*) FROM users) users;"`
   - Legacy SQLite: `gpg -d crn-<date>.db.gpg > /tmp/crn-verify.db && sqlite3 /tmp/crn-verify.db "PRAGMA integrity_check; SELECT COUNT(*) FROM papers; SELECT COUNT(*) FROM users;"`
   - Counts must be non-zero and match the live app's numbers. Record only the counts; then delete the scratch DB.
3. Storage: the encrypted file stays on Shawn's machine only. Never in git, the repo, any agent or CI scratch space, or chat. Passphrase not stored next to the file.

## 3. Incident check before unsuspend

All steps are read-only until **SHAWN:** says go.

0. **SHAWN:** complete §2a *Backup before first unsuspend* (above) before anything else.
1. Fake-login search: `fly logs -a cannabis-paper-scraper` (live only; use the Fly dashboard log search for history). Search for `/auth/google/callback?email=` and the `[GOOGLE AUTH WARNING]` line. Record each IP, time, and email.
2. Users audit (on the local dump):
   - `SELECT id, username, email, google_id, is_verified, created_at FROM users ORDER BY created_at DESC;`
   - Flag `google_id LIKE 'mock_google_%'` (created by the mock path).
   - Flag accounts created during the exposure window.
   - Flag any email that is in `ADMIN_EMAILS`. Admin status comes from that env var, not a DB column, so confirm the list is exactly what you expect.
3. Integrity check of admin-written data. Review rows changed during the window: `feedback_audit`, paper edits (edit-classification, delete, sync-metadata, upload-pdf, reclassify-llm), calibration lock/resolve-disagreement. Compare against the pre-incident dump if you have one.
4. **SHAWN:** rotate `FLASK_SECRET_KEY` with `fly secrets set FLASK_SECRET_KEY='<new random>'`. This invalidates all sessions.
5. **SHAWN:** unsuspend the app.

## 4. Release order

1. Deploy main at the reviewed SHA (record the SHA before deploying).
2. Limiter: storage is `RATELIMIT_STORAGE_URI` (default `memory://`, per process). That is fine for today's single machine and single worker. Run with `RATE_LIMIT_SINGLE_INSTANCE=1` and leave `RATELIMIT_STORAGE_URI` **unset**. Do not set a `redis://` URI: the `redis` package is not in `requirements.txt`, so boot would crash. Scaling past one machine/worker needs a Shawn decision first (see "Scale" below).
3. Pre-deploy tests on the reviewed SHA, compared against the known-failure baseline `ci/test_failure_baseline.txt` (also enforced by the dev test suite CI workflow):
   ```
   source <your-venv>/bin/activate
   SENTRY_DSN= APP_ENV=test pytest test_*.py tests/ --junitxml=/tmp/pytest.xml
   python ci/check_test_baseline.py /tmp/pytest.xml ci/test_failure_baseline.txt
   ```
   A failure not in the baseline blocks the deploy.
4. **SHAWN:** `fly deploy -a cannabis-paper-scraper`.

## 5. Rollback

- List releases: `fly releases -a cannabis-paper-scraper --image`
- **SHAWN:** redeploy the previous image: `fly deploy -a cannabis-paper-scraper --image <previous image ref>`
- Secrets are not rolled back by an image rollback. Revert them separately if needed.
- DB: a code rollback does not undo schema or data changes.
  - Prod data lives in Fly Postgres (`cannabis-papers-db`); the `/data` volume persists across deploys.
  - entrypoint.sh no longer deletes or re-seeds an existing `/data` DB (entrypoint.sh:16-41); seeding happens only when no DB exists and SEED_DEV_DB=1 with a dev/test APP_ENV.
- Fly Postgres backups are NOT enabled (Shawn wants lightweight). **SHAWN:** before every deploy, take a manual dump (section 2a) and keep it locally encrypted. Restore from it only if data is damaged.

## 6. Post-deploy smoke

Set `BASE=https://cannabis-paper-scraper.fly.dev`.

1. Guest GETs return 200, and no gold/expert/rationale/split keys appear in JSON:
   `/`, `/api/dashboard-config`, `/api/tab-counts`, `/api/search?q=cbd`, `/api/search/section-stats`, `/api/graph/stats`, `/api/papers/<id>`.
2. Guest POST returns 401: `curl -s -o /dev/null -w '%{http_code}' -X POST $BASE/api/harvest`
3. Rate limit: `for i in $(seq 1 61); do curl -s -o /dev/null -w '%{http_code}\n' $BASE/api/tab-counts; done | tail -1` should print `429`.
4. Login: Google sign-in and One Tap both land signed in.
   - `$BASE/auth/google/callback?email=test@example.com` must NOT log you in.
   - Email/password login works.
5. Sentry (only once MCK-102 lands and `SENTRY_DSN` is set): `fly ssh console -a cannabis-paper-scraper -C "python -c 'import sentry_setup,sentry_sdk; sentry_setup.init_sentry(); sentry_sdk.capture_message(\"crn smoke test\"); sentry_sdk.flush()'"`, then confirm the event in Sentry.

## Scale: stays at 1 machine
- CRN runs on exactly one Fly machine with `RATE_LIMIT_SINGLE_INSTANCE=1`; the guest limiter uses in-memory storage, so the 60/min cap only holds on a single machine and a single worker.
- Check before and after every deploy: `fly scale show` must report count 1. Never run `fly scale count 2+`, and don't add regions or workers.
- Adding a second machine (or worker) is the trigger to bring the Redis question back to Shawn (SHAWN: decide on a shared limiter store, which is new infra and may cost money) before scaling.
