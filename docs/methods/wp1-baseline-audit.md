# WP1 baseline audit — existing fields vs seed methods needs

Status: draft for Director and Reliability review. Biomedical adjudication is open. This note does not deploy, does not change Fly, and does not define scientific meanings.

Tip audited: `3856758` (`fix(cursor): environment.json ports as objects (#77)`), which includes merged env unblock PR #76.

## Sources inventoried

| Source | What it actually holds |
| --- | --- |
| `schema.sql` | SQLite `papers` DDL. Comments on columns are legacy hints, not an approved vocabulary. |
| `db_manager.py` `_PAPERS_PATCH_COLUMNS` / `columns_to_add` | Idempotent `ALTER TABLE` patches. `cannabis_type` and `tab_*` flags live here and are absent from the `schema.sql` `CREATE TABLE`. |
| `rules_config.json` | `field_groups`, decision nodes, and the shared extraction prompt (`version` 2.7.0). Prompt output lists `multiple_doses` and `multiple_time_intervals`, which are not `papers` columns. |
| `classification_schema.py` | Routing taxonomy helpers: publication type, review subtypes, ingestion status. Not a methods ontology. |
| `subnode_field_scopes.py` | Calibration field lists for `node2a`, `node2b`, `node2c`, and Node 7 exposure paths. |
| `content_tiers.py` `METHODS_HEAVY_FIELDS` | Fields dropped from alignment when the content tier is abstract-only. |
| `migrations/versions/2d3a2de95a99_create_heuristics_and_tasks_tables.py` | The only Alembic revision. It creates `heuristics_rules` and `background_tasks`. It does not version `papers`. |
| `docs/projects/project-1/architecture-design-document.md` | §5.2–§5.3 and §10.1. Several §5.3 names are illustrative and have no column. §5.2 lists `classification_source`, which is not in `schema.sql`. |
| Seed needs | The path `ai-seed-fom/` is not in this repository at the tip above. Needs below are the methodology requirements in the 22 September 2026 AI Seed materials: proposal §2.2 ontology (`AI_Proposal_NM 3.0.docx`) and the consultant plan sections on critical fields, the minimum data model, and IDEAS/SGBA+ checkpoints (`Cannabis_Research_Navigator_Consultant_Plan.docx`). |

Postgres is the production source of truth for paper rows (`docs/agent_automation_plan.md`). This audit does not reconcile the older SQLite-as-source wording later in that same plan. That reconciliation is out of scope for this docs slice.

Paper Scraper Engineer surfaces (Analyze chrome, harvest rewrites, `tab_*` dashboard flags) are listed only so they are not mistaken for Methods work. This PR does not change them.

## How to read dispositions

| Disposition | Meaning in this draft |
| --- | --- |
| keep | Slot stays. Year-1 contract can store it without a new scientific definition. |
| refine | Slot stays, but shape, missingness, or provenance must change before it is a Methods assertion. Allowed values stay `pending_biomedical`. |
| drop | Do not carry this slot into the Year-1 methods contract. |
| defer | Real seed need or existing column, but not in this PR’s implementation. No migration here. |

Nothing in the “rationale” column answers a biomedical question.

## Bibliographic identity and text availability

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `pmid`, `doi`, `semantic_scholar_id`, `title`, `authors`, `journal`, `year`, `abstract`, `publication_date` | `schema.sql` | Publication identity | keep | Identity columns already exist; Methods assertions point at them. |
| `full_text_link`, `open_access` | `schema.sql` | Text availability, licence | refine | A link is not retrieved text. Availability becomes `source_tier` plus missingness, not a new boolean. |
| Query version, retrieval time, document hash, parser version, study-family id | Absent | Minimum data model | defer | Seed asks for them. Adding columns is a later Alembic change, not this PR. |
| `date_harvested`, `citation_count`, `summary` | `schema.sql` | Not a methods field | defer | Harvest and display metadata. Leave the harvest path alone. |

## Routing

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `ingestion_status` | `schema.sql`; `classification_schema.py` | Inclusion boundary | keep | Node 0 gate. Changing who is in the corpus is a Shawn question, not a rename. |
| `publication_type` and review subtypes | `schema.sql`; `classification_schema.py`; `rules_config.json` nodes 1 and 3 | Reviews vs original research | keep | Coarse routing labels already exist. |
| `study_type` | `schema.sql` (free text / JSON list in practice); scopes | Study design: in vitro, in vivo, clinical/human, mixed | refine | Keep the column as the current route. Year-1 assertions must not treat today’s labels as the approved design list. |
| Architecture names `review_type`, `topic_scope`, `included_studies_count`, `synthesis_notes` | Architecture §5.3 only | Secondary literature | defer | Illustrative. Not columns. Restricted extraction for reviews stays a Shawn question. |

## Exposure, product, strain

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `exposure_method` | `schema.sql`; all Node 2 scopes; prompt | Route | refine | Paper-level list. Seed wants experiment/arm linkage. Route vocabulary is `pending_biomedical`. |
| `cannabis_type` | Prompt, scopes, `db_manager` patch; missing from `schema.sql` `CREATE TABLE` | Product and composition | refine | Runtime column exists; DDL is drifted. Composition beyond the current prompt list is `pending_biomedical`. |
| `strain_reported` | `schema.sql`; `extractor.extract_strain_info`; `METHODS_HEAVY`; alignment-excluded | Strain when stated | keep | Raw source string. Methods-heavy, so abstract tiers do not gate it. |
| `strain_normalized` | `schema.sql` comment “Chemotype I/II/III”; extractor chemotype map | Terminology normalization that preserves source wording | defer | Normalization rule is an existing code behavior, not an approved ontology. Do not extend the map in this PR. |

## Dose and concentration

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `dose_mg` | `schema.sql`; `METHODS_HEAVY` | Dose with units | refine | Unit is baked into the column name. Seed wants raw value, raw unit, and a separate normalized value. |
| `thc_pct`, `cbd_pct` | `schema.sql`; scopes; `METHODS_HEAVY` | Cannabinoid composition | refine | Keep as legacy numeric slots. They are not a composition record. |
| `thc_mg_ml`, `cbd_mg_ml`, `thc_mg_g`, `cbd_mg_g`, `thc_mg_kg`, `cbd_mg_kg`, `thc_uM`, `cbd_uM` | `schema.sql`; branch scopes; `METHODS_HEAVY` | Concentration with units | refine | Same unit-baked pattern. Product concentration is not defined here as delivered dose. |
| `puff_count` | `schema.sql`; inhaled Node 7 paths; `METHODS_HEAVY` | Exposure detail when reported | keep | Integer slot only. When it applies is `pending_biomedical`. |
| Architecture `dose_mg_kg`, `concentration_uM`, `intervention_type` | §5.3 examples | Dose / intervention | drop | Not columns. Covered only if Shawn maps them onto the unit-bearing assertion, which this draft does not do. |

## Time and regimen

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `duration_days` | `schema.sql`; clinical and in vivo scopes; `METHODS_HEAVY` | Exposure duration | refine | One numeric column cannot yet distinguish exposure, treatment, and follow-up. |
| `inhaled_exposure_duration` | `schema.sql`; `METHODS_HEAVY` | Exposure duration | refine | Separate string slot already exists. Relation to `duration_days` is `pending_biomedical`. |
| `treatment_duration` | `schema.sql`; in vitro scopes; `METHODS_HEAVY` | Exposure or treatment duration | refine | Kept as its own slot so in vitro timing is not forced into `duration_days`. |
| `administration_frequency` | `schema.sql`; prompt; `METHODS_HEAVY` | Frequency | keep | Free-text slot. Allowed normalizations are `pending_biomedical`. |
| `repeat_exposure_count` | `schema.sql`; in vivo scopes; `METHODS_HEAVY` | Regimen detail | keep | Integer when the source states a count. |
| `exposure_regimen_bin` | `schema.sql` comment “acute \| subchronic \| chronic”; in vivo scopes; prompt; `METHODS_HEAVY` | Not named as a seed field | defer | Bin edges are a scientific definition. Leave the column; do not add values in the new contract. |
| `multiple_doses`, `multiple_time_intervals` | Scopes and prompt JSON only | Multi-arm / multi-dose | refine | Flags are not durable columns. Year-1 contract uses repeated assertions instead of a boolean. |

## Sample size, comparator, outcome

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `sample_size` | `schema.sql`; node2b scope; `METHODS_HEAVY`; not in the node2a scope list | Sample size and per-arm denominator | refine | One paper-level integer. What it counts is `pending_biomedical`. |
| Comparator | Absent | Comparator | defer | Seed field with no column and no approved meaning. |
| `outcome_domain` | `schema.sql`; scopes; prompt | Outcome measure and time point | refine | Coarse multi-label only. Measure text and time point are separate Year-1 slots with values `pending_biomedical`. |
| Outcome time point | Absent | Outcome time point | defer | No column. Do not reuse exposure duration as a stand-in. |

## Population, criteria, IDEAS/SGBA+

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `species` | `schema.sql`; node2b scope; not in the prompt output example | Model / population, including species | refine | Host label exists. Cell model vs animal vs human is `pending_biomedical`. DDL and prompt output are drifted. |
| `population_age` | `schema.sql`; node2a scope; `extract_population_age` docstring says pediatric / adult / geriatric | Age where reported | refine | Keep capture-if-reported. Those three words are a code comment, not an approved list. |
| `population_sex` | `schema.sql` comment “male, female, both”; node2a scope; `extract_population_sex` | Reported biological sex, distinct from gender | refine | One column and no gender slot. Sex and gender stay separate keys; vocabularies are `pending_biomedical`. |
| Gender | Absent | Gender identity when the source states it | defer | Hook only in `schemas/methods.schema.json`. No column until Shawn sets the value list. |
| Race/ethnicity, Indigenous identity, disability, socioeconomic circumstances, geographic context | Absent | SGBA+ fields where explicitly reported | defer | Hooks in the schema. No inference, no value lists, no migration in this PR. |
| `inclusion_criteria`, `exclusion_criteria` | `schema.sql`; node2a scope; `ALIGNMENT_EXCLUDED_FIELDS` | Not in the seed critical-field list | defer | Already stored and already outside the alignment denominator. Not a Year-1 methods gate. |

## Provenance, locks, and evaluation metadata

| Field / group | Where | Seed need | Disposition | Rationale |
| --- | --- | --- | --- | --- |
| `classification_confidence`, `classification_timestamp`, `classifier_version` | `schema.sql` | Model/prompt version and confidence on each field | refine | Paper-level only. The new envelope is per assertion. Confidence stays a stored number, not a calibrated probability. |
| `expert_locked_fields` | `schema.sql` | Locked expert value | keep | Do not overwrite locks. The envelope’s `human_override` points at this behavior. |
| `feedback_audit` | `schema.sql` | Audit history | keep | Correction log already exists. Test-label handling is unchanged here. |
| `llm_calls_log` | `schema.sql` | Tokens, model, cost | keep | Run log, not a scientific field. |
| `classification_source` | Architecture §5.2 only | Extractor identity | defer | Named in the architecture doc, not in `schema.sql`. Envelope uses `extractor_id` instead of a silent column add. |
| Content tiers | `content_tiers.py` | Abstract silence is not full-text omission | keep | `pdf_extracted`, `abstract_reclassify`, `pdf_link`, `abstract_only` are the technical `source_tier` vocabulary. `METHODS_HEAVY_FIELDS` already keeps dose, duration, sample size, strain, and concentration off abstract alignment. |
| Experiment id, arm id, source span | Absent as columns | Experiment/arm and source passage | defer | Represented in the JSON Schema only. No Alembic revision in this PR. |

## Other-lane columns (not Methods)

| Field / group | Where | Disposition | Rationale |
| --- | --- | --- | --- |
| `tab_preclinical`, `tab_clinical`, `tab_unclassified_preclinical`, `tab_tangential`, `tab_review` | `db_manager.py` patches only | defer | Dashboard routing flags. Paper Scraper Engineer lane. |

## Alembic

Current head is `2d3a2de95a99` and it does not describe `papers`. Future methods columns belong in Alembic against Postgres. This PR adds no revision: the contract is `schemas/methods.schema.json` plus the docs in this directory.

## Drift to fix later (not in this PR)

- `cannabis_type` and `tab_*` are patched in `db_manager.py` and missing from the `schema.sql` `CREATE TABLE`.
- `multiple_doses` and `multiple_time_intervals` are in scopes and the prompt, not in the table.
- `species` is a column and a node2b scope field, and it is absent from the prompt’s output example.
- `classification_source` is documented in the architecture doc and absent from `schema.sql`.
- `schema.sql` comments and extractor docstrings state vocabularies (chemotype bins, sex bins, regimen bins) that this draft does not adopt.
