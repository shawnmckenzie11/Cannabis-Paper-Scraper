#!/usr/bin/env bash
# Perpetual low-cost Loop A cycle (one subnode per invocation).
# Cheap path: PDF Maude A/B + LOCAL_FEEDBACK + holdout refresh gate before PUSH.
# Does NOT run golden LLM / AUTO_ADVANCE.
#
# Env:
#   MAX_CALLS          default 10 (hard cap)
#   SKIP_PDF_FETCH     default 1
#   DEPLOY_FIRST       default 0
#   RUN_FEEDBACK       default 1
#   LOCAL_FEEDBACK     default 1
#   AUTO_IMPLEMENT     default 0 (exit 2 with HANDOFF_STAGED_PATCH when patch exists)
#   PUSH_ON_IMPROVE    default 1
#   MIN_HOLDOUT_PCT    optional absolute floor before PUSH
#   SKIP_BATCH         default 0 (1 = skip batch; refresh+finish only)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x ./venv/bin/python ]]; then
  echo "Missing ./venv/bin/python" >&2
  exit 1
fi

MAX_CALLS="${MAX_CALLS:-10}"
if (( MAX_CALLS > 10 )); then
  echo "Capping MAX_CALLS=${MAX_CALLS} → 10 (perpetual budget)" >&2
  MAX_CALLS=10
fi
SKIP_PDF_FETCH="${SKIP_PDF_FETCH:-1}"
DEPLOY_FIRST="${DEPLOY_FIRST:-0}"
RUN_FEEDBACK="${RUN_FEEDBACK:-1}"
LOCAL_FEEDBACK="${LOCAL_FEEDBACK:-1}"
PUSH_ON_IMPROVE="${PUSH_ON_IMPROVE:-1}"
SKIP_BATCH="${SKIP_BATCH:-0}"
export SKIP_PDF_FETCH
mkdir -p scratch/calibration_runs scratch/patch_reports/calibration_a

PLAN_PATH="scratch/calibration_runs/perpetual_loop_a_last_plan.json"
echo "==> plan-next"
./venv/bin/python calibration_rl_alternating_loop.py plan-next >"$PLAN_PATH.raw" 2>"$PLAN_PATH.err" || true
./venv/bin/python - <<PY
from pathlib import Path
raw = Path("$PLAN_PATH.raw").read_text(encoding="utf-8")
idx = raw.find("{")
if idx < 0:
    raise SystemExit(f"plan-next produced no JSON: {raw[:500]!r}")
Path("$PLAN_PATH").write_text(raw[idx:], encoding="utf-8")
print(raw[idx:])
PY

eval "$(./venv/bin/python - <<'PY'
import json
from pathlib import Path
plan = json.loads(Path("scratch/calibration_runs/perpetual_loop_a_last_plan.json").read_text())
sub = plan["subnode"]
offset = int(plan.get("offset") or 10)
latest = plan.get("latest_holdout_alignment_pct") or {}
prior = latest.get(sub)
holdout_id = plan.get("holdout_batch_id") or ""
holdout = ""
if holdout_id:
    base = Path("scratch/calibration_runs")
    for cand in (base / f"{holdout_id}.json", base / holdout_id):
        if cand.exists():
            holdout = str(cand)
            break
def sh(v):
    return "'" + str(v).replace("'", "'\"'\"'") + "'"
print(f"SUBNODE={sh(sub)}")
print(f"OFFSET={offset}")
print(f"PRIOR_PCT={sh('' if prior is None else prior)}")
print(f"HOLDOUT_BATCH={sh(holdout)}")
PY
)"

echo "==> Loop A subnode=${SUBNODE} offset=${OFFSET} MAX_CALLS=${MAX_CALLS} prior_holdout=${PRIOR_PCT:-none}"

if [[ "$SKIP_BATCH" != "1" ]]; then
  SUBNODE="$SUBNODE" MAX_CALLS="$MAX_CALLS" OFFSET="$OFFSET" \
    DEPLOY_FIRST="$DEPLOY_FIRST" RUN_FEEDBACK="$RUN_FEEDBACK" LOCAL_FEEDBACK="$LOCAL_FEEDBACK" \
    ./scripts/run_subnode_calibration.sh
fi

STAGED="$(ls -1t scratch/calibration_runs/staged_patches/${SUBNODE}_*.json 2>/dev/null | head -1 || true)"
BATCH_OUT="$(ls -1t scratch/calibration_runs/${SUBNODE}_calibration_*.json 2>/dev/null | head -1 || true)"
echo "STAGED_PATCH=${STAGED:-none}"
echo "BATCH_JSON=${BATCH_OUT:-none}"

if [[ -n "${STAGED:-}" && "${AUTO_IMPLEMENT:-0}" == "1" ]]; then
  echo "==> AUTO_IMPLEMENT=1: staged patch present — implement → deploy → refresh before finish"
  echo "HANDOFF_STAGED_PATCH=$STAGED"
  exit 2
fi

REFRESH_SRC="${HOLDOUT_BATCH:-}"
if [[ -z "$REFRESH_SRC" && -n "${BATCH_OUT:-}" ]]; then
  REFRESH_SRC="$BATCH_OUT"
fi

REFRESH_JSON=""
if [[ -n "$REFRESH_SRC" && -f "$REFRESH_SRC" ]]; then
  echo "==> refresh holdout $REFRESH_SRC"
  SKIP_PDF_FETCH=1 ./venv/bin/python calibration_agent.py --refresh-maude-from-batch "$REFRESH_SRC"
  REFRESH_JSON="$(ls -1t scratch/calibration_runs/calibration_*.json | head -1)"
fi

if [[ "$PUSH_ON_IMPROVE" != "1" ]]; then
  echo "==> skip finish/PUSH (PUSH_ON_IMPROVE=0)"
  exit 0
fi
if [[ -z "${REFRESH_JSON:-}" || ! -f "$REFRESH_JSON" ]]; then
  echo "==> skip finish/PUSH (no refresh JSON)"
  exit 0
fi

export REFRESH_JSON SUBNODE PRIOR_PCT
./venv/bin/python - <<'PY'
import json
import os
import sys
from pathlib import Path

import classification_schema
import subnode_field_scopes

refresh_path = os.environ["REFRESH_JSON"]
refresh = json.loads(Path(refresh_path).read_text(encoding="utf-8"))
prior_raw = (os.environ.get("PRIOR_PCT") or "").strip()
prior_pct = float(prior_raw) if prior_raw else None
subnode = os.environ["SUBNODE"]
agreed = total = 0
for row in refresh.get("results") or []:
    llm, maude = row.get("llm") or {}, row.get("maude") or {}
    if not llm or not maude:
        continue
    scoped = subnode_field_scopes.compare_scoped_fields(
        maude, llm, subnode, classification_schema.compare_field_values
    )
    fields = (scoped or {}).get("fields") or {}
    agreed_f = (scoped or {}).get("agreed_fields") or {}
    total += len(fields) + len(agreed_f)
    agreed += len(agreed_f)
align = round(100.0 * agreed / total, 1) if total else None
print(f"HOLDOUT_ALIGNMENT_PCT={align}")
min_floor = (os.environ.get("MIN_HOLDOUT_PCT") or "").strip()
if min_floor and (align is None or align < float(min_floor)):
    print(f"REFUSE_PUSH: align {align} < MIN_HOLDOUT_PCT {min_floor}")
    sys.exit(3)
if prior_pct is not None and align is not None and align + 0.05 < prior_pct:
    print(f"REFUSE_PUSH: align {align} regressed vs prior {prior_pct}")
    sys.exit(3)
Path("scratch/calibration_runs/perpetual_loop_a_last_refresh.json").write_text(
    json.dumps(
        {
            "subnode": subnode,
            "refresh_json": refresh_path,
            "alignment_pct": align,
            "prior_pct": prior_pct,
        },
        indent=2,
    ),
    encoding="utf-8",
)
PY

echo "==> holdout OK — finish + PUSH"
SUBNODE="$SUBNODE" PUSH=1 ./scripts/run_loop_a_finish.sh
echo "==> perpetual Loop A cycle complete for ${SUBNODE}"
