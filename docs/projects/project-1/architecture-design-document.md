# Research Literature Intelligence System

## Architecture & Classification Design Document

**Prepared for:** [Client / Lab Name]  
**Domain:** [Research Domain]  
**Primary Source:** PubMed / MEDLINE via NCBI Entrez API  
**Prepared by:** [Consultant / Firm Name]  
**Date:** [Date]  
**Version:** 1.0 — Client Review Draft

---

## Reference implementation

The **Cannabis Paper Scraper** (`cannabis-paper-scraper` on Fly.io) is the validated reference for this blueprint. Project 1 engagements **clone the architecture** (node decision tree, Maude + LLM pipeline, RL calibration cycle, agent automation) — not cannabis-specific cues, schema columns, or extraction literals. See [`docs/agent_automation_plan.md`](../../agent_automation_plan.md) for the production mapping.

---

## 1. Purpose and Scope

This document defines the architecture, data model, and classification logic for a literature intelligence system that continuously harvests, classifies, and organizes published research within a defined scientific domain. It is intended as a client-facing design specification: a document that can be reviewed, approved, and used as the reference for development, validation, and future enhancement.

The system is organized as a **node decision tree**:

1. **Node 0 (ingestion gate)** — lexographic and semantic positive/negative cues in `rules_config.json` decide whether a harvested record is worth classifying further.
2. **Node 1 (publication-type routing)** — secondary literature (Node 1B) is classified **before** original research (Node 1A).
3. **Node 2 (path-specific extraction)** — clinical (2A), in vivo (2B), and in vitro (2C) branches each carry their own field scopes.
4. **Confidence and review** — thresholds from `rules_config.json` route uncertain records to an expert queue.
5. **RL calibration cycle** — batch → feedback → patch → deploy → re-measure the **same holdout** until alignment targets are met.

This structure supports a one-month MVP while leaving room for deeper subtyping (Node 3+) in later phases.

PubMed / MEDLINE via the NCBI Entrez API is the default system of record for the initial release. Secondary sources such as Semantic Scholar, ClinicalTrials.gov, or Embase can be added later as optional connectors once the base workflow is stable.

---

## 2. Discovery Questions

The following questions should be answered before architecture is finalized. Clear answers reduce downstream ambiguity, prevent scope creep, and make the final release easier to support. Each block maps to a concrete deliverable file agents and engineering use during implementation.

### 2.1 Domain definition

1. What is the precise scientific scope of this database?
2. What is explicitly out of scope?
3. Are there known keyword collisions or false-positive terms?
4. Is there an existing taxonomy or ontology the system should align with?

**Deliverable:** Completed §10.3 relevance worksheet → `rules_config.json` → `cues.relevance` (positive and negative cues).

### 2.2 Evidence boundaries

5. What are the top-level publication-type bins required for this domain?
6. Are systematic reviews and meta-analyses treated as a separate bin or grouped with synthesized evidence?
7. Are there study types that do not fit the main bins?

**Deliverable:** Expert decision tree (PDF or markdown) → `rules_config.json` node prompt sections and `decision_boundaries`.

### 2.3 Required extraction fields

8. What fields must be extracted from every included paper?
9. Which fields differ by branch (Node 2A / 2B / 2C)?
10. Which fields are required at launch versus deferred to phase 2?

**Deliverable:** Completed §10.1 worksheet → `subnode_field_scopes.py` + `schema.sql` migration.

### 2.4 Review capacity and calibration

11. Who is the domain expert for review and correction?
12. How many records can the expert review per week?
13. Is there a reference dataset available for validation?

**Deliverable:** Review queue SLA and expert roster recorded in §10.4 sign-off; holdout batch paper IDs for RL calibration.

### 2.5 Scale and delivery

14. What is the estimated corpus size at launch?
15. What is the expected monthly publication volume?
16. Is historical backfill required, or is forward-looking ingestion the priority?

**Deliverable:** Harvest query terms, backfill window, and `MAX_CALLS` calibration budget per sub-node.

---

## 3. System Architecture Overview

The system follows a **node decision tree** from ingestion through branch-specific extraction, confidence scoring, and expert feedback. Each node has a distinct responsibility and failure mode, which makes the system auditable and easier to hand off to a client team.

### 3.1 Pipeline nodes

| Node | Name | Responsibility |
| :-- | :-- | :-- |
| 0 | Ingestion gate | Applies relevance cues from `rules_config.cues.relevance`; outputs `relevant`, `tangential`, or `irrelevant`. |
| 1B | Secondary literature router | Reviews, meta-analyses, editorials — **route before Node 1A**. |
| 1A | Original research router | Primary data papers; routes to Node 2A / 2B / 2C. |
| 2A / 2B / 2C | Path-specific extractor | Clinical, in vivo, or in vitro field extraction via Maude + LLM; PDF tier when abstract is insufficient. |
| — | Confidence scorer | Auto-accept ≥ `auto_accept` (default 0.85); review queue ≤ `review_recommended` (default 0.6). |
| — | Expert feedback loop | Captures corrections, locks fields, feeds RL calibration and upward propagation. |

### 3.2 Design principles

- **Maude (deterministic) + LLM (ambiguous cases):** Maude handles obvious routing and extraction cheaply; the LLM handles ambiguous language and richer extraction. PDF full-text tier is used when abstract-only extraction is unreliable.
- **No silent low-confidence acceptance:** anything below threshold goes to review rather than being treated as certain.
- **Expert correction is permanent:** manually corrected fields are locked unless explicitly revised by the expert.
- **Every classification is traceable:** each record stores `classification_source` (`heuristic`, `maude`, `llm`, `llm_fewshot`, `expert`), `classifier_version` / build ID, confidence level, and timestamp.
- **RL calibration is holdout-driven:** patches are validated by re-measuring the **same** holdout batch after deploy — not by drawing a new sample each cycle.

### 3.3 Decision tree (client-configurable)

The default tree shape mirrors the validated cannabis implementation. Client-specific labels and cues are filled during discovery; the structure remains stable across engagements.

```
INGESTION (Node 0)
├── ORIGINAL_RESEARCH
│   ├── IN_VITRO → Node 2C
│   ├── IN_VIVO → Node 2B
│   └── CLINICAL → Node 2A
├── REVIEW / META_ANALYSIS / EDITORIAL / COMMENTARY / …
└── UNKNOWN
```

**Critical routing rule:** classify **Node 1B (Reviews / Secondary Literature) before Node 1A (Original Papers)**.

Optional deeper sub-branches (Node 3+) — enable only when the domain requires them:

- Node 3A: Systematic review  
- Node 3B: Meta-analysis  
- Node 3C: Narrative review / editorial / comment / letter  

For MVP, Node 0 → 1B/1A → 2A/2B/2C is the main organizing layer.

### 3.4 Agent automation map

Cursor agents and scripts execute the calibration and operations workflow. Discovery worksheets feed config files; agents run the RL cycle on Fly production.

```mermaid
flowchart LR
  discovery[DiscoveryWorksheets] --> rules[rules_config.json]
  rules --> ingest[harvest.py]
  ingest --> classify[classifier.py + maude_classifier.py]
  classify --> review[ReviewQueue API]
  review --> rl[calibration-automation agent]
  rl --> patch[extractor + maude_cues]
  patch --> deploy[fly deploy]
  deploy --> remeasure[calibration_agent refresh holdout]
  remeasure --> log[handoff_learning_log.json]
```

| Automation artifact | Purpose |
| :-- | :-- |
| [`.cursor/agents/calibration-automation.md`](../../../.cursor/agents/calibration-automation.md) | Full RL cycle on node2a/b/c holdouts |
| [`.cursor/agents/maude-nightly-reclassify.md`](../../../.cursor/agents/maude-nightly-reclassify.md) | Bulk Maude refresh without LLM cost |
| [`scripts/run_subnode_calibration.sh`](../../../scripts/run_subnode_calibration.sh) | Fly PDF Maude A/B batches |
| [`calibration_feedback_agent.py`](../../../calibration_feedback_agent.py) | Disagreement → staged patches |
| [`subnode_field_scopes.py`](../../../subnode_field_scopes.py) | Per-branch extraction field lists |
| [`handoff_learning_log.json`](../../../scratch/calibration_runs/handoff_learning_log.json) | Cross-cycle learnings (template for new projects) |
| [`.cursor/agents/filter-agent.md`](../../../.cursor/agents/filter-agent.md) | Global vs tab filter tier policy (§5.4) |

Operator prompts and copy-paste commands: [`agent-runbook.md`](agent-runbook.md).

### 3.5 Repository / deployment profile

Per-client checklist (fill in during Phase 1 scaffold):

| Item | Client value | Notes |
| :-- | :-- | :-- |
| Fly app name | `[client-scraper]` | e.g. `cannabis-paper-scraper` |
| Production DB | `DATABASE_URL` (Postgres) or volume SQLite | All writes target Fly production only |
| Calibration artifacts | `/data/calibration_runs/` | Persistent volume path on Fly |
| Preflight | `python3 fly_db_check.py` | Run before any production write |
| Dashboard | `https://[app].fly.dev/calibration/dashboard` | Built from pulled batch JSON |
| Build ID | `calibration_build.py` → `MAUDE_CLASSIFIER_BUILD_ID` | Bump on every deploy after patch |

---

## 4. Intake and Publication-Type Routing

### 4.1 Node 0: Ingestion gate

Node 0 is the **relevance gate**. It uses client-defined positive and negative cues (not limited to three keywords) configured in `rules_config.cues.relevance`. Cues may be written as w1, w2, w3 for harvest queries, but the gate itself supports lexographic and semantic negative cues to suppress false positives (e.g. acronym collisions).

Any harvested paper is retained for auditability. Out-of-scope records are flagged (`ingestion_status`: `irrelevant` or `tangential`) and excluded from default search results — not deleted on first pass.

The gate is deliberately broad at launch. Its purpose is to capture enough relevant material to support calibration within the MVP window.

### 4.2 Node 1: Publication-type routing

All in-scope papers pass through Node 1B before Node 1A:

- **Node 1B — Secondary / synthesized evidence:** reviews, systematic reviews, meta-analyses, editorials, comments, letters, perspectives.
- **Node 1A — Original research:** primary data papers routed to Node 2A (clinical), 2B (in vivo), or 2C (in vitro).
- **Other / mixed / unclear:** papers that do not cleanly fit a single branch (Node 2D when enabled).

Secondary literature receives **restricted extraction** — dose, sample size, and model/system fields remain null rather than being inferred from review text.

### 4.3 Suggested sub-branches (Node 2 / Node 3)

When the domain requires finer routing after MVP:

- Node 2A: Clinical — interventional (RCT, non-randomized) or observational (prospective, retrospective, cross-sectional, case-control).
- Node 2B: In vivo — rodent, non-rodent mammal, non-mammal.
- Node 2C: In vitro — immortalized cell, primary cell, co-culture, organoid.
- Node 3A–3C: Review subtypes for secondary literature.

Enable only the sub-branches that change extraction field scopes or client reporting.

---

## 5. Data Model

The schema is split into three tiers matching the reference implementation. Domain-specific extraction columns are confirmed with the client during discovery (§10.1) and implemented in `schema.sql` + `subnode_field_scopes.py`.

### 5.1 Core bibliographic record

| Field | Type | Notes |
| :-- | :-- | :-- |
| pmid | TEXT unique | PubMed identifier. |
| doi | TEXT unique | DOI when available. |
| title | TEXT | Required. |
| authors | JSON array | Structured list. |
| journal | TEXT | Journal name. |
| year | INTEGER | Year of publication. |
| abstract | TEXT | Abstract text. |
| full_text_link | TEXT | PMC or DOI link if available. |
| date_harvested | TEXT | Harvest timestamp. |
| publication_date | TEXT | Publication timestamp when available. |

### 5.2 Routing and classification metadata

| Field | Type | Notes |
| :-- | :-- | :-- |
| ingestion_status | TEXT | Node 0: `relevant`, `tangential`, `irrelevant`. |
| publication_type | TEXT | Node 1: review, original research, meta-analysis, editorial, etc. |
| study_type | TEXT | Node 2 branch label (clinical, in vivo, in vitro, …). |
| classification_source | TEXT | `heuristic`, `maude`, `llm`, `llm_fewshot`, `expert`. |
| classifier_version | TEXT | Prompt variant or calibration build label. |
| classification_confidence | REAL | 0.0 to 1.0; null once expert-reviewed. |
| classification_timestamp | TEXT | When classification last ran. |
| expert_locked_fields | JSON array | Fields manually corrected by expert. |

### 5.3 Branch extraction fields

Field lists are defined per Node 2 branch in `subnode_field_scopes.py`. Tables below use **illustrative cannabis-reference examples**; replace with client fields from §10.1.

#### Node 2A — Clinical (example fields)

| Field | Type | Notes |
| :-- | :-- | :-- |
| sample_size | INTEGER | Null if not reported. |
| exposure_method | TEXT | Route of administration. |
| intervention_type | TEXT | Client-defined intervention label. |
| dose_mg | REAL | Standardized dose when reported. |
| duration_days | REAL | Treatment or follow-up duration. |
| outcome_domain | JSON array | Controlled vocabulary (§10.2). |
| administration_frequency | TEXT | e.g. once daily, BID. |

#### Node 2B — In vivo (example fields)

| Field | Type | Notes |
| :-- | :-- | :-- |
| species | TEXT | Host species label. |
| sample_size | INTEGER | Animal count when reported. |
| exposure_method | TEXT | Oral, injection, inhalation, etc. |
| dose_mg_kg | REAL | mg/kg when reported. |
| duration_days | REAL | Exposure duration in days. |
| repeat_exposure_count | INTEGER | Repeat exposures when reported. |
| outcome_domain | JSON array | Controlled vocabulary (§10.2). |

#### Node 2C — In vitro (example fields)

| Field | Type | Notes |
| :-- | :-- | :-- |
| exposure_method | TEXT | Media, vapor-conditioned media, dissolved compound, etc. |
| treatment_duration | TEXT | e.g. 24 hours. |
| concentration_uM | REAL | µM when reported. |
| outcome_domain | JSON array | Controlled vocabulary (§10.2). |
| repeat_exposure_count | INTEGER | Repeat exposures when reported. |

#### Node 1B — Secondary literature (restricted extraction)

| Field | Type | Notes |
| :-- | :-- | :-- |
| review_type | TEXT | Systematic review, meta-analysis, narrative review, editorial, etc. |
| topic_scope | TEXT | Main topics covered. |
| included_studies_count | INTEGER | If reported. |
| outcome_domain | JSON array | Controlled vocabulary when applicable. |
| synthesis_notes | TEXT | Brief summary of findings. |

### 5.4 UI filter tiers (database section)

The dashboard **Database** view splits filters into a global horizontal bar and tab-specific sidebar controls. Policy is enforced by [`dashboard_ui_config.py`](../../../dashboard_ui_config.py) and audited with [`filter_agent.py`](../../../filter_agent.py) (see [`.cursor/agents/filter-agent.md`](../../../.cursor/agents/filter-agent.md)).

#### Global bar (all tabs)

Only **§5.1 core bibliographic** search fields appear in the top horizontal row, plus derived availability flags:

| UI label | API param | Schema fields |
| :-- | :-- | :-- |
| Search Articles | `query` | title, abstract, authors, journal, pmid, doi (FTS) |
| Recently Harvested | `recent_range` | date_harvested |
| PDF | `has_pdf` | full-text link ends with `.pdf` |
| Full Text | `has_full_text` | full_text_link present (PMC/DOI) |

Do **not** place §5.2 routing or §5.3 extraction controls in the global bar.

#### Sidebar (tab-specific)

Each dashboard tab (`all_original`, `preclinical`, `clinical`, `review`, `unclassified`) loads a **filter profile** from `FILTER_PROFILES`:

| Tier | Sidebar sections | Examples |
| :-- | :-- | :-- |
| §5.2 routing | Classification Details, Publication Type, Study Design | `classification_level`, `publication_type`, `study_type` |
| §5.3 extraction | Exposure, species, dose/duration sliders, outcomes | `exposure_method`, `sample_size`, `dose_mg`, `outcome_domain` |
| §5.1 (sidebar) | Publication year range | `year_min`, `year_max` — shown on all tabs but not in global bar |

**Review tab** adds Publication Type checkboxes (§5.2). **Clinical / Pre-Clinical** tabs expose branch fields from §5.3 matching Node 2A / 2B / 2C.

When customizing for a new client, update `FILTER_PROFILES` and taxonomy options — not the tier rules above.

---

## 6. Classification Decision Flow

This section documents the routing logic that determines whether a record is in scope and which extraction path it follows. It is the most important section for client review because it directly encodes judgment into the system.

### 6.1 Tier 1 — Node 0 (ingestion gate)

**Question:** Is this paper plausibly in scope given configured relevance cues?

**Outputs:** `relevant`, `tangential`, `irrelevant`.

**Action on fail:** retain the record for auditability; flag as out of scope and exclude from default search results. Do not delete on first pass.

### 6.2 Tier 2 — Node 1B before Node 1A

**Question:** Is this secondary/synthesized literature or original research?

**Critical rule:** run Node 1B classification **before** Node 1A. Reviews and meta-analyses must not be misrouted into original-research extractors.

Secondary paths receive restricted extraction (§5.3 Node 1B). Original research proceeds to Node 2A / 2B / 2C.

### 6.3 Tier 3 — Node 2 path-specific extraction

Once routed to an original-research branch, papers follow the relevant extractor (`maude_classifier.py` + `extractor.py` + LLM). Each path uses its own field list from `subnode_field_scopes.py`. When abstract text is insufficient, use the **PDF extracted tier** (`content-tier: pdf_extracted`) for calibration and production extraction.

### 6.4 Tier 4 — Confidence and review queue

Default thresholds from `rules_config.confidence_thresholds`:

- **Auto-accept:** confidence ≥ 0.85 (`auto_accept`).
- **Review queue:** confidence ≤ 0.6 (`review_recommended`).

Low-confidence papers surface at `/api/classification/queue`, ordered by uncertainty. Tune thresholds during RL calibration with the client expert.

### 6.5 Tier 5 — Expert lock and feedback audit

Expert corrections via `/api/papers/<paper_id>/edit-classification` are logged in `feedback_audit`. Corrected fields are added to `expert_locked_fields` and must not be silently overwritten by automated reclassification.

---

## 7. Expert Feedback and Continuous Improvement

The system improves over time using expert corrections without requiring the expert to review every record indefinitely.

### 7.1 Correction workflow

1. The expert reviews papers from the queue.
2. The expert edits incorrect fields directly.
3. Each correction is logged with the original value, the new value, and an optional reason.
4. Corrected fields are locked against future automated overwrite.
5. After enough corrections accumulate, the **RL calibration cycle** (batch → feedback → patch → deploy → re-measure same holdout) proposes refinements to `extractor.py`, `maude_classifier.py`, and `maude_cues.json`.
6. Any proposed patch must be validated against the **same holdout batch** before it is accepted; zero-regression gate applies (`reward_function.zero_regression_gate`).

### 7.2 What gets logged

- Every expert correction (`feedback_audit`).
- Every automated change to routing or extraction logic (`optimization_log`).
- Before/after alignment and recall on holdout batches.
- Which records were re-evaluated and which build ID was active.

### 7.3 Upward propagation

At classification time, similar prior expert corrections are retrieved via BM25 over `feedback_audit_fts` and injected as dynamic few-shot context — distinct from static cues in `rules_config.json`. See [`docs/agent_automation_plan.md`](../../agent_automation_plan.md) § Upward Propagation.

This audit trail allows a new team member or client stakeholder to understand why the system behaves as it does without inspecting implementation details.

---

## 8. Calibration and Validation Plan

Before the system is used with minimal supervision, it goes through bounded **RL calibration** with close expert involvement. Generic Hamming/F1 metrics supplement the primary holdout metrics but do not replace them.

### 8.1 Holdout validation set

- **Target size:** 10–20 papers per sub-node batch for RL cycles; 50–100 papers for final sign-off validation.
- **Composition:** fixed holdout paper IDs per sub-node (`node2a`, `node2b`, `node2c`); the **same batch JSON is re-measured** after every deploy.
- **Artifacts:** `node2{a,b,c}_calibration_*.json`, `*_feedback_report.json`, `*_walkthrough.md` under `/data/calibration_runs/`.

### 8.2 Primary metrics (RL holdout protocol)

| Metric | Source | Use |
| :-- | :-- | :-- |
| Alignment % | `calibration_metrics.py` / `compute_scoped_metrics` | Field-level agreement on scoped fields (primary gate). |
| Recall % | `score_paper_rl_metrics` | Maude recall on holdout (secondary). |
| Top disagreeing fields | Batch JSON + feedback report | Drives staged patches. |
| Hamming loss | `optimization_log` | Overall field-level disagreement (secondary). |
| F1 score | Eval manifest | Multi-label / categorical fields (secondary). |
| Mean absolute error | Numeric field comparison | Dose, duration, sample size (secondary). |

**Gate:** `agreement_threshold_pct` in `rules_config.json` (default **90%** holdout alignment) before sign-off.

### 8.3 RL cycle definition (complete only when code ships)

1. **Batch** — PDF Maude A/B on Fly: `SUBNODE=node2{b,c} MAX_CALLS={N} ./scripts/run_subnode_calibration.sh`
2. **Feedback** — `run_feedback_cycle` on the batch JSON → `staged_patches/`
3. **Implement** — `extractor.py`, `maude_classifier.py`, `maude_cues.json` (+ tests)
4. **Bump** — `calibration_build.py` → new `MAUDE_CLASSIFIER_BUILD_ID`
5. **Deploy** — `fly deploy --remote-only -a [client-app]`
6. **Re-measure** — same holdout: `python3 calibration_agent.py --refresh-maude-from-batch [batch].json`
7. **Log** — append `handoff_learning_log.json`

### 8.4 Sign-off criteria

Recommended sign-off conditions:

- Holdout **alignment ≥ 90%** on node2a, node2b, and node2c (or client-adjusted threshold).
- Zero unresolved high-severity **routing** misclassifications (Node 0 / Node 1 order).
- Expert approval of holdout walkthrough and a sample of review-queue decisions.

---

## 9. Phased Delivery Plan

Phases follow agent execution order. Each phase has a client-facing deliverable and an automation owner.

| Phase | Client deliverable | Agent / script owner |
| :-- | :-- | :-- |
| 0 — Discovery | Signed worksheets (§10) + decision tree | Human + doc §2/§10 |
| 1 — Scaffold | New repo/fork, schema, Fly app, empty `rules_config` | Engineering |
| 2 — Node 0 + harvest | Ingestion cues, PubMed pull, relevance gate | `harvest.py`, `extractor.py` |
| 3 — Node 1 routing | 1B-before-1A classifier cues | `classifier.py`, `calibration_agent --mode node1_routing` |
| 4 — Node 2 extraction | Per-branch field scopes + Maude cues | `maude_classifier.py`, `subnode_field_scopes.py` |
| 5 — RL calibration | Alternating node2a/b/c cycles to ≥90% holdout | `calibration-automation` agent |
| 6 — Review UI + export | Queue, expert edit API, dashboard | `app.py`, `calibration_metrics.py --build-dashboard` |
| 7 — Ops | Nightly Maude refresh, handoff log | `maude-nightly-reclassify` agent |

---

## 10. Appendix: Domain Customization Worksheet

Complete these tables during discovery. Agents copy outputs into the config files listed in §2.

### 10.1 Field definition worksheet

| Variable Name | Branch (Node) | Data Type | Allowed Values / Notes |
| :-- | :-- | :-- | :-- |
| *example only:* `dose_mg_kg` | 2B in vivo | REAL | mg/kg THC or client compound |
| *example only:* `treatment_duration` | 2C in vitro | TEXT | e.g. 24 h, 48 h |
| | | | |

### 10.2 Outcome taxonomy

| Outcome Domain | Definition / Inclusion Criteria |
| :-- | :-- |
| *example only:* pain | Human or animal pain endpoints, analgesia scales |
| *example only:* inflammation | Cytokine, histology, or inflammatory marker outcomes |
| | |

### 10.3 Relevance cue worksheet

| Term / Acronym | In-Domain Meaning | Common False-Positive Meaning |
| :-- | :-- | :-- |
| *example only:* CBD | Cannabidiol | Common bile duct, corticobasal degeneration |
| *example only:* hemp | Cannabis cultivar / fiber crop with cannabinoid data | Textile or agricultural yield only |
| | | |

### 10.4 Sign-off

| Role | Name | Date |
| :-- | :-- | :-- |
| Domain Expert |  |  |
| Client Sponsor |  |  |
| Engineering Lead |  |  |

---

## Engagement flow (summary)

1. **Discovery (week 1):** Complete §2 + §10 with domain expert; identify acronym collisions and out-of-scope terms.
2. **Architecture sign-off (week 1–2):** Client approves decision tree shape and per-branch field list — no code until signed.
3. **Scaffold (week 2):** Fork the reference repo; new Fly app; replace domain config and schema columns; keep calibration scripts and agents.
4. **Calibration (weeks 3–4):** Full RL cycles on node2a/b/c holdouts per [`.cursor/rules/rl-calibration.mdc`](../../../.cursor/rules/rl-calibration.mdc).
5. **Handoff:** Signed architecture doc + populated config files + calibration dashboard + `handoff_learning_log.json` export.
