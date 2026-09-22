# Schema Proposal (Narrative) — Methods WP1

**Role:** CRN Methods (Scientific Data Architect)  
**Date:** 2026-09-22 (ET)  
**Audience:** tip/dev PR reviewers; Shawn for biomedical adjudication; Eng later for Alembic (not this package)

---

## 1. What this proposal is

A **Year-1 methods extraction contract** that:

- Extends the live Cannabis Paper Scraper catalog (PostgreSQL source of truth per Seed director plan).  
- Separates **technical storage/honesty machinery** from **Shawn biomedical meaning**.  
- Ships as docs + JSON Schema under `/workspace/crn-methods-wp1/` for tip/dev — **no Fly, no Eng pipeline PR, no git write from this lane**.

Machine contract: `schemas/methods.schema.json`.  
Field inventory: `docs/methods/field-dictionary.md`.  
Shawn question list: `docs/methods/biomedical-adjudication-questions.md`.

---

## 2. Technical choices (Methods / Eng can implement without inventing science)

### 2.1 Attachment unit (provisional)

- Extractions methods records attach to **`paper_id`** (existing `papers` row).  
- Multi-experiment / multi-arm structure is **not** implemented until Q7 is answered.  
- Technical default does **not** claim scientific “one paper = one comparable unit.”

### 2.2 Provenance envelope (required on every extraction)

| Key | Purpose |
|-----|---------|
| `schema_version` | Contract version string |
| `extractor_id` | Pipeline/component id |
| `model_id` | Model identity if LLM/encoder used |
| `prompt_id` | Prompt/rules version id |
| `confidence` | 0–1 score when available |
| `source_tier` | Aligns with content tiers (abstract vs full text, etc.) |
| `source_span` | Optional quote/locator into available text |
| `human_override` | bool or structured override payload |
| `reviewed_at` | ISO timestamp when human reviewed |

This refines today’s `classification_confidence` / `classifier_version` / `classification_timestamp` without deleting them until Eng migrates.

### 2.3 Distinct missingness (required)

Null alone is insufficient. Proposed enum:

`reported` | `not_in_available_source` | `source_unavailable` | `not_applicable` | `uncertain` | `extraction_failed`

Supports Seed honesty (“empty is OK”) and SGBA+ “absent vs not reported” checks.

### 2.4 Source-tier gating

Preserve the spirit of `content_tiers.py` + `METHODS_HEAVY_FIELDS`:

- Methods-heavy numerics should not be treated as “missing science” when only an abstract was available — prefer `source_unavailable` or `not_in_available_source`.  
- Exact tier labels map technically to `source_tier`; whether abstract may assert a value is Q13.

### 2.5 IDEAS / SGBA+ hooks

Schema includes reported-only SGBA+/IDEAS fields and a `sgba_reporting_present` flag.  
**Technical forbid:** inference that fabricates sex/gender/population.  
**Scientific coding:** pending Q12.

### 2.6 Dual-write / hand-tag compatibility

Public Compare must work on **existing hand tags / Analyze fields** before AI backfill (Seed objective).  
Technical proposal: methods schema is additive; legacy columns remain readable; rename mapping lives in the field dictionary.

### 2.7 Out of this package

- Alembic migrations, extract workers, pgvector, Label Studio deploy  
- Fly volume/deploy  
- Replacing or rewriting Maude decision-tree runtime (status = Q15)  
- Answering biomedical enums

---

## 3. Shawn biomedical adjudication (do not answer here)

All scientific meaning is gated on the 16 questions in `biomedical-adjudication-questions.md`, including:

- Which Core fields are mandatory in Year 1 (Q1)  
- Research-type and clinical subtype ontologies (Q2–Q3)  
- Product taxonomy; route vs product (Q4–Q5)  
- Disease/experimental model (Q6)  
- Unit of scientific record (Q7)  
- Dose/concentration/regimen rules and **forbidden conversions** (Q8)  
- Replicates & samples (Q9)  
- Outcomes domains (Q10)  
- Species / biological system (Q11)  
- SGBA+ scientific rules (Q12)  
- Source-tier truth bar (Q13)  
- Comparability / gap language bar (Q14)  
- Legacy Maude/decision-tree status (Q15)  
- Gold/adjudication policy (Q16)

Until answered, allowed enum arrays in JSON Schema are placeholders marked `pending_biomedical` (open string with description — not a fake closed ontology).

---

## 4. Suggested storage shape (technical sketch only)

Not an Eng migration. Illustrative:

1. Keep `papers` bibliographic + legacy classification columns.  
2. Add `paper_methods_extractions` (or JSONB column) holding schema-conformant documents with provenance + per-field missingness.  
3. Index Compare filters only after Q1 marks fields mandatory.

Cascade extraction (MeSH → encoder → schema-constrained LLM) remains Seed Pillar 1 Eng work; this package only defines the **target document shape**.

---

## 5. Risks if technical and biomedical are conflated

| Risk | Mitigation in WP1 |
|------|-------------------|
| Invented cannabis ontology in schema | Open enums + `pending_biomedical` |
| Fake completeness on abstract-only rows | missingness + source_tier |
| SGBA+ fabrication | never-infer annotation rule |
| Silent mg↔µM “helpfulness” | forbidden conversions flagged for Q8 |
| Maude replaced by accident | Q15 explicit; tree treated as DEFER |

---

## 6. Acceptance for this draft package

- [x] Audit vs Seed needs with keep/refine/drop/defer  
- [x] Field dictionary with PENDING scientific values  
- [x] JSON Schema with provenance + missingness + IDEAS/SGBA hooks  
- [x] Annotation guide draft  
- [x] 16 Shawn questions embedded unanswered  
- [x] PR description for tip/dev when Cloud Agent env is fixed  
- [ ] Shawn adjudication (blocker for closed enums)  
- [ ] Eng implementation (out of lane)

