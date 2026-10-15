#!/usr/bin/env bash
# Run one experiment: verify environment, then orchestrate SUMO -> CAN -> vCAN -> capture -> dataset -> validation.
# Usage: scripts/run_experiment.sh [config/experiment.yaml] [extra orchestrator args, e.g. --duration 10]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG="${1:-config/experiment.yaml}"; shift || true
N=$(.venv/bin/python -c "from src.config import load_experiment; print(len(load_experiment('$CONFIG').fleet))")
scripts/verify_environment.sh "$N"
# shellcheck disable=SC1091
source .venv/bin/activate
python -m src.orchestrator --config "$CONFIG" "$@"
