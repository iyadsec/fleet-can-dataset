#!/usr/bin/env bash
# Create and bring up N virtual CAN interfaces: vcan0 .. vcan(N-1).
# Usage: sudo scripts/setup_vcan.sh [N]        (default N=3)
# Idempotent: existing interfaces are left in place and only brought up.
# Not persistent: nothing is written to /etc; re-run after a reboot.
set -euo pipefail

N="${1:-3}"
TXQUEUELEN="${TXQUEUELEN:-1000}"   # kernel tx queue per interface (frames)

if ! [[ "$N" =~ ^[0-9]+$ ]] || (( N < 1 || N > 64 )); then
  echo "error: N must be an integer in 1..64 (got '$N')" >&2; exit 2
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
