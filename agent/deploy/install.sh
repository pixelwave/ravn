#!/bin/bash
# Install or upgrade RAVN. Existing node id, token, and certificate are kept.
# Usage:
#   ./install.sh --binary ./ravn
#   curl -fsSL https://pixelwave.github.io/ravn/install.sh | bash
set -euo pipefail

REPO="${RAVN_REPO:-pixelwave/ravn}"
BINARY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary)
      BINARY="$2"
      shift 2
      ;;
    --repo)
      REPO="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run install.sh as root" >&2
  exit 1
fi

tmp=$(mktemp -d)
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

arch=$(uname -m)
case "$arch" in
  x86_64) asset=ravn_linux_amd64 ;;
  aarch64) asset=ravn_linux_arm64 ;;
  armv7l) asset=ravn_linux_armv7 ;;
  *)
    echo "unsupported architecture: $arch" >&2
    exit 1
    ;;
esac

if [[ -n "$BINARY" ]]; then
  install -m 0755 "$BINARY" /usr/local/bin/ravn
else
  base="https://github.com/${REPO}/releases/latest/download"
  curl -fsSL "$base/$asset" -o "$tmp/$asset"
  curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS"
  (
    cd "$tmp"
    grep -F " $asset" SHA256SUMS | sha256sum -c -
  )
  install -m 0755 "$tmp/$asset" /usr/local/bin/ravn
  rm -f "$tmp/$asset" "$tmp/SHA256SUMS"
fi

if ! id ravn >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/ravn --create-home --shell /usr/sbin/nologin ravn
fi
install -d -o ravn -g ravn -m 0750 /etc/ravn /var/lib/ravn
install -d -m 0755 /usr/local/libexec

script_dir=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fi
if [[ -n "$script_dir" && -f "$script_dir/ravn-apply.sh" ]]; then
  install -m 0755 "$script_dir/ravn-apply.sh" /usr/local/libexec/ravn-apply
  install -m 0644 "$script_dir/ravn.service" /etc/systemd/system/ravn.service
  install -m 0644 "$script_dir/ravn-update.service" /etc/systemd/system/ravn-update.service
  install -m 0644 "$script_dir/ravn-update.path" /etc/systemd/system/ravn-update.path
else
  for name in ravn-apply ravn.service ravn-update.service ravn-update.path; do
    curl -fsSL "https://github.com/${REPO}/releases/latest/download/$name" -o "$tmp/$name"
  done
  install -m 0755 "$tmp/ravn-apply" /usr/local/libexec/ravn-apply
  install -m 0644 "$tmp/ravn.service" /etc/systemd/system/ravn.service
  install -m 0644 "$tmp/ravn-update.service" /etc/systemd/system/ravn-update.service
  install -m 0644 "$tmp/ravn-update.path" /etc/systemd/system/ravn-update.path
  rm -f "$tmp/ravn-apply" "$tmp/ravn.service" "$tmp/ravn-update.service" "$tmp/ravn-update.path"
fi

# Remove a sudoers rule from older installs. The update path no longer uses sudo.
rm -f /etc/sudoers.d/ravn

systemctl daemon-reload
systemctl enable --now ravn-update.path
systemctl enable --now ravn.service

for _ in $(seq 1 50); do
  if [[ -s /etc/ravn/token && -s /etc/ravn/tls.crt ]]; then
    break
  fi
  sleep 0.1
done
if [[ ! -s /etc/ravn/token ]]; then
  echo "ravn did not create a token. check: journalctl -u ravn -n 50" >&2
  exit 1
fi
/usr/local/bin/ravn installed
