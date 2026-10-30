# Field Dictionary — Year-1 Proposed Methods Fields

**Role:** CRN Methods (Scientific Data Architect)  
**Date:** 2026-09-22 (ET)  
**Contract:** Scientific **value meanings and allowed enums are `pending_biomedical`** until Shawn adjudicates (`biomedical-adjudication-questions.md`).  
**Technical:** Field names, types, provenance, missingness, and IDEAS/SGBA hooks are proposed here for tip/dev schema work.

---

## Conventions

| Marker | Meaning |
|--------|---------|
| `pending_biomedical` | Scientific definition / allowed values await Shawn — do not invent |
| **Core (proposed)** | Candidate Year-1 Compare / extraction spine — mandatory vs deferred is Q1 |
| **Supporting** | Useful for extraction honesty or gold; not necessarily Compare filters |
| **Deferred** | Placeholder only until adjudication |
| Missingness | Every extractable value may carry a `missingness` code (see schema) |
| Provenance | Every extraction record carries the provenance envelope |

Legacy Scraper column names are noted under **Maps from** for migration planning (Eng lane later).

---

## A. Record identity (catalog — keep)

| Field | Type (tech) | Year-1 role | Maps from | Scientific notes |
|-------|-------------|-------------|-----------|------------------|
| `paper_id` | integer / FK | Unit of attachment (default) | `papers.id` | Whether paper vs arm is the scientific unit is Q7 — tech default = paper |
| `pmid`, `doi` | string | Provenance to source | existing | — |
| `title`, `year`, `journal` | string/int | Display | existing | — |

---

## B. Core method / context fields (proposed)

### B1. `research_type`

| | |
|--|--|
| **Role** | Core (proposed) |
| **Intent** | High-level study system: in vitro / in vivo / clinical (+ edge cases) |
| **Type** | string or string[] — **enum `pending_biomedical`** |
| **Maps from** | fragments of `study_type` + Node 2a/2b/2c routing |
| **Missingness** | required awareness |
| **Shawn** | Q2 (ontology + edge cases), Q3 (clinical subtypes may nest or sibling) |

### B2. `clinical_subtype`

| | |
|--|--|
| **Role** | Core if clinical; else `not_applicable` |
| **Intent** | Clinical design grain (e.g. RCT / observational / …) |
| **Type** | string — **enum `pending_biomedical`** |
| **Maps from** | clinical labels inside `study_type` |
| **Shawn** | Q3 |

### B3. `product`

| | |
|--|--|
| **Role** | Core (proposed) |
| **Intent** | What cannabis / cannabinoid product or substance class was used |
| **Type** | string[] — **enum `pending_biomedical`** |
| **Maps from** | `cannabis_type` |
| **Shawn** | Q4; interaction with route = Q5 |

### B4. `route`

| | |
|--|--|
| **Role** | Core (proposed) |
| **Intent** | Route / administration / exposure modality |
| **Type** | string[] — **enum `pending_biomedical`** |
| **Maps from** | `exposure_method` (partial; Node 7 paths are finer) |
| **Shawn** | Q5 (route vs product), Q2 edge cases |

### B5. `disease_or_experimental_model`

| | |
|--|--|
| **Role** | Core (proposed) / Deferred until Q6 |
| **Intent** | Disease context or experimental model enabling Compare filters |
| **Type** | string or structured object — **ontology `pending_biomedical`** |
| **Maps from** | **no dedicated column today** |
| **Shawn** | Q6 |

### B6. `outcome_domains`

| | |
|--|--|
| **Role** | Core (proposed) |
| **Intent** | Primary/secondary outcome domains the study actually investigated |
| **Type** | string[] — **enum `pending_biomedical`** |
| **Maps from** | `outcome_domain` |
| **Shawn** | Q10 |

### B7. `species_or_biological_system`

| | |
|--|--|
| **Role** | Core for preclinical; `not_applicable` often for clinical human |
| **Intent** | Host species or biological system |
| **Type** | string — **enum `pending_biomedical`** |
| **Maps from** | `species` |
| **Shawn** | Q11 |

---

## C. Dose / concentration / regimen (supporting → core candidates)

**Technical rule (proposed):** store values **in the unit family as reported**. Cross-family conversion policy is Q8 — do not encode forbidden conversions into the dictionary as allowed science.

| Field | Type | Maps from | Notes |
|-------|------|-----------|-------|
| `dose_mg` | number \| null | `dose_mg` | Absolute dose (mg) when reported |
| `dose_thc_mg_kg` / `dose_cbd_mg_kg` | number \| null | `thc_mg_kg`, `cbd_mg_kg` | Weight-adjusted |
| `dose_thc_mg_g` / `dose_cbd_mg_g` | number \| null | `thc_mg_g`, `cbd_mg_g` | As reported |
| `conc_thc_mg_ml` / `conc_cbd_mg_ml` | number \| null | `thc_mg_ml`, `cbd_mg_ml` | Concentration |
| `conc_thc_uM` / `conc_cbd_uM` | number \| null | `thc_uM`, `cbd_uM` | In vitro molar |
| `pct_thc` / `pct_cbd` | number \| null | `thc_pct`, `cbd_pct` | Product % |
| `puff_count` | integer \| null | `puff_count` | Inhaled contexts |
| `duration_days` | number \| null | `duration_days` | |
| `inhaled_exposure_duration` | string \| null | same | Free text as reported |
| `administration_frequency` | string \| null | same | |
| `treatment_duration` | string \| null | same | Often in vitro |
| `repeat_exposure_count` | integer \| null | same | |
| `exposure_regimen_bin` | string \| null | same | Labels **`pending_biomedical`** |
| `multiple_doses` | boolean | same | Design flag |
| `multiple_time_intervals` | boolean | same | Design flag |
| `sample_size` | integer \| null | `sample_size` | Replicate rules Q9 |
| `replicates_notes` | string \| null | **new** | Free text only; no inferred N |

All scientific “what counts as a dose vs concentration vs regimen” language: **`pending_biomedical`** (Q8, Q9).

---

## D. Product identity / strain (supporting)

| Field | Type | Maps from | Notes |
|-------|------|-----------|-------|
| `strain_reported` | string \| null | `strain_reported` | As written; do not invent |
| `strain_normalized` | string \| null | `strain_normalized` | Chemotype mapping **`pending_biomedical`** |

Engineering note: legacy prompts sometimes stuffed ligands/suppliers into `strain_reported`. Year-1 contract should prefer `product` + optional `test_substance_reported` (deferred rename) after Shawn — **no new scientific categories invented here**.

---

## E. Publication / ingest (catalog supporting — not Compare spine)

| Field | Maps from | Notes |
|-------|-----------|-------|
| `publication_type` | same | Keep separate from `research_type` |
| `study_type_legacy` | `study_type` | Preserve during transition; do not treat as final ontology |
| `ingestion_status` | same | Node 0 relevance |

---

## F. IDEAS / SGBA+ representation hooks (reported-only)

**Rule:** Capture **only when the source reports**. Never infer from author names, pronouns, country, or stereotypes. Scientific coding of values: **`pending_biomedical`** (Q12).

| Field | Type | Maps from | Notes |
|-------|------|-----------|-------|
| `sgba_sex_reported` | string \| null | `population_sex` | Reported sex categories only |
| `sgba_gender_reported` | string \| null | **new hook** | Only if paper reports gender (distinct from sex) |
| `sgba_population_age` | string \| null | `population_age` | |
| `sgba_population_descriptors` | string[] \| null | **new hook** | Free/reported population descriptors; no invented taxonomy |
| `sgba_reporting_present` | boolean | **new tech** | true iff any SGBA+-relevant fact was explicitly reported |
| `ideas_representation_notes` | string \| null | **new hook** | Human-review notes only; not model invention |

`inclusion_criteria` / `exclusion_criteria`: deferred for Compare; optional gold detail.

---

## G. Provenance & review (technical — every extraction)

See `methods.schema.json` provenance envelope:

`schema_version`, `extractor_id`, `model_id`, `prompt_id`, `confidence`, `source_tier`, `source_span`, `human_override`, `reviewed_at`

Plus honesty labels for UI: `ai_assisted` | `human_reviewed` (derived from override/review fields — technical).

---

## H. Missingness (technical — every field value slot)

Enum (proposed, technical):

`reported` | `not_in_available_source` | `source_unavailable` | `not_applicable` | `uncertain` | `extraction_failed`

Definitions for annotators: `annotation-guide-draft.md`. Not biomedical ontology.

---

## I. Year-1 mandatory vs deferred

**Not decided here.** Embed only: Shawn Q1 must mark each Core field mandatory vs deferred. Until then, treat Core list as **proposed candidates**, Supporting as optional extraction, Deferred as hooks only.


## J. Expert review record (`$defs.expert_review`, wp1.1.0-draft)

| Field | Type | Notes |
|---|---|---|
| review_id | string | Unique review id |
| paper_id | int | FK papers.id |
| study_group_id | string\|null | Groups duplicates / same-study papers into the same split; **pending_biomedical** (Q7, MCK-90) |
| source_span | string | Reference to the passage reviewed |
| claim_field | string | Contract field under review |
| claim_value_shown | any | Value shown to the reviewer, verbatim |
| verdict | enum | `agree` / `disagree` / `cannot_judge_from_source`; required, no default; blank/unclear is invalid (never coerced to disagree) |
| verdict (name) | — | `cannot_judge_from_source` is the intended, final enum value name (not `cannot_judge` / `unclear`) |
| reviewer_confidence | int 1–5 | Ordinal; anchors **pending_biomedical** |
| rationale_text | string | Expert's submitted text (post-edit) |
| raw_transcript | string\|null | Stored separately; **never the label**; null when typed |
| input_mode | enum | `voice` / `typed` |
| stt_model_id | string\|null | null when typed |
| reviewer_id, reviewed_at, schema_version | string | `reviewed_at` ISO 8601; version `wp1.1.0-draft` |
| split_assignment_ref | string | Ref into Evaluation's split manifest, assigned **before** review; grouped by `study_group_id` |
| label_status | enum | `review_note` only (default). `gold`/`training` pending Q16 (Shawn, MCK-90) |

**Audio is never stored.** Sentry never receives the transcript or any form field.

## v0.2 additions (wp1.2.0-draft)

- **Guest public fields v0**: `schemas/guest_public_fields.json` (default-deny allowlist; link entries carry `relationship` in {cites, cited_by}, `paper_id`, `external_id`, `is_internal`). Guest stats never broken down by split, gold, verdict or reviewer.
- **study_group_id**: equals `paper_id` (Q7, Shawn Oct 29 2026).
- **reference_value**: `paper_id, field, value, raw_text, status (draft|expert_checked), checked_by, checked_at`. Internal only.
- **gold_badge**: paper-level; `paper_id, rater (verified sign-in), timestamp, raw_transcript, submitted_rationale` (separate), no audio. `split=test` ⇒ `storage=locked_store`. Rationale format pending_biomedical (free text for now).
- **normalized_field**: `raw_text` + `normalized_value` with `normalization_status: pending_biomedical`. Rules: `schemas/normalization_rules.json` (range formatting allowed; "once daily" vs "daily" pending_biomedical).
- **work_id**: grouping key; preprint/published versions and duplicate records of one work share a `work_id`. Splits are assigned by `work_id` (not paper_id). `study_group_id` stays = `paper_id`. DOI/title matches across splits produce a `work_merge_flag` for Methods confirmation — never auto-merged; a confirmed merge requires a new manifest version.

## v0.3 additions (wp1.3.0-draft) — eval gaps, fields only

Contract version **0.3** (`schema_version: wp1.3.0-draft`; `x-contract-version: "0.3"`). New fields on existing records are optional so v0.2 records stay valid during draft; v0.3 producers must emit them.

1. **gold_badge.decision** `gold|not_gold` and **awarded_by** (rater id). *Gold* = paper marked by the resident PhD as top-notch in structure/detail.
2. **relevance_grade** (new, keyed on `(query_id, pmid)`): `query_id, pmid, grade, rater, query_set_version, query_set_sha256` (required); optional `rater_verified, timestamp, split, storage, rationale_format`. Names/types aligned with the eval locked-store `relevance_grade` proposal. **`grade` 0/1/2 is a PLACEHOLDER** pending the Biomedical lead's decision on a multi-axis linear relevance score.
   - **PROPOSED axes (NOT schema fields yet):** `topic_match` 0/1/2 (0 = hard exclude); `study_type` category `original|review_meta_analysis|survey|reanalysis|other`; `design_correctness` 1–5; `reporting_depth` 1–5; gold badge; `relevance = w1*topic + w2*original + w3*design + w4*reporting + w5*gold`, weights **w1–w5 UNSET placeholders**.
3. **correction** (new): `record_id, supersedes_record_id, reason, corrected_by, at (ISO-8601 date-time), retracted (bool)`. State records are **append-only**; corrections supersede, never edit.
4. **Mapping rule** (`$defs.paper_pmid_mapping`): `paper_id` resolves to `papers.pmid`. If pmid is null the record is flagged `pmid_missing: true` and `manifest_eligible: false` — **excluded from manifests** (encoded via if/then).
5. **work_id** on `reference_value` and `gold_badge`, copied from the paper.
6. When `storage == locked_store`: `rater_verified` (must be true) and `rationale_format` are **required** (if/then). `rationale_format` placeholder enum `free_text`. **Rationale is never scored.**
7. **reference_value.split / storage**; `split=test` allowed only with `storage=locked_store` (if/then).
8. **gold_badge.split deliberately excludes `train`** — intentional: gold badges are evaluation labels and never training data.

**Q7 scope ruling:** one study = one original research paper. Surveys, market studies and re-analyses are not original research and are excluded from study sets (e.g. PMIDs 38757607, 36803833, 34265088).

### v0.3 review follow-ups

- **record_id** (string, non-empty): unique id of a state record and the target of `correction.supersedes_record_id`. Available on every record type (gold_badge, reference_value, relevance_grade, correction, paper_pmid_mapping, expert_review, work_merge_flag, and the top-level methods document). **Optional** on v0.2-era types so v0.2 records stay valid; **required** on the v0.3 types `relevance_grade`, `correction`, `paper_pmid_mapping`.
- **Split manifest:** manifest v0.1 (owned by Evaluation) has no `work_id` column. Split manifest **v0.2 should add a `work_id` column** so splits can be assigned/checked by work_id. Not changed here.
- **rationale_format:** enum `pending_biomedical|free_text` (pending_biomedical kept so v0.2 working-store records stay valid); locked-store records must be `free_text`. Rationale format is pending the Biomedical lead; no structured option yet. Rationale is never scored.
- **Locked store is test-only:** for gold_badge, reference_value and relevance_grade, `storage == locked_store` requires `split == test` (if/then). Dev records never go to the locked store.
- **Retractions:** a retraction is a `correction` with `retracted: true` whose `supersedes_record_id` is the target's `record_id` — never a store position/sequence number (replaces the locked-store `retracts_seq` proposal).

## v0.4 additions (wp1.4.0-draft) — PubMed metadata record

Contract version **0.4** (`x-contract-version: "0.4"`). `$defs.pubmed_metadata_record` describes each element of `data/pubmed_metadata_split_v0.1.json` (a JSON array) as written by `scripts/fetch_pubmed_metadata.py`. The existing file validates unchanged.

- **Required:** `pmid` (digit string), `title`, `authors` (list of "LastName Initials" or collective name), `journal`, `year` (integer), `doi`, `publication_types` (list), `missing`, `provenance`. No other keys allowed.
- **Nulls:** each bibliographic field is a value or `null`. **A null field must have a reason in `missing`** (`missing[field] = reason`; producer currently writes `"absent in PubMed record"`). `missing` keys are limited to those six fields.
- **provenance:** `source` ("NCBI E-utilities"), `endpoint` (efetch URL), `params` (query params minus `id`: db, retmode, tool), `retrieved_at` UTC `YYYY-MM-DDTHH:MM:SSZ`.
- **record_id:** optional, same definition as v0.3.

### Preprint flag (OPTIONAL, derived — pending Biomedical lead)

Preprint status is **derivable** from `publication_types` (contains `"Preprint"`); it is not stored. Evaluation flagged PMIDs 40502036, 36993751, 40235472 as preprints, and these are exactly the records with `"Preprint"` in the merged file. **No inclusion/exclusion rule for preprints is defined — pending Biomedical lead.**
