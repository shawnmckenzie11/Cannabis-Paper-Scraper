#!/usr/bin/env bash
# Run one golden-endpoint RL row: pull → LLM → promote → Claude feedback → guard → reingest → push.
# Usage: ROW_INDEX=2 ./scripts/run_golden_endpoint_row.sh
#        ENDPOINT_ID=node2b... ./scripts/run_golden_endpoint_row.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ROW_INDEX="${ROW_INDEX:-}"
ENDPOINT_ID="${ENDPOINT_ID:-}"
SKIP_PUSH="${SKIP_PUSH:-0}"
DELEGATE_PATCH="${DELEGATE_PATCH:-1}"

if [[ -z "$ROW_INDEX" && -z "$ENDPOINT_ID" ]]; then
  echo "Set ROW_INDEX or ENDPOINT_ID" >&2
  exit 1
fi

export ANTHROPIC_TIMEOUT_SEC="${ANTHROPIC_TIMEOUT_SEC:-600}"
export ANTHROPIC_MAX_RETRIES="${ANTHROPIC_MAX_RETRIES:-5}"
# Handoff call #2 is OFF by default (free synthesize). Set GOLDEN_HANDOFF_CLAUDE=1 to enable.
export GOLDEN_HANDOFF_CLAUDE="${GOLDEN_HANDOFF_CLAUDE:-0}"

from_env() {
  python3 - <<'PY'
import json
from scripts.golden_endpoint_cycle import load_tree_path_golden, sorted_endpoint_ids_from_golden, endpoint_block_from_golden
import os
golden = load_tree_path_golden()
eid = os.environ.get("ENDPOINT_ID")
if not eid:
    idx = int(os.environ.get("ROW_INDEX", "0"))
    eid = sorted_endpoint_ids_from_golden(golden)[idx]
block = endpoint_block_from_golden(golden, eid)
print(json.dumps({"endpoint_id": eid, "paper_ids": [p["paper_id"] for p in block.get("papers") or []]}))
PY
}

META="$(from_env)"
EID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['endpoint_id'])" "$META")"
echo "=== golden row: $EID ==="

# Phase 1: cycle through guard (no push yet)
export PULL=1 PUSH=0
if [[ -n "$ROW_INDEX" ]]; then
  export ROW_INDEX
  unset ENDPOINT_ID
else
  export ENDPOINT_ID="$EID"
  unset ROW_INDEX
fi

bash scripts/run_golden_endpoint_with_fly_proxy.sh || {
  echo "Fly proxy cycle failed; retrying local pull skip..." >&2
  export PULL=0
  bash scripts/run_golden_endpoint_cycle.sh
}

ARTIFACT_DIR="$(ls -td "scratch/golden_dataset/cycles/${EID}/${EID}"_* 2>/dev/null | head -1)"
if [[ -z "$ARTIFACT_DIR" ]]; then
  echo "No artifact dir for $EID" >&2
  exit 1
fi

CYCLE_REPORT="$ARTIFACT_DIR/cycle_report.json"
STATUS="$(python3 -c "import json; print(json.load(open('$CYCLE_REPORT')).get('status',''))")"

if [[ "$STATUS" == "blocked_golden_guard" && "$DELEGATE_PATCH" == "1" ]]; then
  echo "Guard blocked — run calibration-automation with GOLDEN_ENDPOINT_CYCLE=1 on:"
  echo "  $ARTIFACT_DIR"
  echo "Then: GUARD_ONLY=1 ARTIFACT_DIR=$ARTIFACT_DIR ENDPOINT_ID=$EID PULL=0 PUSH=0 ./scripts/run_golden_endpoint_cycle.sh"
  exit 2
fi

if [[ "$STATUS" != "completed" ]]; then
  echo "Cycle status: $STATUS (expected completed after guard)" >&2
  exit 1
fi

# Phase 2: reingest + push
export PULL=0 LLM=0 PROMOTE=0 FEEDBACK=0 GOLDEN_GUARD=0 REINGEST=1
if [[ "$SKIP_PUSH" == "1" ]]; then
  export PUSH=0
else
  export PUSH=1
fi
export ENDPOINT_ID="$EID"
unset ROW_INDEX
bash scripts/run_golden_endpoint_with_fly_proxy.sh || {
  export PULL=0
  bash scripts/run_golden_endpoint_cycle.sh
}

python3 scripts/export_golden_table_html.py
echo "=== golden row finished: $EID ==="
