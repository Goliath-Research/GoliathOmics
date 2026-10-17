#!/usr/bin/env bash
# Release-time proof that a pod can install the worker venv offline:
#   pip install --no-index --find-links WHEELS -r LOCK
# is resolved (pip --dry-run, into a throwaway --target) for each target arch
# with only the bundled wheels. Any pin without a usable wheel fails here,
# so a release never ships an incomplete wheels/ directory.
#
# Usage: check_offline_wheels.sh LOCK WHEELS_DIR [--arch x86_64 ...]
#        [--python-version 3.12] [--python python3]

set -euo pipefail

die() { printf '[check-offline-wheels] ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '[check-offline-wheels] %s\n' "$*" >&2; }

[[ $# -ge 2 ]] || die "usage: $0 LOCK WHEELS_DIR [--arch A ...] [--python-version X.Y] [--python BIN]"
LOCK="$1" WHEELS="$2"; shift 2
ARCHES=() PYVER=3.12 PY="${PYTHON_BIN:-python3}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch) ARCHES+=("$2"); shift 2 ;;
    --python-version) PYVER="$2"; shift 2 ;;
    --python) PY="$2"; shift 2 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ "${#ARCHES[@]}" -gt 0 ]] || ARCHES=(x86_64)
[[ -f "$LOCK" ]] || die "missing lock $LOCK"
[[ -d "$WHEELS" ]] || die "missing wheels dir $WHEELS"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# Same cleanup install_release.sh does: only our wheels dir, never an index.
sed -E '/^[[:space:]]*(-f|--find-links|-i|--index-url|--extra-index-url|--trusted-host|--no-index)([[:space:]=]|$)/d' \
  "$LOCK" >"$TMP/lock.txt"
hash_args=()
grep -q -- '--hash=' "$TMP/lock.txt" && hash_args=(--require-hashes)
pyabi="cp${PYVER/./}"
rc=0
for arch in "${ARCHES[@]}"; do
  plat_args=(--platform any --platform "linux_${arch}")
  for plat in manylinux1 manylinux2010 manylinux2014 manylinux_2_17 manylinux_2_24 manylinux_2_27 \
              manylinux_2_28 manylinux_2_31 manylinux_2_34 manylinux_2_35; do
    [[ "$arch" != x86_64 && "$plat" =~ ^manylinux(1|2010)$ ]] && continue
    plat_args+=(--platform "${plat}_${arch}")
  done
  info "offline install check: linux $arch $pyabi, $(grep -cE '^[A-Za-z0-9]' "$TMP/lock.txt") pins, wheels from $WHEELS"
  if ! PIP_CONFIG_FILE=/dev/null "$PY" -m pip install -q --dry-run --ignore-installed --no-index \
      --find-links "$WHEELS" --target "$TMP/target-$arch" --only-binary=:all: \
      --implementation cp --python-version "$PYVER" --abi "$pyabi" --abi abi3 --abi none \
      "${plat_args[@]}" "${hash_args[@]}" -r "$TMP/lock.txt" >"$TMP/pip-$arch.log" 2>&1; then
    sed 's/^/  /' "$TMP/pip-$arch.log" >&2
    info "FAILED for $arch: a pod could not install the worker venv offline from this release"
    rc=1
  fi
done
[[ "$rc" == 0 ]] || die "offline wheel install check failed; every locked requirement needs a bundled wheel"
info "OK: the full requirement set installs offline for ${ARCHES[*]}"
