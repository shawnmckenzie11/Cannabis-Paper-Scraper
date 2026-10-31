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
- **normalized_field**: `raw_text` + `normalized_value` with `normalization_status: pending_biomedical` (widened in v0.6, see below). Rules: `schemas/normalization_rules.json` (range formatting allowed; "once daily" vs "daily" pending_biomedical).
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

## v0.5 additions (wp1.5.0-draft, #105)

`x-contract-version: "0.5"`

| Name | Where | Meaning | Status |
|---|---|---|---|
| `pmc_oa_xml` | `source_tier` value | Value came from PMC Open Access XML full text. | technical |
| `source_license` | optional in `provenance_envelope` | `license_raw` (verbatim or null), `license_source` (`pmc_oa_list`, `publisher_page`, `unknown`), `fulltext_storable` (pinned `false`), `pending_biomedical` (`true`). Only quoted spans and extracted fields are kept until Shawn sets the license-to-storage policy. | policy pending Shawn |
| `validation` | `split` value | Held-out check scored only by Evaluation; capped and logged. `test` never appears in extraction runs. | technical |
| `record_id` | top-level | Required on `wp1.5.0-draft` records; stable across reruns. Current record = latest non-retracted record in its correction chain. | technical |
| `extraction_run_manifest` | `$defs` | Per-run manifest: split (dev or validation only), mode (`scored` or `dry_run`; dry runs have null `model_ids` and no records), git ref, config and prompt hashes, records, excluded papers with reason. | technical |
| `run_event` | `$defs` | Run-log event with ids and a reason code only (`quote_not_found`, `extraction_failed`, `parse_downgrade_abstract_only`). | technical |

Also in v0.5: `test` records must live in `locked_store` (now enforced both ways), `run_event.field` must be a contract field name, and the run manifest's `contract_version` is pinned to `"0.5"`.

### v0.5 changelog

The contract version stays `"0.5"`; these are additive changes, and every record valid before them is still valid.

- #109: `extraction_run_manifest.excluded[].reason` accepts `consecutive_failures`, for papers skipped after the runner stops on repeated failures. The full set is `budget_cap`, `pmid_missing`, `source_unavailable`, `consecutive_failures`. Evaluation leaves `budget_cap` papers out of the score; `pmid_missing`, `source_unavailable` and `consecutive_failures` papers count as recall misses on every gold field for that paper, with each reason's count reported separately.

## v0.6 additions (wp1.6.0-draft, MCK-425): product and strain vocabulary — PhD to confirm

`x-contract-version: "0.6"`. Shawn's picks (Oct 30, 2026). **Every value and rule below is `phd_to_confirm`** until the Biomedical lead signs off. Vocabularies live in `schemas/normalization_rules.json` (`vocabularies`, `review_policy`, rules `mck425_*`).

| Name | Where | Meaning | Status |
|---|---|---|---|
| `normalized_field` | `$defs` (changed) | `raw_text` (verbatim, required, never overwritten) + `normalized_value` + `normalization_status` + `rule_id` + `vocabulary_version` + optional nullable `external_id`. A non-null `normalized_value` must carry `rule_id` and `vocabulary_version`. | technical |
| `normalization_status` | in `normalized_field` | Widened from const `pending_biomedical` to `pending_biomedical`, `approved`, `unmapped`, `needs_review` (4C). `unmapped` keeps raw only (`normalized_value` null), never guessed. | phd_to_confirm |
| `product` | `fields` | 1B: list of `product_entry` = `origin` (`plant_derived`, `synthetic`, `biosynthetic`) + `preparation` (`whole_flower`, `full_spectrum_extract`, `broad_spectrum_extract`, `isolate`, `formulated_drug`) + optional `substance`, each a `normalized_field`. Required shape on `wp1.6.0-draft`; older records keep the free string list. | phd_to_confirm |
| `substance` | in `product_entry` | Receptor agonists/antagonists and named drugs (e.g. Epidiolex, nabilone, dronabinol) are their own values, not collapsed into a class. Vocabulary seeded only with those three drugs. | phd_to_confirm |
| `route` | `fields` | Unchanged; stays a separate field from product. | — |
| `strain_normalized` | `fields` | 2D: name cleaning only (case, spelling, alias) as a `normalized_field`; carries no chemistry. Required shape on `wp1.6.0-draft`. | phd_to_confirm |
| `chemotype_reported` | `fields` (new) | 2D: `I` / `II` / `III` set only when the paper reports cannabinoid content (`cannabinoid_content_reported: true` + `source_span`); otherwise null with `not_in_available_source`. A strain name never implies chemotype. | phd_to_confirm |
| `review_policy.rare_raw_string_threshold_n` | `normalization_rules.json` | 4C: a rule is approved once by the Biomedical lead; raw strings seen fewer than N times go to `needs_review`. **N=3 is a PLACEHOLDER — PhD / Shawn to confirm.** | placeholder |
| `external_id` | vocab values + `normalized_field` | 3C: empty nullable slot for later ChEBI / RxNorm. | technical |

Also in v0.6: `record_id` is required on `wp1.6.0-draft` records, `run_event.field` includes `chemotype_reported`, and the run manifest `contract_version` accepts `"0.5"` or `"0.6"`.

## v0.7 additions (wp1.7.0-draft): reporting completeness — pending_biomedical

`x-contract-version: "0.7"`. Based on the PhD lead conversation of Oct 31, 2026. All additive: nothing existing is removed or renamed, and every earlier record stays valid. **Every value, tier and vocabulary below is `pending_biomedical` / `phd_to_confirm`.**

The idea: for this database, a paper's quality is how well it reports the details we need to reproduce the work. What counts as "needed" depends on how sure we are of the study type.

| Name | Where | Meaning | Status |
|---|---|---|---|
| `reporting` | top level (new) | One `field_reporting` entry per completeness item key (`research_type`, `strain_or_product_name`, `supplier`, `form`, `major_cannabinoids`, `route`, `dose`, `frequency`, `duration`, `vehicle_solvent`, `storage`). A missing key means `not_yet_assessed`. | technical |
| `field_reporting.status` | `$defs` | `reported`, `not_reported` (section read, detail absent), `could_not_check` (section not fetched or parsed), `not_applicable` (set by the decision-tree branch / study type), `not_yet_assessed` (default). Records written before v0.7 are `not_yet_assessed`, **never** backfilled as `not_reported`. | technical |
| `field_reporting.location` | `$defs` | `methods`, `supplementary`, `abstract`, `discussion`, `unknown` (no `cited_prior_paper`: "as previously described [ref]" is `not_reported`, K1). Required when status is `reported`; must be null or absent otherwise. | technical |
| `field_reporting.evidence_quote` | `$defs` | Verbatim span supporting the status, optional. | technical |
| `field_reporting.check_failure_reason` | `$defs` | `fetch_failed`, `parse_failed`, `source_not_available`, `budget_cap`, `source_not_fetched`. `source_not_fetched` is a **deliberate scope choice** (e.g. a main-text-only run that skips supplements), distinct from `fetch_failed`, which is an error. Required when status is `could_not_check`; null or absent otherwise. The contract only records the reason. **How each reason is scored is owned by CRN Evaluation's scorer** (Pipeline proposes `fetch_failed` / `parse_failed` count as misses). | technical |
| `supplier` | `fields` (new) | Company or source the product was procured from. Its own field, separate from strain/product name (**U2, answered**). Not applicable for clinical studies. | pending_biomedical |
| `vehicle_solvent` | `fields` (new) | Vehicle or solvent used to deliver the product (oil does not dissolve in cell media). | pending_biomedical |
| `storage` | `fields` (new) | How the product was stored, free text. Good practice, not required. |
| `storage_temperature`, `milled_ground`, `protected_from_light` | `fields` (new) | G1 good-practice storage details (temperature as reported; milled/ground and light protection = `yes`/`no`/`not_reported`). Recorded only, never affects any tier. |
| `randomized` / `arms` / `blinding` | `fields` (new) | Clinical design. `randomized` (`yes`/`no`/`unknown`) is the scope gate; `arms` (`single_arm`/`controlled`/`unknown`) and `blinding` (`open_label`/`single_blind`/`double_blind`/`unknown`) are recorded only. |
| `cell_line_or_tissue_source`, `comparison_group` | `fields` (new) | In vitro source (V2) and clinical comparison arm (V3). |
| `field_reporting.confidence`, `.needs_review` | `$defs` | Optional extraction confidence and human-review flag on any reporting item. | pending_biomedical |
| `form` | `fields` (new) | Physical form; vocabulary `product_form` (includes `topical`). | phd_to_confirm |
| `cannabinoids` | `fields` (new) | List of cannabinoids as reported (not THC/CBD only), each with optional amount/unit/span. Names from vocabulary `cannabinoid` (each value has `full_name` and `synonyms`; THC/CBD `core`, others `candidate`; drug names like dronabinol stay in `product_substance` per MCK-425). A non-empty list needs `missingness: reported` and a `source_span`, same gating as `chemotype_reported`. The existing THC/CBD numeric fields stay. | phd_to_confirm |
| `route` | `fields` | Unchanged shape. New vocabulary `route` splits oral into `oral_diet` and `oral_gavage` (dose is uncertain when mixed in food). Route stays separate from product (MCK-425). | phd_to_confirm |

### Completeness tiers (`schemas/completeness_rules.json`, rule `crn_reporting_completeness` v0.7.0, pending_biomedical)

| Tier | Items |
|---|---|
| classification | research type (picks the study-type tier; recorded, not part of gold) |
| universal (every study) | strain/product name, supplier (not applicable for clinical), form, major cannabinoids, route (provisional). All must be `reported` for gold. Location is recorded but never a pass condition. |
| in_vivo (V1, provisional) | required always: route, dose, duration, number of animals. Secondary: species, animal strain, sex, age or weight, frequency, vehicle (key `vehicle_solvent`). Same algorithm and `secondary_cutoff` as in vitro. |
| in_vitro (V2, provisional) | required always: cell line or tissue source, concentration, exposure time. Secondary: species of origin, vehicle/solvent, replicates. |
| clinical (V3, provisional; RCTs only) | all required, no share: number of participants, route, dose, frequency, duration, participant age or sex (either counts), diagnosis or condition, comparison group (may carry `confidence` + `needs_review`). Scope gate on `randomized`: `yes` → scored; `no` → `out_of_scope`; `unknown` → `unverified` (never guessed). `arms` and `blinding` are recorded only. |
| good_practice (G1) | storage temperature, milled/ground, protected from light. Recorded only; never counts toward gold or any share. No contamination or batch-testing fields. (Replaces V4; `storage_duration` dropped: it existed only in this draft.) |

**Grades are explicit, never blank:** `gold`, `near_gold`, `unverified`, `not_scored` (reviews, case reports, observational / self-report), `out_of_scope` (non-randomized interventional trials), `not_yet_assessed` (default).

**Observational / self-reported studies, reviews and case reports** skip every current tier, including universal: the helper returns `not_scored` for all tiers. Only future exposure fields would be scored for them, after the PhD lead signs off.

Completeness is **computed** from the statuses (`methods_completeness.compute`), never stored as truth. Output per tier: a universal grade, a study-type fraction (reported / (reported + not_reported); `could_not_check` and `not_yet_assessed` counted separately), and good practice on its own. **There is no combined score.**

Universal grade (papers are always kept, never dropped):

- `gold`: every applicable universal item is `reported`.
- `near_gold`: at least one applicable item is `not_reported`. Returns a `missing` list, plus a `not_checked` list if any. Missing takes precedence over unchecked.
- `unverified`: nothing missing, but at least one item `could_not_check` (or `not_yet_assessed`). Returns `not_checked` with each item's `check_failure_reason`. This is not a reporting gap.

Items marked `not_applicable` (status, or supplier on clinical studies) are left out and never counted missing.

**Required + secondary tiers (in vitro now; same mechanics planned for in vivo).** `secondary_cutoff` default 0.80, overridable per call.

1. Any required item `not_reported` → `near_gold`.
2. Else any required item `could_not_check` → `unverified`.
3. Else secondary share, worst case (unchecked counted as not reported; denominator = applicable items including unchecked) and best case (unchecked counted as reported). Worst ≥ cutoff → `gold`; best < cutoff → `near_gold` (share too low); otherwise `unverified`. Both shares and the `missing` / `not_checked` lists are returned.

**Route requirements (TOP2, partially answered, provisional).** Route of administration is required (universal item). Which dose / cannabinoid fields are expected then depends on study type + route, encoded in `route_requirements` from the extraction flowchart (`Cannabis_Classification_Decision_Tree`, Node 2A / 6 / 7) only. If route is not reported, route counts as missing (`near_gold`) and route-dependent fields get no requirement (not counted as extra misses); this rule is provisional pending Shawn. What the flowchart doesn't settle is listed under `route_requirements.open`.

**Cannabinoid vocabulary.** `values` are locked (including Δ8-THC, CBGA, CBL, 11-OH-THC confirmed by the PhD on 2026-10-31); each has `full_name` and `synonyms`. THC-COOH, CBCA, THCVA, CBDVA, CBE, CBT sit in `candidates_pending_phd`: recorded, never scored.

### Answered (Shawn, 2026-10-31)

- **U1:** universal = strain/product name, supplier, form, major cannabinoids; all must be reported for gold.
- **TOP4:** location is never a pass condition anywhere in the universal tier; it is still recorded.
- **V2 (provisional):** in vitro tier as above.
- **TOP2 (partial):** requirements depend on study type and route, per the flowchart; open items listed in `completeness_rules.json`.
- **V3 (provisional):** clinical tier for RCTs only, as above.
- **Non-randomized interventional trials** (single-arm, open-label non-randomized) are out of scope: no gold rule, no comparison-group check.
- **Q1:** age or sex, either counts (provisional). **Q2:** resolved by the out-of-scope rule.
- **G1 (replaces V4):** good practice is exactly storage temperature, milled/ground, protected from light. Never affects tier.
- **C1 (dose wording):** a dose given only as a range counts as `reported`. "Per protocol" counts as `reported` only if the paper or its cited protocol gives an actual number; otherwise dose is `not_reported`. Examples:
  - "mice received 5–20 mg/kg THC i.p." → reported (range).
  - "dosed per protocol (10 mg/kg, see Ref 12)" → reported.
  - "dosed per protocol [12]", and ref 12 states 3 mg/kg → reported (put the number and source in `evidence_quote`).
  - "dosed per protocol" with no number anywhere → not_reported.
- **C2 (vague duration):** "chronic", "long-term", "acute" etc. with no actual time do **not** count as duration reported; duration stays `not_reported`. The term is kept on the duration reporting item as `duration_category` (closed enum: `acute` — acute, single dose, single administration; `subacute` — sub-acute; `subchronic` — sub-chronic; `chronic` — chronic, repeated; `long_term` — long-term, prolonged, extended; `other`) plus `duration_category_verbatim` (author's words). Search/filter label only; never affects any tier; no time thresholds (authors' wording only). Synonyms live in `normalization_rules.json` → `duration_category`. It sits in the per-field reporting block, so it moves into each track's reporting block when the track shape lands.
  - "chronic THC exposure" (no time) → duration `not_reported`, `duration_category: chronic`, verbatim "chronic".
  - "chronic (21 days) THC" → duration `reported`, `duration_category: chronic`.
- **TOP1 (final):** "as previously described [ref]" = `not_reported` (`not_reported_reason: cited_elsewhere`). Cited papers are never fetched. Example: "Rats were dosed as previously described (Smith 2015)." → dose `not_reported`, the sentence goes in `evidence_quote`.
- **TOP3:** a number without units = `not_reported` (`not_reported_reason: missing_units`). Example: "animals received 10 of THC daily" → dose `not_reported`; "10 mg/kg" → reported.
- **TOP4 (confirmed, closed):** a missing universal item always blocks gold, no exceptions (even with a signed-off reference value: only a numeric one fills `reference_fields`, see P1). TOP4 applies only to items required for the study type, so supplier stays `not_applicable` for clinical (resolved).
- **U2:** supplier is its own field, separate from strain/product name; for clinical studies it is `not_applicable`.

### Open PhD questions (not decided)

1. Which cannabinoids are "major"? Pending Shawn. Default until then: any cannabinoid content the paper measured counts as reported.
2. **P1 tier effect:** do signed-off reference values affect the tier (A no effect / B counts as reported / C separate flag)? Default A for non-numeric values; numeric signed-off values currently fill `reference_fields` (see P1 section).

### not_reported_reason (optional, never affects tier)

Allowed only when status is `not_reported`: `cited_elsewhere` (TOP1), `vague_term_only` (C2 duration), `protocol_without_number` (C1), `government_program_unnamed` (P2), `product_named_content_missing` (P1 gap-log cases), `missing_units` (TOP3: number without units), `absent` (default: nothing found).

### Check-failure reasons (all mean `could_not_check`)

`fetch_failed`, `parse_failed`, `source_not_available`, `budget_cap`, `source_not_fetched` (deliberate scope choice, distinct from `fetch_failed`), `figure_not_read` (a figure holds the detail but reading it was not attempted; no figure-reading budget yet), `low_confidence_read` (read, but too uncertain to score). None of these is ever a miss: they are excluded from precision/recall and counted separately. Additive only; no value renamed or removed.

### comparison_group review fields (V3)

`extraction_confidence` (`high`/`medium`/`low`), `needs_review` (bool), `review_reason` (`no_named_control_arm`/`arm_only_in_table_or_figure`/`other`, only allowed when `needs_review` is true). Available on any reporting item; used for comparison_group.

### Computed track shape (never stored)

Per track (universal, study-type tier): `tier`, `missing`, `not_checked`; plus `overall_tier`. All are **computed at read time** from the stored reporting statuses and never written to the database. API responses carry them together with `rule_version` and the `secondary_cutoff` used, so any grade can be reproduced.

### P1: product reference values (Shawn, 2026-10-31; design only, no DB migration)

No auto-fill of cannabinoid content from labels or monographs. If the paper does not report it, the field stays `not_reported`.

Proposed tables (not in the schema yet; land with the gated DB migration):

1. `product_content_gap_log`: `log_id`, `paper_id`, `track_id` (nullable until tracks land), `product_name` (verbatim), `product_key` (normalized), `field` (e.g. cannabinoid THC content), `logged_at`, `extraction_run_id`, `status` (`open` | `resolved`).
2. `product_reference_value`: one signed-off known value per `product_key` + `field`: `value`, `unit`, `source_citation` (label / monograph / DOI / URL), `signed_off_by` (role `biomedical_lead` | `grad_reviewer` + id), `signed_off_at`, `rule_version`, `superseded_by`.
3. `paper_reference_link`: `paper_id`, `track_id`, `field`, `reference_value_id`, `linked_at`, `linked_by`, `revoked_at`. Signed-off values live only in `product_reference_value` and attach through this link table; they never write over paper fields. The apply step is idempotent, with unique key (`paper_id`, `track_id`, `field`, `reference_value_id`). Revoking sets `revoked_at`, and the shown value reverts to the paper's (`not_reported`).
4. Per-field flag `value_source` = `paper` | `signed_off`. The reporting status stays `not_reported`, and the signed-off value is shown with its own provenance.

The first prod run needs Shawn's go-ahead and a fresh snapshot.

Evaluation excludes reference values from extraction accuracy.

**Tier effect: OPEN QUESTION for Shawn.**
- A) No effect: the paper stays `near_gold`, with the missing list annotated "reference value available". **Default until answered.**
- B) Counts as reported for gold.
- C) A separate tier flag, e.g. `gold_with_reference`.

### Computed tier output (P1 additions)

`compute()` also returns `reference_fields` (items satisfied only by a numeric signed-off value, `value_source: signed_off`; empty if none), `missing_reference_available` (items with a non-numeric signed-off value: option A, no tier effect), `signoff_set_hash` (sha256 of the active `paper_reference_link` rows, so frozen runs are reproducible), `rule_version` and `secondary_cutoff` used. **Any future tier cache must be invalidated when `rule_version`, the cutoff, or the sign-off set changes (add, replace or revoke).**

### Storage / database

This PR changes the contract only. Writing `reporting` to the database is a **separate later migration**, gated on CRN Reliability fixing Alembic on prod (missing psycopg, unknown stamped revision, backups off).

Also in v0.7: `record_id` is required on `wp1.7.0-draft` records (same as v0.6), `run_event.field` includes the new fields, and the run manifest `contract_version` accepts `"0.7"`.

### Changelog

- **v0.7 (wp1.7.0-draft, 2026-10-31):** per-field reporting block with `check_failure_reason`; completeness tiers as a versioned rule; `supplier`, `vehicle_solvent`, `storage`, `form`, `cannabinoids` fields; `product_form` and `route` vocabularies. Additive only.
