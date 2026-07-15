# Perpetual low-cost classification learning — ops checklist

## Always-on ($0 tokens)
- Fly nightly Maude+heuristic: `python3 scheduled_jobs.py schedule-maude-reingest --at 23:00 --maude-and-heuristic` (America/Toronto)
- Post-harvest OA PDF upgrade: wired in `app.py` → `scheduled_jobs.run_post_harvest_maude_upgrade`
- PDF reuse: `SKIP_PDF_FETCH=1` + `scratch/paper_cache/`

## Weekly Loop A (cheap discovery)
```bash
MAX_CALLS=10 SKIP_PDF_FETCH=1 DEPLOY_FIRST=0 RUN_FEEDBACK=1 LOCAL_FEEDBACK=1 \
  AUTO_IMPLEMENT=1 ./scripts/run_perpetual_loop_a_cycle.sh
```
Agent prompt: `scripts/perpetual_loop_a_automation_prompt.md`

### Cursor Automation (finish in editor)
Create a scheduled automation (e.g. Mondays 14:00 America/Toronto) that runs the prompt in `scripts/perpetual_loop_a_automation_prompt.md` against repo `shawnmckenzie11/Cannabis-Paper-Scraper` branch `main`. Do not enable golden AUTO_ADVANCE.

## Golden (gated, not perpetual)
See `.cursor/rules/golden-endpoint-policy.mdc`. Next queue: `node2a.clinical_observational.oral` with `GOLDEN_FULL_SUBNODE_REINGEST=0`.
