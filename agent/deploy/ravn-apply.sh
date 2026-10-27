#!/bin/bash
# Installs one staged binary. This is the only root write the update path performs.
# It refuses symlinks and files that the ravn user did not create, then deletes staging files.
set -euo pipefail

staged=/var/lib/ravn/staged
if [[ ! -e "$staged" ]]; then
  exit 0
fi
if [[ -L "$staged" || ! -f "$staged" ]]; then
  echo "ravn-apply: refusing non-regular staged file" >&2
  rm -f "$staged"
  exit 1
fi
owner=$(stat -c '%U' "$staged")
if [[ "$owner" != "ravn" ]]; then
  echo "ravn-apply: refusing staged file owned by ${owner}" >&2
  rm -f "$staged"
  exit 1
fi

install -m 0755 "$staged" /usr/local/bin/ravn
rm -f "$staged" /var/lib/ravn/*.partial /var/lib/ravn/*.download
systemctl restart ravn.service
