#!/usr/bin/env bash
# Continuous alternating node2b→node2c→node2a RL cycles until 85% alignment.
# Each cycle: Fly batch (10 fresh papers via OFFSET) → local feedback → implement patch
# → deploy → same-holdout refresh → handoff log → optional targeted pass → next node.
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
TARGET_PCT="${TARGET_ALIGNMENT_PCT:-85}"
MAX_CYCLES="${MAX_CYCLES:-999}"

echo "==> RL alternating loop (target ${TARGET_PCT}% per node, max ${MAX_CYCLES} cycles)"
echo "    State: scratch/calibration_runs/rl_alternating_loop_state.json"
echo "    Plan next: python3 calibration_rl_alternating_loop.py plan-next"
echo ""
echo "This script is a pointer — run cycles via calibration-automation agent or:"
echo "  python3 calibration_rl_alternating_loop.py status"
echo ""
echo "Per-cycle commands (example node2b offset 10):"
echo "  SUBNODE=node2b MAX_CALLS=10 OFFSET=10 DEPLOY_FIRST=0 RUN_FEEDBACK=1 ./scripts/run_subnode_calibration.sh"
echo "  # implement patch + tests"
echo "  fly deploy --remote-only -a ${APP}"
echo "  python3 calibration_agent.py --refresh-maude-from-batch scratch/calibration_runs/{batch}.json"
echo ""
echo "Stop when: python3 calibration_rl_alternating_loop.py status  → target_met true"
