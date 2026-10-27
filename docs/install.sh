#!/bin/bash
# Run the installer from the latest release. Existing node id, token, and certificate are kept.
set -euo pipefail
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
curl -fsSL "https://github.com/pixelwave/ravn/releases/latest/download/install.sh" -o "$tmp"
bash "$tmp" "$@"
