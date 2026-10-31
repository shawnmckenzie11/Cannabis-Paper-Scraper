# CRN Methods — WP1 Draft Package

**Cannabis Research Navigator · Scientific Data Architect (Methods)**  
**Date:** 2026-09-22 (ET)  
**Location:** `docs/methods/` and `schemas/methods.schema.json` in this repository (Methods index only; not the repo-root README)

---

## Important

**Scientific values await Shawn.** All research-type, product, route, model, outcome, species, regimen, and SGBA+ **vocabularies** are marked `pending_biomedical`. This package proposes **technical** contract shape only. Do not treat open strings as locked ontology.

---

## Index

| File | Path | Purpose |
|------|------|---------|
| Baseline audit | `docs/methods/wp1-baseline-audit.md` | keep / refine / drop / defer vs Seed needs |
| Field dictionary | `docs/methods/field-dictionary.md` | Year-1 proposed fields; scientific values PENDING |
| Schema proposal | `docs/methods/schema-proposal.md` | Narrative: technical vs Shawn questions |
| JSON Schema | `schemas/methods.schema.json` | Contract: provenance + missingness + IDEAS/SGBA hooks |
| Completeness rule | `schemas/completeness_rules.json` | v0.7 reporting-completeness tiers (pending_biomedical) |
| Annotation guide | `docs/methods/annotation-guide-draft.md` | Provenance, missingness, SGBA never-infer |
| Shawn questions | `docs/methods/biomedical-adjudication-questions.md` | Q1–Q16 unanswered |
| This index | `docs/methods/README.md` | Package map |

---

## Read-only inputs (local Scraper)

Audited against the Cannabis Paper Scraper tree, including Seed needs from `ai-seed-fom/*` (proposal, researcher brief, director plan, pillars) and:

- `schema.sql`, `rules_config.json`, `classification_schema.py`  
- `subnode_field_scopes.py`, `content_tiers.py` (`METHODS_HEAVY_FIELDS`)  
- Maude cues/classifier; `migrations/` present but no methods extraction table yet  

The `ai-seed-fom/` inputs are not added by this PR.

---

## Technical proposals (locked for discussion, not science)

- **Missingness:** `reported` | `not_in_available_source` | `source_unavailable` | `not_applicable` | `uncertain` | `extraction_failed`  
- **Provenance envelope:** `schema_version`, `extractor_id`, `model_id`, `prompt_id`, `confidence`, `source_tier`, `source_span`, `human_override`, `reviewed_at`  
- **IDEAS/SGBA:** reported-only hooks; never invent  

## Lanes

| Lane | This package |
|------|----------------|
| Methods / tip-dev docs | Yes |
| Shawn biomedical answers | No — questions only |
| Paper Scraper Engineer / Fly / git write of runtime | No |

---

## Next

1. Shawn adjudicates Q1–Q16.  
2. Revise dictionary + schema enums from decisions.  
3. Eng implements storage/pipeline in a **separate** unlocked PR.

**v0.5 (wp1.5.0-draft):** adds the `pmc_oa_xml` tier, optional `source_license` (full-text storage pinned off pending Shawn), the `validation` split, `extraction_run_manifest` and `run_event`, and requires `record_id` on new records. All additive; earlier records stay valid. See `field-dictionary.md`.

**v0.6 (wp1.6.0-draft, MCK-425, PhD to confirm):** product becomes origin + preparation (+ named substance) normalized fields, `strain_normalized` is name cleaning only, new `chemotype_reported` (only when cannabinoid content is reported), `normalization_status` widens to `pending_biomedical` / `approved` / `unmapped` / `needs_review`, rare raw strings (N=3 placeholder) go to review. Vocabulary versioned in `schemas/normalization_rules.json`. Earlier records stay valid. See `field-dictionary.md`.

**v0.7 (wp1.7.0-draft, pending_biomedical):** adds a per-field `reporting` block (status / location / evidence quote, plus `check_failure_reason` when a section could not be checked), completeness tiers as a versioned rule in `schemas/completeness_rules.json` (explicit grades gold / near_gold / unverified / not_scored / out_of_scope / not_yet_assessed; clinical gold for RCTs only; study-type fraction, good practice separate, no combined score, computed not stored; U1, U2, TOP4 answered), new `supplier`, `vehicle_solvent`, `storage`, `form` (incl. topical) and `cannabinoids` fields, and an `oral_diet` / `oral_gavage` route split. Three PhD questions stay open. The DB migration is a separate later step, gated on CRN Reliability fixing Alembic on prod. Earlier records stay valid. See `field-dictionary.md`.
