#!/usr/bin/env bash
# Download (or restore from local parts) and VERIFY the fleetcan-sim-dataset-1.0 dataset.
#
#   scripts/download_fleetcan_sim_dataset_1_0.sh DESTINATION                     # from the GitHub release
#   scripts/download_fleetcan_sim_dataset_1_0.sh DESTINATION --from-dir PARTS    # from already downloaded parts
#   options: --keep-archive (keep the downloaded parts in DESTINATION/.fleetcan-download)
#
# Result: DESTINATION/fleetcan-sim-dataset-1.0/{README.md,SHA256SUMS,sessions.csv,vehicles.csv,attacks.csv,
#         campaigns.csv,campaign_members.csv,replay_links.csv.gz,CSV/,Parquet/}
#
# Two integrity levels, both mandatory, fail closed:
#   1 archive integrity: the downloaded <name>-archive-SHA256SUMS must be identical to the copy committed in this
#     repository, and every part must match it
#   2 dataset integrity: every extracted file must match the dataset SHA256SUMS (identical to the committed copy
#     results/datasets/fleetcan-sim-dataset-1.0/SHA256SUMS); 835 session logs in CSV/ and in Parquet/
# Requirements: bash, tar, zstd, sha256sum or shasum, python3; gh unless --from-dir. About 22 GB free disk
# (about 10 GB download + 10 GB dataset); the download is deleted after a successful restore.
set -euo pipefail

NAME=fleetcan-sim-dataset-1.0
TAG=dataset-fleetcan-sim-dataset-1.0
REPO_SLUG=iyadsec/fleet-can-dataset
SESSIONS=835
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF_ARCHIVE_SUMS="$HERE/results/dataset_release/$NAME/$NAME-archive-SHA256SUMS"
REF_DATASET_SUMS="$HERE/results/datasets/$NAME/SHA256SUMS"
REF_JSON="$HERE/results/datasets/$NAME/reference.json"

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

for t in tar zstd python3; do command -v "$t" >/dev/null || die "missing tool: $t"; done
if command -v sha256sum >/dev/null; then SHA256="sha256sum"; elif command -v shasum >/dev/null; then SHA256="shasum -a 256"
else die "missing tool: sha256sum or shasum"; fi
sha_of() { $SHA256 "$1" | cut -c1-64; }
[ -f "$REF_ARCHIVE_SUMS" ] || die "reference $REF_ARCHIVE_SUMS not found (run from a clone of $REPO_SLUG)"
SUMS_SHA=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['SHA256SUMS_sha256'])" "$REF_JSON")
[ "$(sha_of "$REF_DATASET_SUMS")" = "$SUMS_SHA" ] || die "committed SHA256SUMS does not match reference.json"

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
ROOT="$STAGE/$NAME"
[ -f "$ROOT/SHA256SUMS" ] || die "unexpected archive layout"

# ---- 4 dataset integrity --------------------------------------------------------------------------------------
cmp -s "$ROOT/SHA256SUMS" "$REF_DATASET_SUMS" || die "extracted SHA256SUMS differs from the committed copy"
(cd "$ROOT" && $SHA256 -c --quiet SHA256SUMS) || die "dataset files do not match SHA256SUMS"
N=$(wc -l < "$ROOT/SHA256SUMS" | tr -d ' ')
EXTRACTED=$(cd "$ROOT" && find . -type f | wc -l | tr -d ' ')
[ "$EXTRACTED" -eq $((N + 1)) ] || die "extracted $EXTRACTED files, expected $((N + 1)) (listed files + SHA256SUMS)"
S=$(find "$ROOT/CSV" -type f -name 'FCS10_*.csv.gz' | wc -l | tr -d ' ')
[ "$S" -eq "$SESSIONS" ] || die "CSV/ has $S session logs, expected $SESSIONS"
S=$(find "$ROOT/Parquet" -type f -name 'FCS10_*.parquet' | wc -l | tr -d ' ')
[ "$S" -eq "$SESSIONS" ] || die "Parquet/ has $S session logs, expected $SESSIONS"

mv "$ROOT" "$DEST/$NAME"
rmdir "$STAGE"
[ "$KEEP" -eq 1 ] || rm -rf "$DL"
echo "SUCCESS: $NAME restored to $DEST/$NAME ($N files verified against SHA256SUMS, $SESSIONS sessions)"
