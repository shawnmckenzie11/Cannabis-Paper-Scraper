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

