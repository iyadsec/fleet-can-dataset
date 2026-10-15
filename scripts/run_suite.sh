#!/usr/bin/env bash
# Run every experiment_*.yaml in a config directory, one after another; one summary line per run.
# Usage: scripts/run_suite.sh config/m3 [name ...]      (exit non-zero if any run fails validation)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DIR="${1:?config directory}"; shift
# shellcheck disable=SC1091
source .venv/bin/activate
names=("$@")
if (( ${#names[@]} == 0 )); then
  mapfile -t names < <(ls "$DIR"/experiment_*.yaml | sed 's|.*/experiment_||; s|\.yaml$||')
fi
FAIL=0
for n in "${names[@]}"; do
  N=$(python -c "from src.config import load_experiment; print(len(load_experiment('$DIR/experiment_$n.yaml').fleet))")
  scripts/verify_environment.sh "$N" >/dev/null || { echo "$n  environment not ready for $N vehicles"; FAIL=1; continue; }
  out=$(python -m src.orchestrator --config "$DIR/experiment_$n.yaml" 2>&1)
  sid=$(grep -oE '^\[M[0-9]_[A-Z0-9_]+_[0-9]{8}T[0-9]{6}Z\]' <<<"$out" | head -1 | tr -d '[]')
  verdict=$(grep -oE 'VALIDATION: [A-Z]+' <<<"$out" | tail -1)
  echo "$n  $sid  ${verdict:-NO RESULT}"
  [[ "$verdict" == "VALIDATION: PASS" ]] || { FAIL=1; grep -E '\[FAIL\]|Error|Traceback|SystemExit' <<<"$out" | head -20; }
done
exit "$FAIL"
