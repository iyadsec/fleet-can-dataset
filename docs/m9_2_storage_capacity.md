# Milestone 9.2 — storage capacity check and safe expansion

**Verdict: STORAGE READY FOR M10.** **STORAGE GATE: PASS** — 67.86 GB free, against a requirement of ≥ 35 GB and
a preference of ≈ 40 GB.

Storage was expanded only; nothing was shrunk, formatted, recreated or moved. fleetcan-sim-1.1 and the frozen
matrix are unchanged. No research session was generated.

## Topology

`/dev/vda` (GPT) → `vda3` (LVM PV) → VG `ubuntu-vg` (1 PV, 1 LV) → LV `ubuntu-lv` → ext4 mounted on `/`.

The other partitions are `vda1` (vfat, `/boot/efi`) and `vda2` (ext4, `/boot`); both are unchanged.

## Before and after

| Item | Before | After |
|---|---|---|
| Virtual disk `vda` | 64 GiB (68,719,476,736 B) | 100 GiB (107,374,182,400 B) |
| `vda3` sectors (start 6,397,952) | 127,817,728 (65.44 GB) | 203,317,215 (104.10 GB) |
| PV `/dev/vda3` | 65,439,531,008 B, 15,602 extents of 4 MiB | 104,094,236,672 B, 24,818 extents |
| VG `ubuntu-vg` free | 2,681 extents (11.24 GB) | 0 |
| LV `ubuntu-lv` | 54,194,601,984 B (12,921 extents) | 104,094,236,672 B (24,818 extents) |
| ext4 size | 53,155,033,088 B (13,231,104 blocks) | 102,263,050,240 B (25,413,632 blocks) |
| ext4 used | 30.92 GB (29.90 GB after reboot) | 29.90 GB |
| ext4 free (user-available) | **19.73 GB** (20.75 GB after reboot) | **67.86 GB** |
| ext4 root-reserved | 608,834 blocks | 1,095,836 blocks |
| Filesystem state | clean | clean; no ext4, I/O or device-mapper errors in the kernel log |

The disk had no unallocated space outside `vda3`, and the 11.24 GB free in the VG was not enough to reach the
35 GB target. This was Case C: the UTM virtual disk had to be enlarged first, which you did from the host
(64 GiB → 100 GiB, followed by a full stop and start of the VM, booted 2026-10-17 08:36:15). After that, the new
space sat after `vda3`, so the growth inside the VM followed Case B.

## Actions

**On the host (by you).** You backed up the `.utm` bundle, stopped the VM "Ubuntu 22.04 2", resized drive
`3B047741…` to 100 GiB in UTM, and started the VM again. The qcow2 header now reports 0x1900000000 bytes (100 GiB).

**In the VM.** I ran the guarded script `~/m92_expand.sh` through your terminal; you entered the sudo password
interactively and it was never stored. The script checks these preconditions and stops if any fail:

* disk size;
* `vda3` start and size, and that `vda3` is the last partition;
* `vda3` is an LVM PV of `ubuntu-vg`;
* the VG has exactly one PV and one LV;
* the root filesystem is ext4 on the LV.

Commands executed, in order, with verification after each stage:

```
sudo sgdisk -v /dev/vda                 # read-only: backup GPT header at the old end of the disk (expected after the resize)
sudo growpart --dry-run /dev/vda 3      # CHANGE: start=6397952 size 127817728 -> 203317215
sudo growpart /dev/vda 3                # CHANGED (start unchanged; backup GPT header relocated to the new end)
sudo partx -u /dev/vda
sudo sgdisk -v /dev/vda                 # "No problems found"
sudo fdisk -l /dev/vda
sudo pvresize /dev/vda3                 # 1 physical volume resized
sudo pvs -o +pv_pe_count,pv_pe_alloc_count --units b /dev/vda3   # VG free extents 2681 -> 11897
sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv           # 12921 -> 24818 extents; resize2fs online grow
sudo lvs -a -o +devices --units b; df -B1 /; df -hT /; findmnt /; lsblk; sudo pvs; sudo vgs
sudo tune2fs -l /dev/mapper/ubuntu--vg-ubuntu--lv                 # state clean
sudo dmesg | grep -iE "ext4|i/o error|..."                        # no errors
```

Two earlier runs stopped at precondition checks before making any change. The first check wrongly read the child
filesystem type of `vda3`, and an over-strict check on the VG output format also had to be fixed.

`sgdisk` reports a caution that partition 3 does not *end* on a 2048-sector boundary. This is harmless: the start
is aligned, and `growpart` uses the last usable sector.

## Verification

* **Filesystem read/write test.** I wrote a 1 GiB random file in a temporary directory under `~` (not in `data/`),
  copied it, and re-read both files. All three SHA-256 values were identical (`a977149e…`). The test files were
  removed afterwards.
* **Root filesystem.** Mounted `rw,relatime` from `/dev/mapper/ubuntu--vg-ubuntu--lv`. No unexpected mounts.

**Baseline fleetcan-sim-1.1** (`sha256sum -c` plus the established checks):

| | Before the change | After the change |
|---|---|---|
| Files | 20,805 / 20,805 OK | 20,805 / 20,805 OK |
| SHA256SUMS | `bc523937…18c8` | identical |
| FREEZE.json | `df58000a…a160` | identical |
| manifest | `aebfd8e0…e979` | identical |
| Sessions | 610 | 610 |
| Writable baseline files | 0 | 0 |

PRE-STORAGE-CHANGE BASELINE: PASS. POST-STORAGE-CHANGE BASELINE: PASS.

**Frozen matrix.** `configs/fleetcan_sim_scale_1_0.yaml` has SHA-256
`9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735`, with 167 configurations, 835 sessions,
`matrix_status: frozen` and `approved_for_generation: false`. `m91_freeze.py --check` confirms the rebuild is
byte-identical. There are no `FCS10_*` sessions in `data/`.

**Git.** The working tree was clean at `50efaa0` before this documentation-only commit. The storage helper scripts
and logs (`~/m92_*`) live outside the repository.

## Capacity for M10

With 67.86 GB free and an expected `fleetcan-sim-scale-1.0` footprint of 23.6 GB (≤ 30.1 GB with every M9.1
margin included), about 37–44 GB will remain free after generation.

**Remaining M10 prerequisite (not storage).** The reboot removed the vCAN interfaces: 0 are present, and N = 100
needs 100. Recreate them with `scripts/setup_vcan.sh`, which needs sudo, before generation.
