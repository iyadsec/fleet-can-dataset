#!/bin/bash
# M9.2 storage EXPANSION ONLY (no shrink, no format, no partition recreation). Stops at the first unexpected result.
# Layout verified beforehand: /dev/vda (GPT) -> vda3 (last partition, LVM PV of ubuntu-vg) -> ubuntu-vg/ubuntu-lv -> ext4 on /
set -u
# output is logged by the caller: ./m92_expand.sh 2>&1 | tee -a ~/m92_expand.log
die() { echo "STOP: $*"; exit 1; }
run() { echo "+ $*"; "$@" || die "command failed: $*"; }
echo "== M9.2 expansion $(date -u +%FT%TZ)"
sudo -v || die "sudo authentication"
# --- pre-conditions ---------------------------------------------------------------------------------------------
[ "$(cat /sys/block/vda/size)" = "209715200" ] || die "vda is not 100 GiB"
[ "$(cat /sys/block/vda/vda3/start)" = "6397952" ] || die "vda3 start changed"
[ "$(cat /sys/block/vda/vda3/size)" = "127817728" ] || die "vda3 size not the inspected value"
[ ! -e /sys/block/vda/vda4 ] || die "unexpected partition vda4"
[ "$(lsblk -dno FSTYPE /dev/vda3)" = "LVM2_member" ] || die "vda3 is not an LVM PV"
[ "$(findmnt -no SOURCE /)" = "/dev/mapper/ubuntu--vg-ubuntu--lv" ] || die "root source changed"
[ "$(findmnt -no FSTYPE /)" = "ext4" ] || die "root fs is not ext4"
[ "$(sudo pvs --noheadings -o vg_name /dev/vda3 | tr -d " ")" = "ubuntu-vg" ] || die "vda3 not in ubuntu-vg"
[ "$(sudo vgs --noheadings -o pv_count,lv_count ubuntu-vg | awk '{print $1","$2}')" = "1,1" ] || die "VG not single-PV single-LV"
echo "pre-conditions OK"
run sudo sgdisk -v /dev/vda
# --- 1 partition -----------------------------------------------------------------------------------------------
run sudo growpart --dry-run /dev/vda 3
run sudo growpart /dev/vda 3
run sudo partx -u /dev/vda
NEW=$(cat /sys/block/vda/vda3/size)
[ "$(cat /sys/block/vda/vda3/start)" = "6397952" ] || die "vda3 start moved"
[ "$NEW" -gt 127817728 ] || die "vda3 did not grow"
echo "vda3 sectors: 127817728 -> $NEW"
run sudo sgdisk -v /dev/vda
run sudo fdisk -l /dev/vda
# --- 2 physical volume -----------------------------------------------------------------------------------------
run sudo pvresize /dev/vda3
run sudo pvs -o +pv_pe_count,pv_pe_alloc_count --units b /dev/vda3
FREE=$(sudo vgs --noheadings -o vg_free_count ubuntu-vg | tr -d " ")
[ "$FREE" -gt 2681 ] || die "VG free extents did not increase ($FREE)"
echo "VG free extents: 2681 -> $FREE"
# --- 3 logical volume + online ext4 grow -----------------------------------------------------------------------
run sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
run sudo lvs -a -o +devices --units b
[ "$(sudo vgs --noheadings -o vg_free_count ubuntu-vg | tr -d " ")" = "0" ] || die "VG still has free extents"
echo "== post"
run df -B1 /
run df -hT /
run findmnt /
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS | grep -v loop
run sudo pvs
run sudo vgs
echo "== tune2fs state"; sudo tune2fs -l /dev/mapper/ubuntu--vg-ubuntu--lv | grep -E "state|Block count|Reserved block count|Free blocks"
echo "== kernel errors since boot"; sudo dmesg | grep -iE "ext4|i/o error|blk_update|device-mapper|EXT4-fs" | grep -iE "error|warn|fail|corrupt" | tail -20; echo "(end)"
echo "M9.2 EXPANSION COMPLETED"
