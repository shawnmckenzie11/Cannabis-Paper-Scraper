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

## Automation Layers

| Layer | Current implementation | Next extension point |
| --- | --- | --- |
| Relevance gate | `extractor.is_cannabis_related()` | Encode chart branches for dud papers and acronym collisions. |
| Prompt cues | `classifier.compile_system_prompt()` | Add expert-provided positive and negative cues to `rules_config.json`. |
| Review queue | `DatabaseManager.get_low_confidence_papers()` | Add priority reasons after the full chart is available. |
| Feedback loop | `feedback_audit` plus counters in `system_metadata` | Add correction batch summaries for optimizer prompts. |
| Reliability eval | `eval_reliability.py` writes repo-local manifest | Schedule eval when correction threshold is reached. |
| Batch parity | `anthropic_batch_helper.create_batch_requests()` uses cue-aware prompts | Add full dynamic rule parity for PDF batch workflows. |

## Decision Chart Intake Format

When expert context arrives, convert it into structured patches:

- `relevance.positive_cues`: terms or patterns that indicate true cannabinoid biology, administration, pharmacology, or outcomes.
- `relevance.negative_cues`: terms or patterns that identify hemp agriculture, non-biological policy/history, acronym collisions, or non-cannabinoid GPR/LPI papers.
- `extraction.preclinical_cues`: animal, cell, ligand, dose, and assay cues.
- `extraction.clinical_cues`: human design, route, product, dose, outcome, and patient context cues.
- `agent_automation`: queue thresholds, eval thresholds, and operational notes for agents.

## Guardrails

- Keep expert edits auditable in `feedback_audit`.
- Treat `expert_locked_fields` as authoritative during reclassification.
- Prefer config patches over prompt rewrites when adding chart details.
- Validate every rules change against the test suite and a representative low-confidence queue sample.
