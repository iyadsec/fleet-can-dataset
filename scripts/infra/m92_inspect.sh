#!/bin/sh
# M9.2 READ-ONLY storage inspection (no changes). Asks for the sudo password interactively.
sudo -v || exit 1
{
echo "== date"; date -u +%FT%TZ
for c in "pvs" "pvs -o +pv_pe_count,pv_pe_alloc_count,pe_start --units b" "vgs" "vgs -o +vg_extent_size,vg_extent_count,vg_free_count --units b" "lvs -a -o +devices" "lvs -o +lv_size --units b" "fdisk -l /dev/vda" "tune2fs -l /dev/mapper/ubuntu--vg-ubuntu--lv"; do
  echo "== sudo $c"; sudo $c 2>&1
done
echo "== kernel log errors (ext4/I/O/LVM)"; sudo dmesg 2>&1 | grep -iE "ext4|i/o error|blk_update|device-mapper|lvm" | grep -iE "error|warn|fail|corrupt" | tail -20; echo "(end)"
} > /tmp/m92_inspect.txt 2>&1
echo "inspection written to /tmp/m92_inspect.txt"
