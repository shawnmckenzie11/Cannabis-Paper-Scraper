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
