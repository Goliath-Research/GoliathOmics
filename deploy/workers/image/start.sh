#!/usr/bin/env bash
# Keepalive for goliath-worker images that are not runpod/pytorch.
set -euo pipefail

if [[ "${1:-}" == "--version-check" ]]; then
  command -v pbrun >/dev/null
  pbrun version
  command -v python3.12 >/dev/null
  python3.12 --version
  exit 0
fi

mkdir -p /var/run/sshd /run/sshd
# Runpod passes the pod's SSH public keys (newline-separated) in PUBLIC_KEY;
# runpod/pytorch's /start.sh installs them, this image must do it itself.
if [[ -n "${PUBLIC_KEY:-}" ]]; then
  install -d -m 700 /root/.ssh
  touch /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  while IFS= read -r key; do
    key="${key%$'\r'}"
    [[ -n "$key" ]] || continue
    grep -qxF -- "$key" /root/.ssh/authorized_keys || printf '%s\n' "$key" >>/root/.ssh/authorized_keys
  done <<<"$PUBLIC_KEY"
fi
if ! pgrep -x sshd >/dev/null 2>&1; then
  /usr/sbin/sshd || true
fi

# GoliathAlign host runtime PATH is set by start_shared_worker after local install.
if [[ -n "${GOLIATH_ALIGN_HOME:-}" && -d "${GOLIATH_ALIGN_HOME}/bin" ]]; then
  export PATH="${GOLIATH_ALIGN_HOME}/bin:${PATH}"
fi

printf '[goliath-worker] keepalive: sshd on :22 (no Jupyter in this image)\n' >&2
exec sleep infinity
