# Project 1 — Agent runbook

Operator reference for Cursor agents running RL calibration and ops on a **client fork** of the literature scraper. Replace `[client-app]` with the client's Fly app name. Read [`handoff_learning_log.json`](../../../scratch/calibration_runs/handoff_learning_log.json) (or the client project's copy) before every cycle.

## Preflight (required before any production write)

```bash
fly ssh console -a [client-app] -C "sh -c 'cd /app && python3 fly_db_check.py'"
```

Abort if paper count or `DATABASE_URL` / volume path does not match the intended production target.

## Full RL cycle (node2b example)

Copy-paste prompt for the `calibration-automation` agent:

```
Complete RL cycle on node2b: 10-paper PDF holdout → feedback → implement patch → deploy → refresh SAME batch → report alignment/recall + field disagreements. Read handoff_learning_log.json first.
```

### Cycle steps

1. **Read context**
   - `scratch/calibration_runs/handoff_learning_log.json`
   - Latest `scratch/calibration_runs/staged_patches/node2*_*.json`
   - `python3 calibration_rl_alternating_loop.py plan-next`

2. **Batch** (skip if reusing existing holdout JSON)
   ```bash
   SUBNODE=node2b MAX_CALLS=10 OFFSET=0 DEPLOY_FIRST=0 ./scripts/run_subnode_calibration.sh
   ```

3. **Feedback** (local, fast)
   ```bash
   python3 -c "from pathlib import Path; import calibration_feedback_agent as cfa; print(cfa.run_feedback_cycle(Path('scratch/calibration_runs/{batch}.json'), skip_lock=True, local_only=True, skip_refresh=True))"
   ```

4. **Implement** — minimal patch in:
   - `extractor.py`
   - `maude_classifier.py`
   - `maude_cues.json`
   - `test_patch_*.py` when behavior is non-obvious

5. **Bump build ID**
   ```bash
   python3 calibration_build.py
   ```

6. **Deploy**
   ```bash
   fly deploy --remote-only -a [client-app]
   ```

7. **Re-measure same holdout**
   ```bash
   python3 calibration_agent.py --refresh-maude-from-batch scratch/calibration_runs/{batch}.json
   ```

8. **Log** — append entry to `handoff_learning_log.json` with pre/post alignment %, recall %, top disagreeing fields, build ID, files changed.

## Targeted handoff (named fields only)

When the user names diverging fields (e.g. `treatment_duration`, `dose_mg_kg`):

1. Analyze disagreements in the **existing** batch JSON — do not draw a new sample.
2. Implement minimal patches for those fields only.
3. Deploy + refresh **same** batch.
4. Report per-field disagree counts before → after.

## Pin in every report

- Subnode (`node2a`, `node2b`, `node2c`)
- Batch JSON filename
- `MAX_CALLS`, holdout rule (same batch vs offset-0 generalization)
- Pre/post **alignment %**, **recall %**
- Top disagreeing fields
- `MAUDE_CLASSIFIER_BUILD_ID` from `calibration_build.py`

## Key files (touch list)

| File | Role |
| :-- | :-- |
| `extractor.py` / `maude_classifier.py` | Shared extraction + routing |
| `maude_cues.json` | Deterministic cue packs |
| `calibration_build.py` | Fly preflight build ID |
| `calibration_agent.py` | Batch + `--refresh-maude-from-batch` |
| `subnode_field_scopes.py` | Per-branch field agreement scope |
| `rules_config.json` | Confidence thresholds, cue config, RL gates |
| `scratch/calibration_runs/staged_patches/` | Feedback output for implementation |
| `scratch/calibration_runs/handoff_learning_log.json` | Cross-cycle learnings |

## Subnode modes

| SUBNODE | calibration `--mode` |
| :-- | :-- |
| node2a | `node2a_clinical` |
| node2b | `node2b_in_vivo` |
| node2c | `node2c_in_vitro` |
| node1 | `node1_routing` |

## Research catalog dashboard (main UI)

The landing page at `/` uses tab-specific filter profiles from [`dashboard_ui_config.py`](../../../dashboard_ui_config.py) (client clone: `client-config-template/dashboard_ui_config.client.py`).

### Tab order (default landing = All Original Research)

1. **All Original Research** — full filter superset
2. **Pre-Clinical** — node2b + node2c filters (species, in vivo/vitro dose ranges, exposure regimen)
3. **Clinical** — node2a filters (sample size N, dose mg, duration days)
4. **Reviews & Meta-Analyses**
5. **Unclassified** — tangential + unclassified original research

**Recents** is a cross-tab sidebar toggle (`Recently Harvested`), not a tab. It sends `recent_range=` alongside the active tab.

Config is injected into `templates/index.html` via Jinja (`dashboard_config`) and exposed at `GET /api/dashboard-config`. Tab SQL is query-time only in `db_manager._TAB_SQL` — no paper row updates required.

## Calibration dashboard

After pulling batch JSON from Fly:

```bash
fly ssh sftp get -a [client-app] /data/calibration_runs/node2b_calibration_*.json ./scratch/calibration_runs/
python3 calibration_metrics.py --build-dashboard
```

Inspect `scratch/calibration_runs/dashboard.html`.

## Nightly Maude refresh (no LLM cost)

Delegate to the `maude-nightly-reclassify` agent when the client needs bulk reclassification on updated Maude rules without LLM spend.

## Not a completed cycle

- Batch-only or Maude-only refresh without code patch + deploy + same-holdout re-measure
- Feedback without implementation
- New paper sample without explicit user request (except offset-0 generalization every 3 cycles)
