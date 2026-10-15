#!/usr/bin/env bash
# Verify that frames sent on each vcan interface are received on that interface,
# first with can-utils (cansend/candump), then with python-can.
# Usage: scripts/test_vcan.sh [N]   (default N=3; no root needed; run setup_vcan.sh first)
# Exit code 0 only if every check passes.
set -uo pipefail

N="${1:-3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
FAIL=0

for tool in cansend candump; do
  command -v "$tool" >/dev/null || { echo "FAIL: $tool not found (sudo apt-get install can-utils)"; exit 1; }
done

echo "== can-utils: cansend -> candump =="
for (( i = 0; i < N; i++ )); do
  dev="vcan$i"
  if ! ip link show "$dev" 2>/dev/null | grep -q "UP"; then
    echo "FAIL $dev: interface missing or down"; FAIL=1; continue
  fi
  frame="12$i#DEADBEEF0$i"
  expect="$dev 12$i [5] DE AD BE EF 0$i"
  out=$( (timeout 3 candump -n 1 "$dev" & sleep 0.3; cansend "$dev" "$frame"; wait) | tr -s ' ' | sed 's/^ //')
  if grep -qF "$expect" <<<"$out"; then
    echo "PASS $dev: sent $frame, received: $out"
  else
    echo "FAIL $dev: sent $frame, got: '${out}'"; FAIL=1
  fi
done

echo "== python-can: send -> recv =="
"$PY" - "$N" <<'PY' || FAIL=1
import sys, can
n = int(sys.argv[1]); ok = True
for i in range(n):
    ch = f"vcan{i}"
    with can.Bus(interface="socketcan", channel=ch, receive_own_messages=True) as bus:
        tx = can.Message(arbitration_id=0x300 + i, data=bytes([0x11, 0x22, i]), is_extended_id=False)
        bus.send(tx)
        rx = bus.recv(timeout=2.0)
    good = rx is not None and rx.arbitration_id == tx.arbitration_id and bytes(rx.data) == bytes(tx.data)
    ok &= good
    print(f"{'PASS' if good else 'FAIL'} {ch}: sent 0x{tx.arbitration_id:03X} {tx.data.hex()}, "
          f"received {'none' if rx is None else f'0x{rx.arbitration_id:03X} {bytes(rx.data).hex()} ts={rx.timestamp:.6f}'}")
sys.exit(0 if ok else 1)
PY

echo "== result =="
if (( FAIL == 0 )); then echo "ALL vCAN TESTS PASSED ($N interfaces)"; else echo "vCAN TESTS FAILED"; fi
exit "$FAIL"
