# 2.3 Extraction plan (MCK-426)

**Owner:** CRN Pipeline. **Status:** plan only, nothing built or run. **Contract:** Methods v0.5 (`schemas/methods.schema.json`, `x-contract-version: "0.5"`, records written as `schema_version: "wp1.5.0-draft"`). Tip/dev only. No Fly, no prod, no new paid APIs, no test-label access.

This plan says how a paper becomes one contract-valid methods record. It adds no fields and no scientific meanings. Anything marked `pending_biomedical` in the contract stays that way: the extractor reports source wording and never picks a vocabulary term the Biomedical lead hasn't approved.

## 1. Stages

| # | Stage | Input | Output | Contract hook |
|---|---|---|---|---|
| 0 | Select | a Pipeline-side copy of the split manifest with test rows removed (PMID + split columns only, dev/validation) | work list | `paper_pmid_mapping`; `pmid_missing` papers go to `extraction_run_manifest.excluded` |
| 1 | Metadata | PMID | `pubmed_metadata_record` | via `scripts/fetch_pubmed_metadata.py` (no email/api_key in provenance, #102) |
| 2 | Acquire text | DOI/PMID | best legal source text + `source_tier` + `source_license` | `source_tier` enum; `provenance_envelope.source_license` |
| 3 | Parse | PDF/XML | sectioned text with locators (§2.2) | feeds `source_span` |
| 4 | Strip | parsed text | text with emails, phone numbers and the author/affiliation/correspondence block removed | none (internal; PII never reaches the LLM) |
| 5 | Locate | sections | candidate passages per field group | none (internal) |
| 6 | Extract | passages | per-field `{value, missingness, confidence, source_span}` (+ `unit_as_reported` on number fields) | `string_field`, `number_field` etc. |
| 7 | Validate | draft record | pass, or repair once, or fail | full JSON Schema validation against v0.5 |
| 8 | Persist | valid record | append-only row + run manifest/events | `record_id`, `correction` (never edit in place) |

### Stage 0 test-set guard
- The selector reads only a derived manifest file that already has test rows removed; it never opens the full split manifest, `tests/golden_dataset.json`, qrels or grades.
- The runner itself enforces this: it refuses (hard error, before any network call) any input path matching the gold dataset, qrels, grades or the full split manifest, and any row whose split is not `dev` or `validation`. This is a code check in the runner, not a convention.
- `test` never appears anywhere under `runs/` (the manifest `split` enum allows only `dev` and `validation`). Only Evaluation reads gold, in its own process.

### Stage 2 source order (licensing-aware, seed 2.1)
1. PMC Open Access XML: tier `pmc_oa_xml`, `license_source: pmc_oa_list`.
2. Open PDF already linked in the catalog (existing pypdf path in `reclassify_with_llm.py`): tier `pdf_extracted`, `license_source: publisher_page` if a licence is shown there, else `unknown`.
3. Abstract from the metadata record: tier `abstract_only`.
4. Nothing usable: tier `metadata_only`; every field gets `missingness: source_unavailable`, and the paper is listed in `excluded` with reason `source_unavailable`.

We never fetch from paywalled or shadow sources. Whether abstract-only extractions may count toward Compare is Q13 (`pending_biomedical`); the record always carries its tier so that rule can be applied later without re-extraction.

### Licence per paper
Every record's provenance carries `source_license`: `license_raw` (verbatim, or null if none found), `license_source` (`pmc_oa_list` | `publisher_page` | `unknown`), `fulltext_storable: false` (const in v0.5), `pending_biomedical: true`. Full text is stored only when `fulltext_storable` is true, which the contract currently forbids, so for now we keep **only quoted spans and extracted fields**. Parsed full text lives in memory for the run and is discarded.

### Stage 3 parser
Today: pypdf plain text, no sections. The parser benchmark (seed 2.3) compares pypdf, GROBID (self-hosted, free) and Marker (self-hosted, free) on dev papers only, scored on section recovery and table text, not on any label. Until it lands, stage 3 = PMC XML sections when available, else pypdf with page numbers as locators. If parsing fails and the pipeline falls back to the abstract, it logs a `run_event` with reason `parse_downgrade_abstract_only`.

## 2. Field groups and how each is extracted

Fields come straight from `fields` in the contract. Grouping just batches prompts.

| Group | Fields | Method |
|---|---|---|
| A. Design | `research_type`, `clinical_subtype`, `species_or_biological_system`, `disease_or_experimental_model`, `outcome_domains`, `publication_type` | LLM, verbatim source wording only; `pending_biomedical: true` kept |
| B. Exposure | `product`, `route`, `strain_reported`, `administration_frequency`, `treatment_duration`, `inhaled_exposure_duration` | LLM verbatim |
| C. Numbers | dose/conc/pct fields, `puff_count`, `duration_days`, `repeat_exposure_count`, `sample_size`, `multiple_doses`, `multiple_time_intervals` | existing rule extractor (`extractor.py`) first, LLM second; `unit_as_reported` filled on every `number_field` with the unit as written; no unit conversion (Q8) |
| D. SGBA | `sgba_*` | LLM reported-only; never inferred from names/country |
| E. Carry-over | `study_type_legacy`, `replicates_notes` | copied / free text as reported |
| Not model-filled | `strain_normalized` (waits MCK-425), `exposure_regimen_bin` (waits Q8), `ideas_representation_notes` (human-only) | left `uncertain` (pipeline-set) or empty for human entry; never sent to the model |

Rules for every field:
- `missingness: reported` only when a `source_span` quote matches the parsed text (§2.1). No quote, no value.
- `not_in_available_source` vs `source_unavailable` follows the source tier (abstract-only says "not in available source", not "not reported").
- `not_applicable` is set only by a human reviewer or by a deterministic rule from the field dictionary's Role column (e.g. clinical-only fields on a preclinical paper once `research_type` is reviewed); the model never sets it.
- `extraction_failed` is set by the pipeline on errors, never by the model.
- `confidence` is stored but not trusted until Evaluation calibrates it (§6).
- Every machine record has `human_override: false` and `reviewed_at: null` until a person reviews it; review sets them (and `honesty_label`) via a new superseding record, never an in-place edit.

### 2.1 Quote match
Normalization before comparing `source_span` to the parsed text: Unicode NFKC, lowercase, collapse all whitespace (incl. line breaks) to one space, rejoin words hyphenated across line breaks, unify quote/dash variants (‘’“” → ' ", – — → -). Match = exact substring after normalization; no fuzzy match. On failure the value is dropped, the field is set `missingness: uncertain`, and a `run_event` `{record_id, field, reason: "quote_not_found"}` is logged (distinct from a reviewer's `uncertain`). The quote itself is not logged.

### 2.2 Locator
`source_span` holds the verbatim quote prefixed by a locator: `pmc:<section-path>` for `pmc_oa_xml` (XML section titles, e.g. `pmc:Methods/Animals`), `page:<n>` for `pdf_extracted`, `abstract` for `abstract_only`. Format: `<locator> | <quote>`.

### 2.3 Record identity
- `record_id` = deterministic hash of (`paper_id`, `extractor_id`, `config_hash`, `prompt_hashes`), so reruns of the same config on the same paper give the same id (contract: stable across reruns).
- A changed config yields a new record; corrections and human review are `correction` records superseding by `record_id`.
- Current record for a paper = the latest non-retracted record in its correction chain.

## 3. Model and prompt config

- **Model:** the Anthropic models already wired in `classifier.py` (existing key; no new paid API). Default: the cheapest model in that list that passes the dev gate; a stronger model only for groups that fail it.
- **Pinned ids:** `config/extraction.yml` has a `model_id` placeholder per group that must be an exact dated model string (no aliases like `-latest`); the runner refuses to start a scored run with a placeholder. The id actually returned by the API is written to `provenance.model_id` and `manifest.model_ids`; a mismatch with config fails the run.
- **Settings:** temperature 0, no tools / function calling enabled, structured output against a per-group subset of the contract schema only, one call per field group per paper (about 5 calls/paper).
- **Prompt safety:** paper text is untrusted data. It goes inside a clearly fenced data block, with the instruction that nothing inside it is an instruction. The output must be schema-valid JSON and nothing else; any output containing instruction-like or off-schema content (extra keys, prose, URLs/commands not present in the source quote) is discarded and the group set `extraction_failed`.
- **Prompt ids:** `prompt_id = "x23-<group>-v<N>"`, file under `prompts/extraction/`, content hash in `manifest.prompt_hashes`.
- **Config file:** `config/extraction.yml` (model per group, max tokens, retries, per-day cap, budget cap). Changing it changes `config_hash` and bumps `extractor_id`.
- **Tuning:** dev split only. Validation is scored only by Evaluation, capped and logged (§6); validation results never feed back into prompts or model choice. Test is never seen by Pipeline.

## 4. Cost and caps

No numbers are invented here. Cost per run = papers × ~5 calls × (input + output tokens) × the provider's listed per-token price for the pinned model, read off the pricing page on the day of the run.
- Measure, don't guess: the first dev run records real token counts per call.
- **Budget cap** in `config/extraction.yml`: the runner stops before a call that would exceed it. Default 0 = dry-run only until Shawn sets a value.
- **Per-day call cap:** every API call counts, including retries and repair calls.
- Papers skipped by either cap are listed in `manifest.excluded` with reason `budget_cap` (Evaluation treats them as excluded, not misses).
- **Dry run:** `mode: dry_run` builds every prompt and counts tokens without calling the API. It writes no records (`records: []`), `model_ids: null`, and is never scored.

## 5. Failure handling

| Failure | Handling | Record result |
|---|---|---|
| Fetch timeout / 5xx | 3 retries, exponential backoff with jitter, total retry time capped per paper (config) | then fall to next source tier |
| HTTP 429 | honor `Retry-After`; counts as a retry | as above |
| Parse error | fall to abstract | tier `abstract_only`, `run_event` `parse_downgrade_abstract_only` |
| Model error / rate limit | 3 retries, jittered backoff, total-time cap; counts against the per-day cap | affected group `extraction_failed`, `run_event` `extraction_failed` |
| N consecutive model/rate-limit failures (N in config) | stop the run cleanly | remaining papers listed as excluded (`budget_cap` is the only fitting contract reason; see §7) |
| Invalid JSON / schema fail | one repair call quoting the validator error (counts against cap) | still failing: group `extraction_failed` |
| Quote not found in source | drop value | `missingness: uncertain`, `run_event` `quote_not_found` |
| Budget/day cap hit | stop run cleanly | remaining papers in `excluded` with `budget_cap` |

Nothing failed is silently filled. Run logs and `run_event`s hold ids and reason codes only: **no quotes**, no full text, email, api_key or reviewer data. Quotes live only in the records' `source_span`. Sentry gets error class only.

## 6. Evaluation hooks (for Evaluation to review)

- Every run writes `runs/<run_id>/records.jsonl` + `manifest.json`, an `extraction_run_manifest` (`run_id`, `mode`, `split` = dev|validation, `contract_version: "0.5"`, `git_ref`, `config_hash`, `prompt_hashes`, `model_ids`, `records` [`record_id`, `paper_pmid`], `excluded`, `events`). Methods records carry no split; the manifest is the split mapping.
- `runs/` is gitignored (to be added with the runner code); retention: dev runs kept 30 days, frozen/validation runs kept until superseded by the next frozen run. Records hold quotes and fields only, never full text.
- **Frozen run** = a git tag (`git_ref`) plus the manifest's config and prompt hashes. Evaluation runs validation/test with that tag in its own process; only Evaluation reads gold.
- **Validation runs** are capped (count set by Evaluation) and each one is logged; results never change prompts or model choice.
- The field-level P/R/F1 scorer consumes `records.jsonl` as-is; Pipeline never runs it against test. Scoring reports: scorer version, contract pin (`0.5` / `wp1.5.0-draft`), and the `source_tier` map of scored records.
- Out of scoring: `strain_normalized`, `exposure_regimen_bin`, `ideas_representation_notes` (not model-filled).
- Confidence calibration: Evaluation bins `confidence` against dev correctness (reliability curve) before any confidence threshold is used in UI or routing.

## 7. Open items needing others

- Methods: confirm group subsets don't need new schema; MCK-425 vocabulary before `strain_normalized`. `excluded.reason` has no value for "stopped after consecutive model failures"; using `budget_cap` until Methods decides whether to add one.
- Biomedical lead: Q8 (unit conversion), Q13 (abstract-only truth policy), preprint rule.
- Shawn: budget cap value before any paid run; licence-to-storage policy (`fulltext_storable`).
- Evaluation: dev gate metric and threshold; validation run cap; retention periods above.
