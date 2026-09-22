# Methods schema proposal (WP1)

Status: draft for Director and Reliability review. Biomedical adjudication is open. No Fly deploy. No scientific definitions are decided in this note.

## What this slice is

WP1’s Methods slice is a contract, not a migration. The contract is `schemas/methods.schema.json`. Narrative companions:

- `docs/methods/wp1-baseline-audit.md` — keep / refine / drop / defer against current code
- `docs/methods/field-dictionary.md` — Year-1 slot names
- `docs/methods/annotation-guide-draft.md` — how to apply provenance, missingness, and SGBA+
- `docs/methods/biomedical-adjudication-questions.md` — questions for Shawn, unanswered

`schema_version` is `wp1-methods-draft-0.1`. Postgres remains the production source of truth. The next schema change that adds columns should be an Alembic revision. The current Alembic head (`2d3a2de95a99`) only creates `heuristics_rules` and `background_tasks`. This PR does not add a revision.

Paper Scraper Engineer work (Analyze chrome, harvest, `tab_*` flags) is untouched.

## What Methods owns technically

Methods owns the shape of an assertion:

- A publication id plus zero or more assertions, so one paper is not one dose.
- Optional `experiment_id` and `arm_id` strings so two arms are not stored as one number. Segmentation rules are not owned here.
- A closed `field_id` list for the Year-1 slots in the field dictionary.
- A closed missingness enum: `reported`, `not_in_available_source`, `source_unavailable`, `not_applicable`, `uncertain`, `extraction_failed`.
- A provenance envelope on every assertion: `schema_version`, `extractor_id`, `model_id`, `prompt_id`, `confidence`, `source_tier`, `source_span`, `human_override`, `reviewed_at`.
- `source_tier` uses the existing labels in `content_tiers.py`: `pdf_extracted`, `abstract_reclassify`, `pdf_link`, `abstract_only`. Those labels name which text was available to the extractor. They are not a claim about the study.
- `reported` requires `raw_text` and a non-empty `source_span.quote`. The other missingness codes require null values, so a blank is not stored as a finding.
- `human_override: true` requires `reviewed_at`. That flag records an expert lock; it does not by itself change `expert_locked_fields` (no writer in this PR).
- `confidence` is an optional number from 0 to 1. It is a stored score, not a calibrated probability.
- Sex and gender are different field ids. The schema has no combined sex/gender field and no closed demographic enum.

Legacy unit-baked columns (`dose_mg`, `thc_mg_kg`, and the rest) stay as they are. The new object can quote them in `raw_text` without declaring a unit conversion.

## What Methods does not own

Open scientific questions are listed in `docs/methods/biomedical-adjudication-questions.md` and marked `pending_biomedical` on normalized values in the schema. In particular, Methods does not decide:

- allowed labels for design, route, product, model, outcome, age, sex, or gender
- when a field is `not_applicable`
- whether a concentration may be converted to a dose
- what a sample-size integer counts
- how arms, comparators, and outcome time points are delimited
- whether chemotype bins or acute/subchronic/chronic bins survive

Until those answers exist, `normalized_text` is optional and unconstrained on purpose. Validators must not grow an enum from `schema.sql` comments.

## What a later implementation PR would add

Not this PR:

- Alembic columns or a child table for assertions, spans, and missingness on Postgres
- a writer that fills the JSON document from `extractor.py` or Maude
- retirement or backfill of `population_sex`
- any change to harvest, Analyze, or Fly
