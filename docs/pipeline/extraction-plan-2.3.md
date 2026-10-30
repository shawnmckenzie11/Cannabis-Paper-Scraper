# 2.3 Extraction plan (MCK-426)

**Owner:** CRN Pipeline. **Status:** plan only, nothing built or run. **Contract:** Methods v0.4 (`schemas/methods.schema.json`, `x-contract-version: "0.4"`, `wp1.4.0-draft`). Tip/dev only. No Fly, no prod, no new paid APIs, no test-label access.

This plan says how a paper becomes one contract-valid methods record. It adds no fields and no scientific meanings. Anything marked `pending_biomedical` in the contract stays that way: the extractor reports source wording and never picks a vocabulary term the Biomedical lead hasn't approved.

## 1. Stages

| # | Stage | Input | Output | Contract hook |
|---|---|---|---|---|
| 0 | Select | split manifest PMID column (dev/validation only) | work list | `paper_pmid_mapping`; `pmid_missing` papers skipped, logged |
| 1 | Metadata | PMID | `pubmed_metadata_record` | v0.4 record via `scripts/fetch_pubmed_metadata.py` (no email/api_key in provenance, #102) |
| 2 | Acquire text | DOI/PMID | best legal source text + `source_tier` | `source_tier` enum |
| 3 | Parse | PDF/XML | sectioned text with page/section locators | feeds `source_span` |
| 4 | Locate | sections | candidate passages per field group | none (internal) |
| 5 | Extract | passages | per-field `{value, missingness, confidence, source_span}` | `string_field` etc. |
| 6 | Validate | draft record | pass, or repair once, or fail | full JSON Schema validation |
| 7 | Persist | valid record | append-only row + run log | `record_id`, `correction` (never edit in place) |

### Stage 2 source order (licensing-aware, seed 2.1)
1. PMC Open Access XML (`pdf_extracted` tier is reserved for real full text; XML counts as full text).
2. Open PDF already linked in the catalog (existing pypdf path in `reclassify_with_llm.py`).
3. Abstract from the v0.4 metadata record, tier `abstract_only`.
4. Nothing usable: tier `metadata_only`; every field gets `missingness: source_unavailable`.

We never fetch from paywalled or shadow sources. Whether abstract-only extractions may count toward Compare is Q13 (`pending_biomedical`); the record always carries its tier so that rule can be applied later without re-extraction.

### Stage 3 parser
Today: pypdf plain text, no sections. The parser benchmark (seed 2.3) compares pypdf, GROBID (self-hosted, free) and Marker (self-hosted, free) on dev papers only, scored on section recovery and table text, not on any label. Until it lands, stage 3 = PMC XML sections when available, else pypdf with page numbers as locators.

## 2. Field groups and how each is extracted

Fields come straight from `fields` in the contract. Grouping just batches prompts.

| Group | Fields | Method |
|---|---|---|
| A. Design | `research_type`, `clinical_subtype`, `species_or_biological_system`, `disease_or_experimental_model`, `outcome_domains`, `publication_type` | LLM, verbatim source wording only; `pending_biomedical: true` kept |
| B. Exposure | `product`, `route`, `strain_reported`, `administration_frequency`, `treatment_duration`, `inhaled_exposure_duration`, `exposure_regimen_bin` | LLM verbatim; `strain_normalized` and `exposure_regimen_bin` left `uncertain` until MCK-425 and Q8 are decided |
| C. Numbers | dose/conc/pct fields, `puff_count`, `duration_days`, `repeat_exposure_count`, `sample_size`, `multiple_doses`, `multiple_time_intervals` | existing rule extractor (`extractor.py`) first, LLM second; numbers only in the unit family reported; no unit conversion (Q8) |
| D. SGBA/IDEAS | `sgba_*`, `ideas_representation_notes` | LLM reported-only; never inferred from names/country; `ideas_representation_notes` is human-only, never model-filled |
| E. Carry-over | `study_type_legacy`, `replicates_notes` | copied / free text as reported |

Rules for every field:
- `missingness: reported` only when a `source_span` quote exists in the parsed text (string match checked in stage 6). No quote, no value.
- `not_in_available_source` vs `source_unavailable` follows the source tier (abstract-only says "not in available source", not "not reported").
- `extraction_failed` is set by the pipeline on errors, never by the model.
- `confidence` is stored but not trusted until Evaluation calibrates it on dev.

## 3. Model and prompt config

- **Model:** the Anthropic models already wired in `classifier.py` (existing key; no new paid API). Default: the cheapest model in that list that passes the dev gate below; a stronger model only for groups that fail it.
- **Settings:** temperature 0, JSON output against a per-group subset of the contract schema, one call per field group per paper (about 5 calls/paper).
- **Prompt ids:** `prompt_id = "x23-<group>-v<N>"`, file under `prompts/extraction/`, content hash logged per run. `model_id` = the exact model string returned by the API. Both go into the provenance envelope, which already requires them.
- **Config file:** `config/extraction.yml` (model per group, max tokens, retries, budget cap). Changing it bumps `extractor_id`.
- **Tuning:** dev split only. Validation is used once per frozen config. Test is never seen by Pipeline; Evaluation runs it.

## 4. Cost

No numbers are invented here. Cost per run = papers × ~5 calls × (input tokens + output tokens) × the provider's listed per-token price for the chosen model, read off the pricing page on the day of the run.
- Measure, don't guess: the first dev run records real token counts per call in the run log.
- **Budget cap** in `config/extraction.yml`: the runner stops before a call that would exceed it. The cap value is Shawn's call; default 0 means dry-run only.
- Dry-run mode builds every prompt and counts tokens without calling the API, so a cost estimate exists before any spend.

## 5. Failure handling

| Failure | Handling | Record result |
|---|---|---|
| Fetch timeout / 5xx | 3 retries, backoff 2/4/8 s | after that, fall to next source tier |
| Parse error | log, fall to abstract | tier `abstract_only` |
| Model error / rate limit | 3 retries with backoff | affected group `extraction_failed` |
| Invalid JSON / schema fail | one repair call quoting the validator error | still failing: group `extraction_failed` |
| Quote not found in source | drop value | `missingness: uncertain`, span kept in run log only |
| Budget cap hit | stop run cleanly | remaining papers untouched, listed in run log |

Nothing failed is silently filled. Run logs hold counts, ids and error classes only: no email, api_key, full text or reviewer data, and nothing goes to Sentry beyond error class.

## 6. Evaluation hooks (for Evaluation to review)

- Every run writes `runs/<run_id>/records.jsonl` + `manifest.json` (config hash, prompt hashes, model ids, split = dev|validation, PMIDs).
- The field-level P/R/F1 scorer consumes `records.jsonl` as-is; Pipeline never runs it against test.
- Frozen config = a git tag; Evaluation runs validation/test with that tag in its own process.

## 7. Open items needing others

- Methods: confirm group subsets don't need new schema; MCK-425 vocabulary before `strain_normalized`.
- Biomedical lead: Q8 (unit conversion), Q13 (abstract-only truth policy), preprint rule.
- Shawn: budget cap value before any paid run.
- Evaluation: dev gate metric and threshold for "model passes".
