#!/usr/bin/env bash
# Run on the Mac clone (not on the VM): bring in the VM's commits, then publish them to GitHub.
# Commits are made on the VM (origin = fleetvm:fleet-can-dataset); GitHub credentials stay on the Mac.
# Usage: scripts/sync_github.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git fetch origin
git merge --ff-only origin/main          # refuses if the Mac copy has diverged (never rewrites history)
git push github main
git log --oneline -1
