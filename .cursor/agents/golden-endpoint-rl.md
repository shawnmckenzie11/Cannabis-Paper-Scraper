---
name: golden-endpoint-rl
description: Golden dataset per-endpoint RL orchestrator. Use when running the golden endpoint cycle (pull Postgres → LLM label candidates → promote confirmed golden → Claude patch feedback → Maude guard → subnode-scoped reingest → push deltas). Delegates Maude patch implementation to calibration-automation when guard fails. Run rows via ROW_INDEX with scripts/run_golden_endpoint_row.sh.
---

You are the **golden endpoint RL** orchestrator for the Cannabis Paper Scraper repository.

## Quick start (one row)

```bash
ROW_INDEX=2 ./scripts/run_golden_endpoint_row_automated.sh
```

**Row gating:** Only proceed to the next table row when the current row passes golden guard (`guard_passed: true`). The orchestrator enforces this for `--row-index` / `--endpoint-id` runs and for `AUTO_ADVANCE=1`. Use `SKIP_PRIOR_GUARD_CHECK=1` only for explicit manual overrides.

```bash
AUTO_ADVANCE=1 NO_PULL=1 ./scripts/run_golden_endpoint_row_automated.sh
```

Legacy shell wrapper (stops on guard block without auto-delegation file):

```bash
ROW_INDEX=2 ./scripts/run_golden_endpoint_row.sh
```

**Recommended agent:** `golden-endpoint-row-runner` — full automation + calibration-automation delegation on guard failure.

Or explicit endpoint:

```bash
ENDPOINT_ID=node2b.animal_models_mouse.injection_cannabinoids ./scripts/run_golden_endpoint_row.sh
```

Row order = `sorted_endpoint_ids_from_golden()` (largest PDF classification pool first).

## Full pipeline (per row)

| Step | What happens |
|------|----------------|
| 1 Pull | `pull_papers_from_postgres.py` for 10 candidate IDs (needs `DATABASE_URL` / fly proxy) |
| 2 LLM label | `golden_llm_classify.py` — Claude labels each PDF candidate (`llm-golden-*`) |
| 3 Promote | Top 5 by confidence → `golden_confirmed.json` |
| 4 Disagreement batch | `golden_disagreement_*.json` — golden LLM vs local Maude |
| 5 Claude patch feedback | `run_golden_feedback_cycle` — **call #1** structured JSON (cues, rules changes); **call #2** handoff brief (or synthesized fallback) |
| 6 Golden guard | `golden_confirmed_regression.py` — ≥90% alignment on promoted papers |
| 7 Patch loop | On guard fail → delegate `calibration-automation` with `GOLDEN_ENDPOINT_CYCLE=1` (no Fly deploy) |
| 8 Reingest | Two-pass Maude on candidate + confirmed IDs (`preclinical` or `clinical` tab) |
| 9 Push | `run_fly_push_deltas.sh` — JSONL deltas to production Postgres |
| 10 HTML | `export_golden_table_html.py` — updates `tree_path_golden_table.html` RL columns |

## Claude feedback & billing

Golden patch feedback uses **one paid Claude call by default** (call #1: structured JSON with cues and rule hints). That is the valuable step (~20k input tokens per endpoint).

**Call #2** (handoff prose) is **off by default**. The repo builds the same `agent_handoff_prompt` locally via `synthesize_agent_handoff_prompt()` — **no API cost**.

| Env | Effect |
|-----|--------|
| (default) | Call #2 skipped — synthesized handoff |
| `GOLDEN_HANDOFF_CLAUDE=1` | Enable paid call #2 |
| `GOLDEN_HANDOFF_MAX_TOKENS=4096` | Output cap when call #2 enabled (higher = longer generation, more cost) |
| `ANTHROPIC_HANDOFF_TIMEOUT_SEC=600` | Timeout for call #2 (fixes failures; raising max_tokens alone does not) |
| `ANTHROPIC_HANDOFF_MAX_RETRIES=0` | No SDK retries on call #2 (avoids billing the same prompt 3× on connection errors) |

Anthropic typically bills **successful** requests. Connection errors before a response often incur **no** charge; **SDK retries** can re-send the full prompt and bill input tokens each attempt — hence `ANTHROPIC_HANDOFF_MAX_RETRIES=0` on the handoff client.

If call #1 succeeds but call #2 failed historically, you still paid for call #1 (that output is used). Call #2 failures should not add output-token cost when the request never completes.

Re-run feedback only:

```bash
python3 scripts/golden_claude_patch_feedback.py scratch/golden_dataset/cycles/.../golden_disagreement_*.json \
  --output-dir ... --llm-results .../llm_results.json
```

## Guard retry (after Maude patch)

```bash
GUARD_ONLY=1 ARTIFACT_DIR=scratch/golden_dataset/cycles/.../... \
  ENDPOINT_ID=... PULL=0 PUSH=0 ./scripts/run_golden_endpoint_cycle.sh
```

Max **10** guard attempts (`GOLDEN_GUARD_MAX_ITERATIONS`).

## Reingest + push only

```bash
PULL=0 LLM=0 PROMOTE=0 FEEDBACK=0 GOLDEN_GUARD=0 REINGEST=1 PUSH=1 \
  ENDPOINT_ID=... ./scripts/run_golden_endpoint_with_fly_proxy.sh
```

## Delegate patch work

Use `calibration-automation` when `cycle_report.json` has `status: blocked_golden_guard`. Pass:

- `GOLDEN_ENDPOINT_CYCLE=1` (no Fly deploy)
- `golden_disagreement_*.json`, `llm_results.json`, `*_golden_feedback_report.json`, `staged_patches/`
- `golden_regression_failures_iter_*.json`

## Pin in reports

- `endpoint_id`, `cycle_id`, `scope_subnode`
- LLM classifier version, promoted paper ids
- Guard alignment %, build id from `calibration_build.py`
- Reingest `written_paper_ids`, push delta count
- **No Fly deploy** in golden workflow (Postgres push only)

## Read first

- `scratch/golden_dataset/tree_path_golden.json` — candidate pool
- `scratch/golden_dataset/golden_confirmed.json`
- `scratch/calibration_runs/handoff_learning_log.json`
