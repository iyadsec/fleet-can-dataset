#!/usr/bin/env bash
# Milestone 2: run every single-vehicle attack experiment in config/m2/ (vehicle_01), one after another.
# Each run validates itself; this script prints one summary line per run and exits non-zero if any fails.
# Usage: scripts/run_m2_suite.sh [name ...]   (default: all config/m2/experiment_*.yaml)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
scripts/verify_environment.sh 3 >/dev/null || { echo "environment not ready"; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate
names=("$@")
if (( ${#names[@]} == 0 )); then
  mapfile -t names < <(ls config/m2/experiment_*.yaml | sed 's|.*/experiment_||; s|\.yaml$||')
fi
FAIL=0
for n in "${names[@]}"; do
  out=$(python -m src.orchestrator --config "config/m2/experiment_$n.yaml" 2>&1)
  sid=$(grep -oE '^\[M2_[A-Z_]+_[0-9TZ]+\]' <<<"$out" | head -1 | tr -d '[]')
  verdict=$(grep -oE 'VALIDATION: [A-Z]+' <<<"$out" | tail -1)
  echo "$n  $sid  $verdict"
  [[ "$verdict" == "VALIDATION: PASS" ]] || { FAIL=1; grep -E '\[FAIL\]|Error|Traceback' <<<"$out" | head -20; }
done
exit "$FAIL"
