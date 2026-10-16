#!/usr/bin/env bash
# Create and bring up N virtual CAN interfaces: vcan0 .. vcan(N-1).
# Usage: sudo scripts/setup_vcan.sh [N]        (default N=3)
# Idempotent: existing interfaces are left in place and only brought up.
# Not persistent: nothing is written to /etc; re-run after a reboot.
set -euo pipefail

N="${1:-3}"
TXQUEUELEN="${TXQUEUELEN:-1000}"   # kernel tx queue per interface (frames)

# Upper bound: an input guard against typos, not a kernel limit (Linux has no per-type cap on virtual
# interfaces at this scale; each vcan costs one netdev + its tx queue). Milestone 1 used 64; Milestone 8
# raised it to 128 for fleets up to 100 vehicles (measured: docs/m8_scalability_to_100.md). Override: MAX_VCAN.
MAX_VCAN="${MAX_VCAN:-128}"
if ! [[ "$N" =~ ^[0-9]+$ ]] || (( N < 1 || N > MAX_VCAN )); then
  echo "error: N must be an integer in 1..$MAX_VCAN (got '$N')" >&2; exit 2
fi
if (( EUID != 0 )); then
  echo "error: run with sudo (needs root for modprobe / ip link)" >&2; exit 1
fi

modprobe vcan
modprobe can_raw

for (( i = 0; i < N; i++ )); do
  dev="vcan$i"
  if ip link show "$dev" &>/dev/null; then
    echo "$dev: already exists"
  else
    ip link add dev "$dev" type vcan
    echo "$dev: created"
  fi
  ip link set dev "$dev" txqueuelen "$TXQUEUELEN"
  ip link set up "$dev"
done

echo "--- vcan interfaces ---"
ip -br link show type vcan
