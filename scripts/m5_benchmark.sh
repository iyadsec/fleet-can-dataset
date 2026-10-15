#!/usr/bin/env bash
# Milestone 5 storage + execution-mode benchmark. For every config/m5/bench/experiment_*.yaml:
#   fast mode, CSV        -> <ID>_CSV_FAST
#   fast mode, Parquet    -> <ID>_PQ_FAST
#   real time, Parquet    -> <ID>_PQ_RT   (3/5/10-vehicle replay configs only)
# Each run is measured with /usr/bin/time -v (wall, CPU %, max RSS) into data/benchmarks/m5/<session>.time
# and analysed (descriptors). Comparison/report: scripts/m5_bench_report.py
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
# shellcheck disable=SC1091
source .venv/bin/activate
mkdir -p data/benchmarks/m5
for cfg in config/m5/bench/experiment_*.yaml; do
  name=$(basename "$cfg" .yaml | sed 's/^experiment_//' | tr a-z A-Z)
  modes=("CSV_FAST:--fast --storage csv" "PQ_FAST:--fast --storage parquet")
  [[ "$name" == REPLAY_* ]] && modes+=("PQ_RT:--realtime --storage parquet")
  for m in "${modes[@]}"; do
    tag=${m%%:*}; args=${m#*:}; sid="M5_BENCH_${name}_${tag}"
    rm -rf data/{raw,ground_truth,metadata,validation,analysis}/"$sid"
    /usr/bin/time -v -o "data/benchmarks/m5/$sid.time" python -m src.orchestrator --config "$cfg" $args --session-id "$sid" > "data/benchmarks/m5/$sid.log" 2>&1
    v=$(grep -oE 'VALIDATION: [A-Z]+' "data/benchmarks/m5/$sid.log" | tail -1)
    python -m analysis.run_session "$sid" > /dev/null 2>&1; dv=$?
    echo "$sid  ${v:-NO RESULT}  descriptors_exit=$dv"
  done
done
