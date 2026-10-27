#!/bin/bash
# Run the uninstaller from the latest release.
set -euo pipefail
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL "https://github.com/pixelwave/ravn/releases/latest/download/uninstall.sh" -o "$tmp"
bash "$tmp" "$@"
