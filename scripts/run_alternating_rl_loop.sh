#!/usr/bin/env bash
# Continuous alternating node2b→node2c→node2a RL cycles until holdout field-subset alignment gate.
# Gate: fixed holdout batches (strain excluded from alignment). Offset-0 every 3 cycles for generalization.
set -euo pipefail

APP="${FLY_APP:-cannabis-paper-scraper}"
TARGET_PCT="${TARGET_ALIGNMENT_PCT:-95}"
MAX_CYCLES="${MAX_CYCLES:-999}"
OFFSET0_EVERY="${OFFSET0_EVERY_N_CYCLES:-3}"

echo "==> RL alternating loop (holdout gate ${TARGET_PCT}%, offset-0 every ${OFFSET0_EVERY} cycles)"
echo "    State: scratch/calibration_runs/rl_alternating_loop_state.json"
echo "    Audit: python3 audit_tier_field_gaps.py"
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
