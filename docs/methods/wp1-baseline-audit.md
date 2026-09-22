# WP1 Baseline Audit — Cannabis Research Navigator Methods Schema

**Role:** CRN Methods (Scientific Data Architect)  
**Date:** 2026-09-22 (ET)  
**Scope:** Read-only inventory of local Cannabis Paper Scraper (`/workspace/cannabis-paper-scraper`) against Faculty of Medicine AI Seed Year-1 needs (`ai-seed-fom/*`).  
**Status:** Draft for tip/dev PR packaging. Scientific value meanings marked `pending_biomedical` — Shawn adjudicates; this audit does not invent definitions.

---

## 1. Sources audited (local, read-only)

| Source | Path |
|--------|------|
| Proposal / pillars | `ai-seed-fom/01-updated-proposal-*.md`, `02-researcher-brief-*.md`, `03-director-implementation-plan-*.md`, `04-two-pillars-ai-language-*.md` |
| Paper columns | `schema.sql` |
| Decision-tree / LLM field contract | `rules_config.json`, `classification_schema.py` |
| Branch field scopes | `subnode_field_scopes.py` |
| Source-tier gating | `content_tiers.py` (`METHODS_HEAVY_FIELDS`) |
| Maude tree | `maude_classifier.py`, `maude_cues.py` |
| Migrations | `migrations/versions/` (heuristics/tasks only; no methods extraction table yet) |

**Not touched:** Paper Scraper Engineer lanes, Fly, git write, production DB.

---

## 2. Seed Year-1 method needs (from Seed docs — not invented)

Pillar 1 asks for validated **study/context properties** comparable across papers:

1. Research type (in vitro / in vivo / clinical framing)  
2. Product  
3. Route of administration  
4. Disease or experimental model  
5. Key outcomes / related method fields  
6. SGBA+ / IDEAS fields **only when papers report them**  
7. Provenance: model/prompt identity, confidence, human override; AI-assisted vs human-reviewed honesty  

Compare UI must work on **hand tags / existing Analyze fields first**, then fill as AI lands.

---

## 3. Inventory disposition

Legend: **KEEP** = retain as-is for Year-1 contract · **REFINE** = keep concept, reshape naming/envelope · **DROP** = do not carry into Year-1 methods contract · **DEFER** = wait on Shawn biomedical adjudication (see `biomedical-adjudication-questions.md`).

### 3.1 Classification / routing

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `ingestion_status` (Node 0) | **KEEP** (catalog) | Relevance gate; not a Compare method card field. Out of methods schema core; remains Scraper ingest. |
| `publication_type` | **KEEP** (catalog) | original research / review / case study. Orthogonal to research-type ontology (Q2). |
| `study_type` (multi-label strings) | **REFINE** | Today mixes design labels (RCT, animal, in vitro) with review subtypes. Seed needs a clearer **research_type** axis — scientific enum **`pending_biomedical`** (Q2, Q3). |
| Maude decision tree (Nodes 0–3, 2a/2b/2c/2d, Node 7 paths) | **DEFER** (Q15) | Live routing/calibration asset. Year-1 methods contract must not silently replace it; status is a Shawn question. |
| High-level compare fields (`ingestion_status`, `publication_type`, `study_type`, `exposure_method`, `cannabis_type`, `outcome_domain`, `species`) | **REFINE** | Align names to Seed vocabulary (product, route, research_type) after adjudication. |

### 3.2 Product / route / exposure

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `cannabis_type` (product-form list in prompts) | **REFINE** → proposed `product` | Existing cue list is engineering vocabulary, not adjudicated ontology. Values **`pending_biomedical`** (Q4). |
| `exposure_method` | **REFINE** → proposed `route` (and/or exposure context) | Today conflates clinical routes, in vivo exposure setups, and in vitro media exposure. Separation of **route vs product** is Q5. |
| `strain_reported` / `strain_normalized` | **REFINE** | Overloaded: botanical strain, synthetic ligand, sometimes supplier cues; animal strains explicitly forbidden in prompts but still a confusion risk. Chemotype I/II/III mapping is scientific — **`pending_biomedical`**. |
| Node 7 exposure path splits (7a–7g in vivo; 7a–7c in vitro) | **DEFER** | Useful extraction scoping; not Year-1 public Compare filters until Shawn confirms. |

### 3.3 Dose / concentration / regimen

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `dose_mg`, `thc_mg_kg`, `cbd_mg_kg`, `thc_mg_g`, `cbd_mg_g` | **KEEP** columns conceptually | Store **as reported**; unit family must stay distinct. |
| `thc_mg_ml`, `cbd_mg_ml`, `thc_uM`, `cbd_uM`, `thc_pct`, `cbd_pct` | **KEEP** conceptually | Concentration vs dose vs % — do not cross-convert without policy (Q8). |
| `puff_count` | **KEEP** (inhaled contexts) | Methods-heavy; abstract-tier gated today. |
| Prompt-side µg/mL → mg/mL conversion guidance | **DROP** from Year-1 methods contract | Technical proposal: forbid silent unit inventing; mark conversions as adjudication (Q8). Existing prompt text is Eng lane debt — Methods documents the **forbidden conversions** rule only. |
| `duration_days`, `inhaled_exposure_duration`, `administration_frequency`, `treatment_duration`, `repeat_exposure_count`, `exposure_regimen_bin` | **REFINE** → regimen group | Keep as reported-structure fields; regimen bin labels acute/subchronic/chronic are scientific — **`pending_biomedical`**. |
| `multiple_doses`, `multiple_time_intervals` | **KEEP** as design flags | Boolean design cues; not outcomes. |

### 3.4 Outcomes / models / species / population

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `outcome_domain` (pain, anxiety, …) | **REFINE** | Cue list exists; Year-1 domain set and “other” policy **`pending_biomedical`** (Q10). |
| Disease / experimental model | **DEFER** as first-class field | **Absent** as dedicated column today; Seed explicitly needs it (Q6). Placeholder hook only until Shawn. |
| `species` | **REFINE** | Host species from Maude tree; align with biological-system ontology (Q11). |
| `population_age`, `population_sex` | **KEEP** as SGBA+ hooks | Expand representation envelope; **never invent** (Q12). |
| `inclusion_criteria`, `exclusion_criteria` | **DEFER** for Compare cards | Free-text; optional recall / gold detail, not Year-1 filter spine. |
| `sample_size` | **REFINE** | Needs replicate vs sample vs arm rules (Q9). |

### 3.5 Provenance / confidence / source tier

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `classification_confidence`, `classifier_version`, `classification_timestamp` | **REFINE** → provenance envelope | Expand to Seed-required: schema_version, extractor_id, model_id, prompt_id, confidence, source_tier, source_span, human_override, reviewed_at. |
| `expert_locked_fields`, `feedback_audit` | **KEEP** (HITL substrate) | Supports gold/override; gold policy itself is Q16. |
| `llm_calls_log` | **KEEP** (ops) | Cost/token telemetry; not scientific ontology. |
| `content_tiers.py` tiers + `METHODS_HEAVY_FIELDS` | **KEEP / REFINE** | Maps to `source_tier` honesty (abstract vs PDF). Align with Q13. |
| Distinct missingness | **NEW (technical)** | Today null/empty conflates “not reported”, “not applicable”, “failed”. Propose enum: `reported \| not_in_available_source \| source_unavailable \| not_applicable \| uncertain \| extraction_failed`. |

### 3.6 IDEAS / SGBA+

| Existing | Disposition | Notes |
|----------|-------------|-------|
| `population_sex`, `population_age` | **KEEP** + hook expansion | Seed: capture only when reported. |
| Gender (distinct from sex), population descriptors beyond age/sex | **DEFER** hooks | Representation fields in schema contract as optional reported-only; scientific coding **`pending_biomedical`** (Q12). |
| Fabricating SGBA+ from names/pronouns/assumptions | **DROP** (forbidden) | Annotation guide: never-infer. |

### 3.7 Unit of scientific record

| Existing | Disposition | Notes |
|----------|-------------|-------|
| One row ≈ one paper (`papers.id`) | **KEEP** as Year-1 default | Seed Q7 asks paper vs family vs experiment vs arm — **`pending_biomedical`**. Technical proposal: Year-1 extraction attaches to paper_id; multi-arm is deferred structure. |

---

## 4. Executive keep / refine / drop / defer (summary)

1. **KEEP** catalog bibliographic spine and ingest relevance (`ingestion_status`, IDs, FTS).  
2. **KEEP** numeric dose/concentration **columns as reported** (no silent cross-unit science).  
3. **KEEP** content-tier gating of methods-heavy fields (abstract honesty).  
4. **KEEP** HITL substrates (`expert_locked_fields`, `feedback_audit`) for gold workflow.  
5. **REFINE** `study_type` → separate publication_type vs research_type axes after Shawn Q2/Q3.  
6. **REFINE** `cannabis_type` / `exposure_method` → product vs route (Q4/Q5).  
7. **REFINE** confidence/version stamps → full provenance envelope.  
8. **REFINE** nulls → distinct missingness enum (technical).  
9. **DROP** from Year-1 methods contract: prompt-authorized inventing of unit conversions; inventing SGBA+; chatbot/MCP/RLHF as FoM deliverables (already Seed out-of-scope).  
10. **DEFER** disease/experimental model ontology, multi-arm unit of record, legacy Maude tree replacement decision (Q6, Q7, Q15), gold policy details (Q16).  
11. **DEFER** Node-7 path labels as public filters until product/route adjudicated.  
12. **NEW** IDEAS/SGBA representation hooks with never-infer rule; scientific codes pending.

---

## 5. Separation reminder

| Lane | Owns |
|------|------|
| **Technical (this WP1 package)** | JSON Schema contract shape, provenance envelope, missingness enum, source_tier mapping, file layout, tip/dev PR text |
| **Shawn biomedical adjudication** | All scientific enums, mandatory vs deferred Year-1 fields, comparability language bar, Maude status, gold policy |
| **Paper Scraper Engineer** | Migrations, extract jobs, Fly, request-path workers — **not started by this package** |

