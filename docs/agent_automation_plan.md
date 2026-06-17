# Agent Automation Plan for Cannabis Paper Classification

This plan turns the re-learning proposal into an agent-ready operating model for the Cannabis Paper Scraper. It assumes the expert decision chart and context cues will arrive incrementally.

## Goals

- Capture expert cannabis paper classification rules as structured cues in `rules_config.json`.
- Give agents stable API entry points for queue review, recent feedback, and automation status.
- Keep the existing heuristic-first, single-pass LLM architecture intact for cost control.
- Close the learning loop through feedback audits, reliability evaluation, and prompt cue updates.

## Agent Workflow

1. Poll `/api/agents/automation-status` for queue counts, feedback counters, rules version, and reliability status.
2. Pull low-confidence papers from `/api/classification/queue`.
3. Compare each paper against the expert decision chart and recent corrections from `/api/feedback/recent`.
4. Submit expert-approved edits through `/api/papers/<paper_id>/edit-classification`.
5. Trigger `/api/classification/run-eval` after enough corrections accumulate or after a major decision-chart update.

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
| Relevance gate | `extractor.is_cannabis_related()` | Encode chart branches for dud papers and acronym collisions. |
| Prompt cues | `classifier.compile_system_prompt()` | Add expert-provided positive and negative cues to `rules_config.json`. |
| Review queue | `DatabaseManager.get_low_confidence_papers()` | Add priority reasons after the full chart is available. |
| Feedback loop | `feedback_audit` plus counters in `system_metadata` | Add correction batch summaries for optimizer prompts. |
| Upward propagation | `retrieve_few_shot_context()` via `feedback_audit_fts` BM25 | Distinct from static cues in `compile_system_prompt()`; injected per paper in `classify_with_llm()`. |
| Optimization logging | `optimization_log` with relevance/extraction Hamming breakdown | `failed_attempts` escalates to `needs_human_review` after 3 rejected patches. |
| Reliability eval | `eval_reliability.py` writes repo-local manifest | Schedule eval when correction threshold is reached. |
| Batch parity | `anthropic_batch_helper.create_batch_requests()` uses cue-aware prompts | Add full dynamic rule parity for PDF batch workflows. |
| Calibration | `calibration_agent.py` | Use expert-reviewed walkthroughs to patch cues and decision-chart branches. |

## Decision Chart Intake Format

When expert context arrives, convert it into structured patches:

- `relevance.positive_cues`: terms or patterns that indicate true cannabinoid biology, administration, pharmacology, or outcomes.
- `relevance.negative_cues`: terms or patterns that identify hemp agriculture, non-biological policy/history, acronym collisions, or non-cannabinoid GPR/LPI papers.
- `extraction.preclinical_cues`: animal, cell, ligand, dose, and assay cues.
- `extraction.clinical_cues`: human design, route, product, dose, outcome, and patient context cues.
- `agent_automation`: queue thresholds, eval thresholds, and operational notes for agents.
- `field_groups`: relevance vs extraction fields used for per-run Hamming scoring in `optimization_log`.
- `reward_function`: `lambda_cost`, `lambda_fallback`, and `lambda_regression` weights for future acceptance tuning (zero-regression gate remains binary today).

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
