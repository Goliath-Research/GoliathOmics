#!/bin/bash
# Symlink goliath-* console scripts to legacy methyl-* names (or the reverse) for one release.
# Sourced by install_gateway_venv.sh and install scripts after the venv is populated.
set -euo pipefail

link_gateway_entry_compat() {
  local venv="$1"
  local bindir="$venv/bin"
  [[ -d "$bindir" ]] || return 0

  if [[ -x "$bindir/methyl-gateway" && ! -e "$bindir/goliath-gateway" ]]; then
    ln -sf methyl-gateway "$bindir/goliath-gateway"
    echo "[WARN] linked goliath-gateway -> methyl-gateway (legacy wheel; prefer goliath-gateway next release)" >&2
  elif [[ -x "$bindir/goliath-gateway" && ! -e "$bindir/methyl-gateway" ]]; then
    ln -sf goliath-gateway "$bindir/methyl-gateway"
  fi

  if [[ -x "$bindir/methyl-reclaim-leases" && ! -e "$bindir/goliath-reclaim-leases" ]]; then
    ln -sf methyl-reclaim-leases "$bindir/goliath-reclaim-leases"
    echo "[WARN] linked goliath-reclaim-leases -> methyl-reclaim-leases (legacy wheel)" >&2
  elif [[ -x "$bindir/goliath-reclaim-leases" && ! -e "$bindir/methyl-reclaim-leases" ]]; then
    ln -sf goliath-reclaim-leases "$bindir/methyl-reclaim-leases"
  fi
}

link_worker_entry_compat() {
  local venv="$1"
  local bindir="$venv/bin"
  [[ -d "$bindir" ]] || return 0

  if [[ -x "$bindir/methyl-worker" && ! -e "$bindir/goliath-worker" ]]; then
    ln -sf methyl-worker "$bindir/goliath-worker"
    echo "[WARN] linked goliath-worker -> methyl-worker (legacy wheel)" >&2
  elif [[ -x "$bindir/goliath-worker" && ! -e "$bindir/methyl-worker" ]]; then
    ln -sf goliath-worker "$bindir/methyl-worker"
  fi
}

install_systemd_unit_alias() {
  local canonical="$1"
  local legacy="$2"
  local etc="/etc/systemd/system"
  [[ -f "$etc/$canonical" ]] || return 1
  ln -sfn "$canonical" "$etc/$legacy"
}
