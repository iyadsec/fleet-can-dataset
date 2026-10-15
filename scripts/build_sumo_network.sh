#!/usr/bin/env bash
# Regenerate the synthetic SUMO road network used by the scenarios.
# 4x4 grid, 200 m blocks, 50 km/h limit, traffic lights at junctions with >2 roads.
# The generated file is committed; re-running with the same SUMO version gives the same network.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
netgenerate --grid --grid.number 4 --grid.length 200 \
  --default.speed 13.89 --default.lanenumber 1 \
  --tls.guess true --tls.guess.threshold 0 \
  --seed 42 \
  -o "$ROOT/sumo/network/grid4x4.net.xml"
echo "wrote sumo/network/grid4x4.net.xml"
