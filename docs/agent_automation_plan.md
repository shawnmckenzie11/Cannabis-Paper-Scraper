# Agent Automation Plan for Cannabis Paper Classification

Expert decision-tree feedback has been received. This plan turns that chart into an agent-ready operating model for the Cannabis Paper Scraper and maps each node to structured cues in `rules_config.json`.

## Expert Decision Tree & Cues (Received)

Source: expert feedback file `EXPERT FEEDBACK TREE & CUES` (2026-06-18).

### Decision Tree

```
INGESTION
├── ORIGINAL_RESEARCH
│   ├── IN_VITRO
│   │   ├── IMMORTALIZED_CELL
│   │   ├── PRIMARY_CELL
│   │   ├── CO_CULTURE
│   │   └── ORGANOID
│   │
│   ├── IN_VIVO
│   │   ├── RODENT
│   │   ├── NON_RODENT_MAMMAL
│   │   └── NON_MAMMAL
│   │
│   └── CLINICAL
│       ├── INTERVENTIONAL
│       │   ├── RCT
│       │   └── NON_RANDOMIZED
│       │
│       └── OBSERVATIONAL
│           ├── PROSPECTIVE
│           ├── RETROSPECTIVE
│           ├── CROSS_SECTIONAL
│           └── CASE_CONTROL
│
├── CASE_REPORT
├── REVIEW
├── META_ANALYSIS
├── EDITORIAL
├── COMMENTARY
└── UNKNOWN
```

**Routing order:** classify **Node 1B (Reviews / Secondary Literature) before Node 1A (Original Papers)**. In vivo, in vitro, and clinical sub-nodes below Node 2 are documented; deeper sub-node cue packs follow in a subsequent expert delivery.

### Node Hierarchy & Context Cues

#### Node 0: Ingestion

Purpose: Normalize harvested records and decide whether the paper is worth classifying further by looking at title and abstract.

Outputs: `relevant`, `tangential`, `irrelevant`

| Cue type | Signals |
| --- | --- |
| Positive (lexographic) | cannabis, marijuana, cannabinoid, THC, CBD, endocannabinoid, administration, pharmacology, clinical outcome |
| Negative (lexographic) | hemp fiber, agricultural yield, textile |
| Negative (semantic) | taxonomy-only, legal/policy-only, unrelated acronym collisions |

#### Node 1B: Reviews / Secondary Literature

Purpose: Reviews, systematic reviews, meta-analyses, editorials, comments, letters, perspectives. **Route here before Node 1A.**

| Cue type | Signals |
| --- | --- |
| Positive (lexographic) | review, systematic review, meta-analysis, narrative synthesis, scoping review |

#### Node 1A: Original Papers

Purpose: Papers with primary data, new results, or experimental/clinical observations.

| Cue type | Signals |
| --- | --- |
| Positive (section) | Methods, Results |
| Positive (lexographic) | in vivo, in vitro, exposure, participants, cohort, sample size, case-control, animal experiment, cell line assay, intervention arm, dose |

#### Node 2A: Original → Clinical

Purpose: Human subjects research with interventions, observational cohorts, or patient outcomes.

| Cue type | Signals |
| --- | --- |
| Positive | participants, patients, randomized, placebo, trial, cohort, adverse events, dose, product form, route of administration |
| Negative | cell culture, primary neurons, mouse, rat, organoid, assay plate |

#### Node 2B: Original → In Vivo

Purpose: Animal studies, whole-organism experiments, pharmacology, and behavioral outcomes.

| Cue type | Signals |
| --- | --- |
| Positive | mouse, mice, rat, hamster, oral gavage, intraperitoneal, behavior test, tissue analysis, sacrificed animals |
| Negative | human participant, clinical trial, patient-reported outcome, chart review |

#### Node 2C: Original → In Vitro

Purpose: Cell-based, biochemical, receptor, and assay studies.

| Cue type | Signals |
| --- | --- |
| Positive | cell line, primary cells, immortalized cells, cultured, incubated, concentration, receptor binding, assay, ELISA, microglia, hepatocytes, fibroblast, macrophage, epithelial cell, THP-1, A549 |
| Negative | patient, participant, trial, animal dosing, behavioral endpoint |

#### Node 2D: Original → Other / Mixed / Unclear

Purpose: Papers that combine multiple study types or do not cleanly fit a single branch.

#### Node 3A: Review → Systematic Review

#### Node 3B: Review → Meta-analysis

#### Node 3C: Review → Narrative / Editorial / Comment / Letter

Purpose: Secondary literature subtypes for routing and restricted extraction.

| Cue type | Signals |
| --- | --- |
| Positive | structured search, PRISMA, included studies, pooled estimate, forest plot, commentary, perspective |
| Negative | original cohort, randomized trial, animal dosing, cell assay |

### Intake Mapping (Expert → `rules_config.json`)

When encoding expert nodes, use four cue strengths where noted: **lexographic (strong)**, **sectional (strong)**, **structural**, and **semantic**.

| Config path | Maps to |
| --- | --- |
| `relevance.positive_cues` / `relevance.negative_cues` | Node 0 ingestion gate |
| `decision_boundaries.*` | Ordered branch rules (e.g. review before original research) |
| `cues.extraction.preclinical_cues` | Nodes 2B, 2C, and in vitro/in vivo sub-branches |
| `cues.extraction.clinical_cues` | Node 2A clinical branch |
| `field_groups.relevance` / `field_groups.extraction` | Hamming scoring in `optimization_log` |
| `reward_function.*` | Patch acceptance weights (gate remains zero-regression today) |

## Goals

- Capture expert cannabis paper classification rules as structured cues in `rules_config.json`. Cues can be lexical (strong), sectional (strong), structural, or semantic.
- Give agents stable API entry points for queue review, recent feedback, and automation status.
- Keep the existing heuristic-first, single-pass LLM architecture intact for cost control.
- Close the learning loop through feedback audits, reliability evaluation, and prompt cue updates.
- Route every paper through a clear hierarchy: Ingestion → Reviews vs Original Papers → study-type branches → field extraction.

## Agent Workflow

1. Poll `/api/agents/automation-status` for queue counts, feedback counters, rules version, and reliability status.
2. Pull low-confidence papers from `/api/classification/queue`.
3. Route each paper through the node hierarchy: Ingestion → Reviews (Node 1B) before Original Papers (Node 1A) → subtype branches (2A–2D, 3A–3C).
4. Compare each paper against the expert decision chart and recent corrections from `/api/feedback/recent`.
5. Submit expert-approved edits through `/api/papers/<paper_id>/edit-classification`.
6. Trigger `/api/classification/run-eval` after enough corrections accumulate or after a major decision-chart update.

## Bounded Calibration Runner

Use `calibration_agent.py` when Claude should supervise a limited learning pass before handing results back for review.

Dry-run candidate selection:

```bash
python3 calibration_agent.py --dry-run --max-calls 50
```

Live 50-attempt A/B calibration:

```bash
python3 calibration_agent.py --max-calls 50 --mode preclinical_original --variants control,decision_checklist
```

The runner:

- Enforces a local `--max-calls` ceiling of 50 classification attempts.
- Defaults to abstract-only classification for predictable budget behavior.
- Alternates configured prompt variants with `CLASSIFIER_PROMPT_VARIANT`.
- Writes `scratch/calibration_runs/<batch_id>.json` and `<batch_id>_walkthrough.md`.
- Updates `papers` and `llm_calls_log` only when not running with `--dry-run`.

## Automation Layers

| Layer | Current implementation | Next extension point |
| --- | --- | --- |
| Relevance gate | `extractor.is_cannabis_related()` | Encode Node 0 branches for dud papers and acronym collisions. |
| Prompt cues | `classifier.compile_system_prompt()` | Patch Node 0–3 cue packs into `rules_config.json`. |
| Review queue | `DatabaseManager.get_low_confidence_papers()` | Add node-level priority reasons from chart routing. |
| Feedback loop | `feedback_audit` plus counters in `system_metadata` | Add correction batch summaries for optimizer prompts. |
| Upward propagation | `retrieve_few_shot_context()` via `feedback_audit_fts` BM25 | Distinct from static cues in `compile_system_prompt()`; injected per paper in `classify_with_llm()`. |
| Optimization logging | `optimization_log` with relevance/extraction Hamming breakdown | `failed_attempts` escalates to `needs_human_review` after 3 rejected patches. |
| Reliability eval | `eval_reliability.py` writes repo-local manifest | Schedule eval when correction threshold is reached. |
| Batch parity | `anthropic_batch_helper.create_batch_requests()` uses cue-aware prompts | Add full dynamic rule parity for PDF batch workflows. |
| Calibration | `calibration_agent.py` | Use expert-reviewed walkthroughs to patch cues and decision-chart branches. |

## Upward Propagation (Phase 1)

At classification time, `classify_with_llm()` calls `retrieve_few_shot_context()` before the Anthropic API request. The function:

1. Queries `feedback_audit_fts` with BM25 over the incoming title/abstract.
2. Pulls field-level corrections for the top matching papers.
3. Injects them as dynamic few-shot examples separate from static cues in `compile_system_prompt()`.
4. Sets `bm25_retrieval_used = 1` in `llm_calls_log` when retrieval fires.

## Optimization Gate

`rule_optimizer.py` scores each candidate patch with separate relevance and extraction Hamming loss, logs both to `optimization_log.field_group_scores`, and applies a zero-regression gate. Three consecutive rejected patches set optimization status to `needs_human_review`.

## Guardrails

- Keep expert edits auditable in `feedback_audit`.
- Treat `expert_locked_fields` as authoritative during reclassification.
- Prefer config patches over prompt rewrites when adding chart details.
- Validate every rules change against the test suite and a representative low-confidence queue sample.
- Enforce Node 1B before Node 1A when routing review vs original-research papers.
