# Year-1 methods field dictionary (draft)

Status: draft for Director and Reliability review. Every scientific allowed-value list is `pending_biomedical` (Shawn). This file names slots and storage rules. It does not define what a study design, dose, sex category, or outcome is.

Machine contract: `schemas/methods.schema.json` (`schema_version` `wp1-methods-draft-0.1`).

Baseline for current columns: `docs/methods/wp1-baseline-audit.md`.

## Rules that apply to every slot

- Store the source wording in `raw_text` when missingness is `reported`.
- Leave `normalized_text` empty until Shawn approves a mapping. Do not copy code comments from `schema.sql` or extractor docstrings into the normalized value.
- Missingness is one of: `reported`, `not_in_available_source`, `source_unavailable`, `not_applicable`, `uncertain`, `extraction_failed`. Definitions of when a field is applicable are `pending_biomedical`; until then, do not mark `not_applicable` in annotated gold.
- Abstract-tier silence uses `not_in_available_source`. It does not mean the full paper omitted the fact.
- Sex and gender are different `field_id`s. Capture a value only when the source states it. Do not infer either from names, locations, pictures, or the model species.
- One paper may have many assertions. `experiment_id` and `arm_id` are optional opaque strings so arms are not collapsed. How to split an arm is `pending_biomedical`.
- Each assertion carries the provenance envelope in the schema (version, extractor, model, prompt, confidence, source tier, source span, human override, reviewed time).

## Method and context slots

| `field_id` | Seed need (uninterpreted) | Current store | Year-1 | Allowed values |
| --- | --- | --- | --- | --- |
| `study_type` | Study design, including mixed designs | `papers.study_type` | refine | `pending_biomedical` |
| `model_population` | Species, strain or cell model, human population | `papers.species` plus free text in age/sex columns | refine | `pending_biomedical` |
| `product_composition` | Product and cannabinoid composition, source wording preserved | `papers.cannabis_type` (runtime patch) and percent/concentration columns | refine | `pending_biomedical` |
| `route` | Route of administration or exposure | `papers.exposure_method` | refine | `pending_biomedical` |
| `dose_or_concentration` | Dose or concentration with units | Unit-baked columns (`dose_mg`, `thc_*`, `cbd_*`) | refine | `pending_biomedical`; no conversion in this draft |
| `exposure_duration` | How long the exposure or treatment lasted | `duration_days`, `inhaled_exposure_duration`, `treatment_duration` | refine | `pending_biomedical`; do not merge those columns here |
| `administration_frequency` | How often the exposure was given | `papers.administration_frequency` | keep | `pending_biomedical` |
| `sample_size` | Sample-size denominator, preferably per arm | `papers.sample_size` | refine | `pending_biomedical` |
| `comparator` | Comparator or control | None | defer | `pending_biomedical` |
| `outcome_measure` | Methodological endpoint, text-supported only | `papers.outcome_domain` (coarse labels only) | refine | `pending_biomedical` |
| `outcome_time_point` | When the outcome was measured | None | defer | `pending_biomedical` |
| `strain_reported` | Strain or cultivar as written | `papers.strain_reported` | keep | Source string only. Chemotype mapping is not this field. |
| `puff_count` | Count when the source reports puffs | `papers.puff_count` | keep | Integer from the source, or a missingness code |
| `repeat_exposure_count` | Repeat count when the source reports one | `papers.repeat_exposure_count` | keep | Integer from the source, or a missingness code |

`exposure_regimen_bin` and `strain_normalized` stay in the database and are out of this dictionary until Shawn accepts or rejects them. See the audit.

## IDEAS / SGBA+ slots

These slots exist so reporting gaps can be stored without inventing identities. Value lists are `pending_biomedical`. Empty is not a demographic category.

| `field_id` | What may be stored | Year-1 persistence |
| --- | --- | --- |
| `sex_reported` | Biological sex wording the source uses, including animal or cell-donor sex when the source states it | Schema hook. Legacy column is `population_sex`. Do not add a second column in this PR. |
| `gender_reported` | Gender-identity wording the source uses | Schema hook only. No legacy column. |
| `age_reported` | Age wording the source uses | Schema hook. Legacy column is `population_age`. |
| `race_ethnicity_reported` | Race or ethnicity wording the source uses | Schema hook only. |
| `indigenous_identity_reported` | Indigenous identity wording the source uses | Schema hook only. |
| `disability_reported` | Disability wording the source uses | Schema hook only. |
| `socioeconomic_reported` | Socioeconomic wording the source uses | Schema hook only. |
| `geographic_context_reported` | Geographic context the source states | Schema hook only. |

If the source does not state the attribute, missingness is `not_in_available_source` or `source_unavailable`, never `reported`.

## Explicitly not Year-1 methods gates

| Item | Why it is absent from the contract |
| --- | --- |
| `inclusion_criteria`, `exclusion_criteria` | Already stored; already excluded from alignment. Not a seed critical field. |
| `tab_*` flags | Dashboard lane. |
| Harvest timestamps, citation counts, summaries | Not methods assertions. |
| Effect sizes, risk-of-bias scores, pooled estimates | Out of the seed extraction pilot. |
