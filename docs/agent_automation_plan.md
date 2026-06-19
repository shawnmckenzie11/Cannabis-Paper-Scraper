# Agent Automation Plan for Cannabis Paper Classification

Expert decision-tree feedback has been received. This plan turns that chart into an agent-ready operating model for the Cannabis Paper Scraper and maps each node to structured cues in `rules_config.json`.

## Production Database Policy (Required)

**All database writes must target the Fly.io production database.** The live app at `cannabis-paper-scraper.fly.dev` reads from this database; local edits do not appear in the UI.

| Environment | Path / target | Use |
| --- | --- | --- |
| **Production (Fly.io)** | `DATABASE_URL` (Postgres) when set; else `DATABASE_PATH=/data/cannabis_papers.db` on volume `mckenzian_db_volume` | **All ingestion, calibration, reclassification, expert edits, eval runs, and schema migrations that mutate `papers` / logs** |
| **Production artifacts** | `/data/calibration_runs/` on the same Fly volume | Calibration JSON + walkthroughs (persistent; auto-selected by `calibration_agent.resolve_calibration_output_dir()`) |
| **Local dev** | `./cannabis_papers.db` (repo root) | Read-only inspection, tests, and dry-runs only — **never** apply calibration or bulk classification updates locally |

Fly app: `cannabis-paper-scraper` (region `yyz`). Paper writes use whatever `DatabaseManager` resolves (`DATABASE_URL` takes precedence over SQLite). Calibration artifacts on Fly must land under `/data/calibration_runs/`, not `/app/scratch/` (ephemeral).

### Pre-flight check (before any write)

Run on the Fly machine and confirm you are on production:

```bash
fly ssh console -a cannabis-paper-scraper -C "cd /app && python3 fly_db_check.py"
```

Expect `DATABASE_URL set: True` (Postgres) or `DATABASE_PATH: /data/cannabis_papers.db`, and a paper count consistent with production (~21k+). **Abort if the count looks like a dev copy.**

### How agents run write operations on Fly

Use `fly ssh console` so scripts execute inside the deployed app with the mounted volume and secrets (e.g. `ANTHROPIC_API_KEY`):

```bash
# Interactive session (calibration, eval, one-off fixes)
fly ssh console -a cannabis-paper-scraper
cd /app
python3 calibration_agent.py --max-calls 20 --mode node1_routing --variants control,decision_checklist
```

One-shot (non-interactive):

```bash
fly ssh console -a cannabis-paper-scraper -C \
  "cd /app && python3 calibration_agent.py --max-calls 20 --mode node1_routing --variants control,decision_checklist"
```

**Do not** run `calibration_agent.py`, `reclassify_metadata.py`, `harvest.py` (live ingest), `eval_reliability.py`, or bulk SQL updates against the repo-root `cannabis_papers.db` unless the user explicitly requests a local-only experiment.

### Artifacts and read-back

- Calibration JSON and walkthroughs on Fly are written to **`/data/calibration_runs/`** (volume-backed). `entrypoint.sh` creates this directory on startup.
- After a Fly calibration run, pull artifacts for local dashboard inspection:
  ```bash
  fly ssh sftp get -a cannabis-paper-scraper /data/calibration_runs/node1_calibration_*.json ./scratch/calibration_runs/
  fly ssh sftp get -a cannabis-paper-scraper /data/calibration_runs/node1_calibration_*_walkthrough.md ./scratch/calibration_runs/
  ```
- Rebuild the local dashboard from pulled JSON only (`python3 calibration_metrics.py --build-dashboard`); inspect at `scratch/calibration_runs/dashboard.html`. Use the **Decision Tree** sidebar to filter results by node (1B, 1A, 2A–2D, 3A–3C, etc.).
- Prefer production API endpoints (`/api/classification/queue`, `/api/papers/<id>/edit-classification`) when the app is running — they always use the Fly database.

### Code/config changes vs data changes

- **Git-tracked files** (`rules_config.json`, `classifier.py`, etc.): edit in the repo, commit, deploy via `fly deploy`.
- **Paper rows, `llm_calls_log`, `feedback_audit`, `optimization_log`**: mutate only on Fly (SSH scripts or live API), never on local SQLite for automation work.

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

- **Treat the Fly.io SQLite volume (`/data/cannabis_papers.db`) as the single source of truth** for all paper classification state; local SQLite is not a sync target for automation.
- Capture expert cannabis paper classification rules as structured cues in `rules_config.json`. Cues can be lexical (strong), sectional (strong), structural, or semantic.
- Give agents stable API entry points for queue review, recent feedback, and automation status.
- Keep the existing heuristic-first, single-pass LLM architecture intact for cost control.
- Close the learning loop through feedback audits, reliability evaluation, and prompt cue updates.
- Route every paper through a clear hierarchy: Ingestion → Reviews vs Original Papers → study-type branches → field extraction.

## Agent Workflow

0. **Confirm production target** — pre-flight check above; all steps below assume Fly.io DB or the live app API, not local SQLite.
1. Poll `https://cannabis-paper-scraper.fly.dev/api/agents/automation-status` (or SSH on Fly) for queue counts, feedback counters, rules version, and reliability status.
2. Pull low-confidence papers from `/api/classification/queue`.
3. Route each paper through the node hierarchy: Ingestion → Reviews (Node 1B) before Original Papers (Node 1A) → subtype branches (2A–2D, 3A–3C).
4. Compare each paper against the expert decision chart and recent corrections from `/api/feedback/recent`.
5. Submit expert-approved edits through `/api/papers/<paper_id>/edit-classification` (live API → Fly DB).
6. Trigger `/api/classification/run-eval` on Fly after enough corrections accumulate or after a major decision-chart update.

## Bounded Calibration Runner

Use `calibration_agent.py` when Claude should supervise a limited learning pass before handing results back for review. **Run on Fly.io** so `papers` and `llm_calls_log` updates land in production.

Dry-run candidate selection (safe locally or on Fly — no DB writes):

```bash
fly ssh console -a cannabis-paper-scraper -C \
  "cd /app && python3 calibration_agent.py --dry-run --max-calls 50 --mode node1_routing"
```

Live calibration (production DB writes — **Fly only**):

```bash
fly ssh console -a cannabis-paper-scraper -C \
  "cd /app && python3 calibration_agent.py --max-calls 20 --mode node1_routing --variants control,decision_checklist"
```

Example Node 1 routing pass (skip Node 0 when ~20k papers already ingested): three batches of 20, run the live command three times; each batch excludes papers already labeled `llm-node1-calibration-*`.

The runner:

- Enforces a `--max-calls` ceiling of 50 classification attempts per invocation.
- Defaults to abstract-only classification for predictable budget behavior.
- Alternates configured prompt variants with `CLASSIFIER_PROMPT_VARIANT`.
- Writes `scratch/calibration_runs/<batch_id>.json` and `<batch_id>_walkthrough.md` on the machine where it runs.
- Updates `papers` and `llm_calls_log` on **`/data/cannabis_papers.db`** only when not running with `--dry-run` — therefore execute live runs via `fly ssh console`, not against repo-root SQLite.

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
| Calibration | `calibration_agent.py` on **Fly.io only** (live runs) | Use expert-reviewed walkthroughs to patch cues and decision-chart branches; pull JSON artifacts via `fly ssh sftp`. |

## Upward Propagation (Phase 1)

At classification time, `classify_with_llm()` calls `retrieve_few_shot_context()` before the Anthropic API request. The function:

1. Queries `feedback_audit_fts` with BM25 over the incoming title/abstract.
2. Pulls field-level corrections for the top matching papers.
3. Injects them as dynamic few-shot examples separate from static cues in `compile_system_prompt()`.
4. Sets `bm25_retrieval_used = 1` in `llm_calls_log` when retrieval fires.

## Optimization Gate

`rule_optimizer.py` scores each candidate patch with separate relevance and extraction Hamming loss, logs both to `optimization_log.field_group_scores`, and applies a zero-regression gate. Three consecutive rejected patches set optimization status to `needs_human_review`.

## Guardrails

- **Production DB only for writes** — never calibrate, reclassify, ingest, or bulk-update `papers` on local `cannabis_papers.db` during agent automation; use Fly SSH or the live API.
- Run the pre-flight `DATABASE_PATH` / paper-count check before any mutating script.
- Keep expert edits auditable in `feedback_audit` (via live API or Fly-side scripts).
- Treat `expert_locked_fields` as authoritative during reclassification.
- Prefer config patches over prompt rewrites when adding chart details.
- Validate every rules change against the test suite; validate queue/calibration behavior against **production** samples after deploy.
- Enforce Node 1B before Node 1A when routing review vs original-research papers.
- After changing `rules_config.json`, deploy to Fly before running calibration so production prompts match the config version written to `classifier_version`.
