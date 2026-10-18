#!/usr/bin/env bash
# Download (or restore from local parts) and VERIFY the frozen fleetcan-sim-scale-1.0 dataset.
#
#   scripts/download_fleetcan_sim_scale_1_0.sh DESTINATION                     # from the GitHub release
#   scripts/download_fleetcan_sim_scale_1_0.sh DESTINATION --from-dir PARTS    # from already downloaded parts
#   options: --keep-archive (keep the downloaded parts in DESTINATION/.fleetcan-download)
#
# Result: DESTINATION/fleetcan-sim-scale-1.0/data/{raw,ground_truth,metadata,validation,generated_sumo,
#         m10_configs}/FCS10_*  and  DESTINATION/fleetcan-sim-scale-1.0/data/final/fleetcan-sim-scale-1.0/
#
# Two integrity levels, both mandatory, fail closed:
#   1 archive integrity: the downloaded <name>-archive-SHA256SUMS must be identical to the copy committed in this
#     repository, and every part must match it
#   2 scientific dataset integrity: every extracted file must match the authoritative dataset SHA256SUMS
#     (digest 5d5418b1...de70, identical to results/datasets/fleetcan-sim-scale-1.0/SHA256SUMS); 835 sessions
# Requirements: bash, tar, zstd, sha256sum or shasum; gh (authenticated; the repository is private) unless --from-dir.
set -euo pipefail

NAME=fleetcan-sim-scale-1.0
TAG=dataset-fleetcan-sim-scale-1.0
REPO_SLUG=iyadsec/fleet-can-dataset
SUMS_SHA=5d5418b1ca9a4107818a83e03a5dea4a4ea83d1c36354fa668fcc5cb0242de70
SESSIONS=835
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF_ARCHIVE_SUMS="$HERE/results/dataset_release/$NAME/$NAME-archive-SHA256SUMS"
REF_DATASET_SUMS="$HERE/results/datasets/$NAME/SHA256SUMS"

die() { echo "FAIL: $*" >&2; exit 1; }
usage() { sed -n '2,8p' "${BASH_SOURCE[0]}"; exit 2; }

DEST="" FROM="" KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from-dir) FROM="${2:?}"; shift 2 ;;
    --keep-archive) KEEP=1; shift ;;
    -h|--help) usage ;;
    *) [ -z "$DEST" ] || usage; DEST="$1"; shift ;;
  esac
done
[ -n "$DEST" ] || usage

# ---- tools ----------------------------------------------------------------------------------------------------
for t in tar zstd; do command -v "$t" >/dev/null || die "missing tool: $t"; done
if command -v sha256sum >/dev/null; then SHA256="sha256sum"; elif command -v shasum >/dev/null; then SHA256="shasum -a 256"
else die "missing tool: sha256sum or shasum"; fi
sha_of() { $SHA256 "$1" | cut -c1-64; }
[ -f "$REF_ARCHIVE_SUMS" ] || die "reference $REF_ARCHIVE_SUMS not found (run from a clone of $REPO_SLUG)"
[ "$(sha_of "$REF_DATASET_SUMS")" = "$SUMS_SHA" ] || die "committed dataset SHA256SUMS does not have the frozen digest"

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
[ ! -e "$DEST/$NAME" ] || die "$DEST/$NAME already exists; choose an empty destination"
DL="$DEST/.fleetcan-download"

# ---- 1 obtain parts -------------------------------------------------------------------------------------------
if [ -n "$FROM" ]; then
  DL="$(cd "$FROM" && pwd)"; KEEP=1
  echo "using local parts in $DL"
else
  command -v gh >/dev/null || die "missing tool: gh (GitHub CLI), or use --from-dir"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login); the repository is private"
  mkdir -p "$DL"
  echo "downloading release $TAG from $REPO_SLUG into $DL"
  gh release download "$TAG" -R "$REPO_SLUG" -D "$DL" --pattern "$NAME*" --skip-existing
fi

# ---- 2 archive integrity --------------------------------------------------------------------------------------
cmp -s "$DL/$NAME-archive-SHA256SUMS" "$REF_ARCHIVE_SUMS" || die "archive SHA256SUMS differs from the committed reference"
PARTS=()
while read -r h f; do
  [ -f "$DL/$f" ] || die "missing part $f"
  [ "$(sha_of "$DL/$f")" = "$h" ] || die "SHA-256 mismatch: $f"
  PARTS+=("$DL/$f")
done < "$REF_ARCHIVE_SUMS"
echo "archive integrity: ${#PARTS[@]} parts OK"

# ---- 3 reconstruct and extract (stream; no concatenated copy on disk) ------------------------------------------
STAGE="$DEST/.$NAME.extracting"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cat "${PARTS[@]}" | zstd -dc -q | tar -xf - -C "$STAGE"
[ -d "$STAGE/$NAME/data/final/$NAME" ] || die "unexpected archive layout"

# ---- 4 scientific dataset integrity ---------------------------------------------------------------------------
ROOT="$STAGE/$NAME/data"
[ "$(sha_of "$ROOT/final/$NAME/SHA256SUMS")" = "$SUMS_SHA" ] || die "extracted SHA256SUMS digest differs"
cmp -s "$ROOT/final/$NAME/SHA256SUMS" "$REF_DATASET_SUMS" || die "extracted SHA256SUMS differs from the committed copy"
(cd "$ROOT" && $SHA256 -c --quiet "final/$NAME/SHA256SUMS") || die "dataset files do not match SHA256SUMS"
N=$(wc -l < "$ROOT/final/$NAME/SHA256SUMS" | tr -d ' ')
EXTRACTED=$(cd "$ROOT" && find . -type f | wc -l | tr -d ' ')
[ "$EXTRACTED" -eq $((N + 2)) ] || die "extracted $EXTRACTED files, expected $((N + 2)) (listed files + SHA256SUMS + FREEZE.json)"
S=$(find "$ROOT/metadata" -mindepth 1 -maxdepth 1 -type d -name 'FCS10_*' | wc -l | tr -d ' ')
[ "$S" -eq "$SESSIONS" ] || die "found $S sessions, expected $SESSIONS"

mv "$STAGE/$NAME" "$DEST/$NAME"
rmdir "$STAGE"
[ "$KEEP" -eq 1 ] || rm -rf "$DL"
echo "SUCCESS: $NAME restored to $DEST/$NAME ($N files verified against SHA256SUMS $SUMS_SHA, $S sessions)"
