# AGENTS.md

## Cursor Cloud specific instructions

This is a single-service Python 3 / Flask app (a cannabis research papers catalog). Dependencies are installed by the cloud update script (`pip install -r requirements.txt`). The notes below are non-obvious caveats; standard commands live in `Dockerfile`, `entrypoint.sh`, and `requirements.txt`.

### Running the app (development)
- Dev server: `python3 app.py` → serves on `http://localhost:5001` with `debug=True`. (Production uses gunicorn on `:8080` per the `Dockerfile`; do not use that for local dev.)
- Set `ENTREZ_EMAIL` before running so the background PubMed harvester identifies itself, e.g. `ENTREZ_EMAIL=you@example.com python3 app.py`.
- Gotcha: on startup the app launches a background "daily harvest" scheduler thread that immediately hits PubMed + Semantic Scholar (needs internet) and ingests ~200 papers into the DB. With `debug=True` the Werkzeug reloader spawns it twice. This is expected; it does not block the server. LLM classification only runs if `AUTO_HARVEST_CLASSIFY=true` and `ANTHROPIC_API_KEY` is set — otherwise harvesting falls back to regex/keyword heuristics (no API key needed).

### Database
- SQLite by default at `DATABASE_PATH` (defaults to `cannabis_papers.db` in the repo root). Schema auto-creates from `schema.sql` on first use. Set `DATABASE_URL` (postgres://...) to use Postgres instead.
- No seed DB is committed, so the catalog starts empty. To seed real data without any API keys (uses PubMed + Semantic Scholar over the internet, heuristic classification): `python3 -c "import harvest; harvest.run_harvest_pipeline(query='cannabidiol epilepsy', max_results=15, update=True, classify=False)"`.

### Auth / signup
- Local username/password login works without external services. When SMTP env vars are unset, the email-verification code is shown directly on the verify page (dev fallback), so signup is fully testable offline.
- Write/admin endpoints are gated to emails in `ADMIN_EMAILS`; harvest/learning-dashboard/connectivity endpoints are intentionally `@mvp_gate`-disabled (return 403) in this MVP.

### Tests / lint
- Tests: `python3 -m unittest test_suite` (the suite detects `unittest` in `sys.modules` and skips the background scheduler). The DeprecationWarnings about sqlite named-parameter binding are pre-existing and harmless.
- No linter is configured; use `python3 -m py_compile *.py` for a basic syntax check.
