#!/bin/bash
# Remove RAVN. Keeps /etc/ravn unless --purge is set, so a reinstall keeps the node id.
# Always removes the binary, units, helper, and staging files.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run uninstall.sh as root" >&2
  exit 1
fi

purge=0
if [[ "${1:-}" == "--purge" ]]; then
  purge=1
elif [[ -n "${1:-}" ]]; then
  echo "unknown argument: $1" >&2
  exit 1
fi

systemctl disable --now ravn.service ravn-update.path ravn-update.service 2>/dev/null || true
rm -f /etc/systemd/system/ravn.service \
  /etc/systemd/system/ravn-update.service \
  /etc/systemd/system/ravn-update.path \
  /etc/sudoers.d/ravn \
  /usr/local/bin/ravn \
  /usr/local/libexec/ravn-apply
rm -rf /var/lib/ravn
systemctl daemon-reload

if [[ "$purge" -eq 1 ]]; then
  rm -rf /etc/ravn
  if id ravn >/dev/null 2>&1; then
    userdel ravn
  fi
  echo "RAVN removed, including /etc/ravn."
else
  echo "RAVN removed. /etc/ravn was kept. Run with --purge to remove it."
fi
