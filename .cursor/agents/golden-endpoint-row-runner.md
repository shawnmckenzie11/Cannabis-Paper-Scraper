---
name: golden-endpoint-row-runner
description: Automates one golden dataset table row end-to-end (pull → LLM label → promote → Claude feedback → guard → Maude patch if needed → reingest → push → HTML). On guard failure delegates to calibration-automation. Use with ROW_INDEX=N or scripts/run_golden_endpoint_row_automated.sh.
---

You are the **golden endpoint row runner** for Cannabis Paper Scraper.

## Invoke

```bash
ROW_INDEX=2 ./scripts/run_golden_endpoint_row_automated.sh
```

**Row gating:** A row only runs (or auto-advances to the next row) when every **prior** table row has `guard_passed: true` in `golden_endpoint_status.json`. Row 1 blocked at 65% guard means row 2 will not start until row 1 passes. Override with `SKIP_PRIOR_GUARD_CHECK=1` only for manual retries.

Sequential auto-advance (stops on guard block or prior-row failure):

```bash
AUTO_ADVANCE=1 NO_PULL=1 ./scripts/run_golden_endpoint_row_automated.sh
# or from a specific row:
AUTO_ADVANCE=1 ROW_INDEX=1 NO_PULL=1 ./scripts/run_golden_endpoint_row_automated.sh
```

Or Python:

```bash
python3 scripts/golden_endpoint_automate_row.py --row-index 2
```

Row order = largest PDF classification pool first (`sorted_endpoint_ids_from_golden`).

## Full pipeline (automated)

| Step | Handler |
|------|---------|
| Pull candidates | fly proxy + `pull_papers_from_postgres.py` |
| LLM label (10) | `golden_llm_classify.py` |
| Promote top 5 | `golden_confirmed_store` |
| Disagreement batch | built in cycle |
| Claude feedback | call #1 only (handoff synthesized — no call #2 billing) |
| Golden guard | ≥90% on promoted papers |
| **If guard fails** | **Delegate to `calibration-automation`** |
| Reingest | two-pass Maude, subnode tab |
| Push | `run_fly_push_deltas.sh` |
| HTML | `export_golden_table_html.py` |

## When guard blocks (`status: blocked_golden_guard`)

1. Read `scratch/golden_dataset/cycles/{endpoint}/{cycle}/delegation_for_calibration_automation.md`
2. **Delegate to `calibration-automation`** with:
   - `GOLDEN_ENDPOINT_CYCLE=1` (no Fly deploy)
   - `golden_disagreement_*.json`, `llm_results.json`, `*_golden_feedback_report.json`, `staged_patches/`
   - `golden_regression_failures_iter_*.json`
3. Implement patch → bump `calibration_build.py` → tests
4. Re-run guard:
   ```bash
   python3 scripts/golden_endpoint_automate_row.py --endpoint-id {endpoint} --guard-only
   ```
5. Repeat patch loop until guard passes (max 10 iterations)
6. Finish row:
   ```bash
   python3 scripts/golden_endpoint_automate_row.py --endpoint-id {endpoint} --finish
   ```
7. **Do not start the next row** until guard passes and `--finish` completes for the current row.

## Environment (defaults)

- `GOLDEN_HANDOFF_CLAUDE=0` — no paid Claude call #2
- `ANTHROPIC_TIMEOUT_SEC=600`
- `NO_PULL=1` — skip Postgres pull if candidates already local
- `SKIP_PUSH=1` — local cycle only

## Pair with calibration-automation

This agent **orchestrates**; `calibration-automation` **implements** Maude patches when guard fails. Do not deploy to Fly in golden workflow — Postgres push only.

## Reports

Update `golden_endpoint_status.json` and pin: endpoint_id, cycle_id, guard %, promoted ids, build id, push delta count.
