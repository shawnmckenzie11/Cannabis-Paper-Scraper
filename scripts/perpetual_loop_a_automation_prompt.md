# Perpetual low-cost Loop A — Cursor Automation prompt

Run one cheap RL discovery cycle for this Cannabis Paper Scraper repo. Do not run golden LLM endpoint automation.

## Hard caps
- `MAX_CALLS=10` (never higher)
- `SKIP_PDF_FETCH=1`
- `LOCAL_FEEDBACK=1`
- `DEPLOY_FIRST=0` for the batch step
- One subnode per run (from `calibration_rl_alternating_loop.py plan-next`)
- No `AUTO_ADVANCE`, no golden LLM classify, no `GOLDEN_HANDOFF_CLAUDE=1`
- Use `./venv/bin/python`; read `.cursor/rules/rl-calibration.mdc` and `.cursor/rules/sqlite-postgres-caches.mdc`

## Steps
1. `python3 calibration_rl_alternating_loop.py plan-next` — note subnode, offset, holdout_batch_id, prior holdout %.
2. Run:
   `MAX_CALLS=10 SKIP_PDF_FETCH=1 DEPLOY_FIRST=0 RUN_FEEDBACK=1 LOCAL_FEEDBACK=1 AUTO_IMPLEMENT=1 ./scripts/run_perpetual_loop_a_cycle.sh`
   - Exit 2 + `HANDOFF_STAGED_PATCH=...` means implement the staged patch (extractor/maude_classifier/maude_cues + tests), bump `calibration_build.py`, `fly deploy --remote-only -a cannabis-paper-scraper`, then refresh the **same** holdout with `SKIP_PDF_FETCH=1`.
3. Only if same-holdout scoped alignment **improves or holds** (≥ prior − 0.05): `SUBNODE={sub} PUSH=1 ./scripts/run_loop_a_finish.sh` (or re-run perpetual script with `SKIP_BATCH=1 AUTO_IMPLEMENT=0` after deploy).
4. If alignment **regresses**: do **not** PUSH; append a note to `scratch/calibration_runs/handoff_learning_log.json` and stop.
5. Update `scratch/calibration_runs/rl_alternating_loop_state.json` / handoff log with pre/post alignment, build id, batch filename.

## Outcome
Report subnode, batch path, pre/post holdout alignment %, whether PUSH ran, build id. Dashboard: https://cannabis-paper-scraper.fly.dev/calibration/dashboard
