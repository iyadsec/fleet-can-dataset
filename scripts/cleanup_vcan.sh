#!/usr/bin/env bash
# Delete virtual CAN interfaces named vcan<number>. Leaves other interfaces and the
# vcan kernel module untouched.
# Usage: sudo scripts/cleanup_vcan.sh [N]   delete vcan0 .. vcan(N-1)
#        sudo scripts/cleanup_vcan.sh all   delete every vcan<number> interface
# Idempotent: missing interfaces are skipped.
set -euo pipefail

ARG="${1:-all}"
if (( EUID != 0 )); then
  echo "error: run with sudo (needs root for ip link delete)" >&2; exit 1
fi

if [[ "$ARG" == "all" ]]; then
  mapfile -t DEVS < <(ip -br link show type vcan 2>/dev/null | awk '{print $1}' | grep -E '^vcan[0-9]+$' || true)
elif [[ "$ARG" =~ ^[0-9]+$ ]]; then
  DEVS=(); for (( i = 0; i < ARG; i++ )); do DEVS+=("vcan$i"); done
else
  echo "error: argument must be a number or 'all'" >&2; exit 2
fi

for dev in "${DEVS[@]}"; do
  if ip link show "$dev" &>/dev/null; then
    ip link delete "$dev" && echo "$dev: deleted"
  else
    echo "$dev: not present"
  fi
done
ip -br link show type vcan || true
