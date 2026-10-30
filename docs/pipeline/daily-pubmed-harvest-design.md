# Daily PubMed harvest, design note (MCK-423)

**Status:** design only. Nothing scheduled, nothing deployed. Tip/dev only.

## Goal
Once a day, find new PubMed records that match the CRN search strategy, store their `pubmed_metadata_record` metadata (contract v0.5), and queue them as candidates. It does not extract, label, or add anything to a split.

## Flow
1. **Search:** NCBI E-utilities `esearch` with the documented CRN query string (seed 2.1; the query text lives in `config/harvest_query.txt`, versioned, hash logged) and `mindate`/`maxdate` = the window since the last successful run (`datetype=edat`). Free API; NCBI asks for `tool` + `email`; limit is 3 req/s without an api_key, 10 req/s with one. None of those are stored in provenance (#102).
2. **Dedupe:** drop PMIDs already in `papers`; DOI/title matches against existing works produce a `work_merge_flag` for Methods, never an auto-merge (v0.2 rule).
3. **Fetch metadata:** `scripts/fetch_pubmed_metadata.py` (efetch, batches of up to 200), writing `pubmed_metadata_record`s valid against contract v0.5.
4. **Screen flags (no decisions):** flag `Preprint` in `publication_types`; flag likely surveys, market studies and re-analyses so they stay out of candidate lists (Q7 ruling). Flags are for human review, not auto-exclusion beyond Q7.
5. **Queue:** insert as `ingestion_status = candidate`. New papers never enter an existing split manifest; Evaluation assigns splits by `work_id` in a new manifest version.

## Reliability
- One run at a time: the job takes an exclusive lock (lock file / DB advisory lock) and exits if another run holds it.
- Idempotent: the window is stored as `last_success_edat`. It advances only after the whole batch is saved, in the same database transaction as the inserts; a failed run leaves it unchanged, so reruns of the same window insert nothing new.
- Retries: 3 with exponential backoff plus jitter on timeouts/5xx, with a total retry-time cap per run; HTTP 429 honors `Retry-After`. Partial batches are retried, not skipped.
- Rate: at most 3 req/s without an api_key, 10 req/s with one.
- Run log: ids (PMIDs, run id) and counts (found, new, duplicate, flagged, failed) plus error classes only. No titles, abstracts, quotes, email, key or other PII. `runs/` is gitignored; harvest logs kept 30 days.
- Failure alert: error class to Sentry (query strings stripped, per MCK-102).

## Where it would run (decision needed later)
Options, none chosen: a Grok Bot routine calling a dev endpoint, a GitHub Actions cron, or a Fly scheduled machine. Fly needs Shawn's GO; prod needs Reliability's runbook. Until then, it runs only by hand on dev.

## Open
- Methods: final search query (2.1 documented strategy) and whether `candidate` needs a contract field.
- Biomedical lead: preprint rule.
- Reliability: scheduler choice and alerting.
