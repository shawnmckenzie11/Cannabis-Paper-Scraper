# Daily PubMed harvest, design note (MCK-423)

**Status:** design only. Nothing scheduled, nothing deployed. Tip/dev only.

## Goal
Once a day, find new PubMed records that match the CRN search strategy, store their v0.4 metadata, and queue them as candidates. It does not extract, label, or add anything to a split.

## Flow
1. **Search:** NCBI E-utilities `esearch` with the documented CRN query string (seed 2.1; the query text lives in `config/harvest_query.txt`, versioned, hash logged) and `mindate`/`maxdate` = the window since the last successful run (`datetype=edat`). Free API; NCBI asks for `tool` + `email`, plus an api_key above 3 req/s. None of those are stored in provenance (#102).
2. **Dedupe:** drop PMIDs already in `papers`; DOI/title matches against existing works produce a `work_merge_flag` for Methods, never an auto-merge (v0.2 rule).
3. **Fetch metadata:** `scripts/fetch_pubmed_metadata.py` (efetch, batches of up to 200), writing contract v0.4 `pubmed_metadata_record`s.
4. **Screen flags (no decisions):** flag `Preprint` in `publication_types`; flag likely surveys, market studies and re-analyses so they stay out of candidate lists (Q7 ruling). Flags are for human review, not auto-exclusion beyond Q7.
5. **Queue:** insert as `ingestion_status = candidate`. New papers never enter an existing split manifest; Evaluation assigns splits by `work_id` in a new manifest version.

## Reliability
- Idempotent: the window is stored as `last_success_edat`; reruns of the same window insert nothing new.
- Retries: 3 with backoff on timeouts/5xx; partial batches are retried, not skipped.
- Rate: stay under 3 req/s without a key.
- Run log: counts (found, new, duplicate, flagged, failed) and error classes only. No email, key, abstract text or PII.
- Failure alert: error class to Sentry (query strings stripped, per MCK-102).

## Where it would run (decision needed later)
Options, none chosen: a Grok Bot routine calling a dev endpoint, a GitHub Actions cron, or a Fly scheduled machine. Fly needs Shawn's GO; prod needs Reliability's runbook. Until then, it runs only by hand on dev.

## Open
- Methods: final search query (2.1 documented strategy) and whether `candidate` needs a contract field.
- Biomedical lead: preprint rule.
- Reliability: scheduler choice and alerting.
