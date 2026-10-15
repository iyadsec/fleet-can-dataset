#!/usr/bin/env bash
# Check every prerequisite for running an experiment. No root needed; changes nothing.
# Usage: scripts/verify_environment.sh [N_VCAN]   (default 3)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N="${1:-3}"
FAIL=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAIL=1; }

. /etc/os-release 2>/dev/null && pass "OS: $PRETTY_NAME ($(uname -m), kernel $(uname -r), virt=$(systemd-detect-virt 2>/dev/null || echo unknown))"
[[ -x "$ROOT/.venv/bin/python" ]] && pass "venv: $("$ROOT/.venv/bin/python" --version)" || fail "venv missing: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate" 2>/dev/null || true

if out=$(sumo --version 2>/dev/null | head -1); then pass "SUMO: $out"; else fail "sumo binary not found"; fi
python - <<'PY' && pass "Python imports: traci sumolib can cantools pandas numpy yaml pytest" || fail "Python imports"
import traci, sumolib, can, cantools, pandas, numpy, yaml, pytest
PY
command -v cansend >/dev/null && command -v candump >/dev/null && pass "can-utils installed" || fail "can-utils missing (sudo apt-get install can-utils)"
modinfo vcan >/dev/null 2>&1 && pass "vcan kernel module available" || fail "vcan module not available for kernel $(uname -r)"
lsmod | grep -q '^vcan' && pass "vcan module loaded" || fail "vcan not loaded (sudo scripts/setup_vcan.sh $N)"
for (( i = 0; i < N; i++ )); do
  ip link show "vcan$i" 2>/dev/null | grep -q UP && pass "vcan$i up" || fail "vcan$i missing/down (sudo scripts/setup_vcan.sh $N)"
done
[[ -f "$ROOT/sumo/network/grid4x4.net.xml" ]] && pass "SUMO network present" || fail "run scripts/build_sumo_network.sh"
echo "free disk: $(df -h "$ROOT" | awk 'NR==2{print $4}')"
(( FAIL == 0 )) && echo "ENVIRONMENT OK" || echo "ENVIRONMENT NOT READY"
exit "$FAIL"
