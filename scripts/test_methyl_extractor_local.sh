#!/bin/bash
# Tests for scripts/methyl_extractor_local.sh (pod-local MethylExtractor install).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/pkg/bin" "$tmp/pkg/lib/hdf5_zstd_plugin" "$tmp/work/goliath/releases/1.0.0"
printf '#!/bin/sh\necho MethylExtractor\n' >"$tmp/pkg/bin/MethylExtractor"
# Shared volumes drop exec bits: ship the binary without them.
chmod 0644 "$tmp/pkg/bin/MethylExtractor"
printf 'plugin\n' >"$tmp/pkg/lib/hdf5_zstd_plugin/libH5Zzstd.so"
tarball="$tmp/work/goliath/releases/1.0.0/methyl-extractor-linux-amd64.tar.gz"
tar -czf "$tarball" -C "$tmp/pkg" bin lib
sha="$(sha256sum "$tarball" | awk '{print $1}')"

export GOLIATH_WORK_MOUNT="$tmp/work"
export GOLIATH_LOCAL_ROOT="$tmp/local"
unset METHYL_EXTRACTOR_HOME
# shellcheck source=methyl_extractor_local.sh
source "$ROOT/scripts/methyl_extractor_local.sh"

install_methyl_extractor_local "$tarball" amd64 "$sha" 2>/dev/null || fail "install failed"
[[ "$METHYL_EXTRACTOR_HOME" == "$tmp/local/methyl-extractor-amd64" ]] || fail "wrong home $METHYL_EXTRACTOR_HOME"
[[ -x "$METHYL_EXTRACTOR_BIN" ]] || fail "binary not executable"
[[ "$HDF5_PLUGIN_PATH" == "$METHYL_EXTRACTOR_HOME/lib/hdf5_zstd_plugin" ]] || fail "wrong HDF5_PLUGIN_PATH"
[[ -f "$HDF5_PLUGIN_PATH/libH5Zzstd.so" ]] || fail "plugin missing"
[[ ! -e "$tmp/work/goliath/methyl-extractor-amd64" ]] || fail "extracted onto the work mount"

# Idempotent: a second run must not re-extract.
touch "$METHYL_EXTRACTOR_HOME/marker"
out="$(install_methyl_extractor_local "$tarball" amd64 "$sha" 2>&1)" || fail "second install failed"
[[ "$out" == *"already installed"* ]] || fail "second install should skip: $out"
[[ -f "$METHYL_EXTRACTOR_HOME/marker" ]] || fail "second install re-extracted"

# A new tarball replaces the local copy.
printf 'v2\n' >"$tmp/pkg/VERSION.txt"
tar -czf "$tarball" -C "$tmp/pkg" bin lib VERSION.txt
install_methyl_extractor_local "$tarball" amd64 2>/dev/null || fail "upgrade failed"
[[ -f "$METHYL_EXTRACTOR_HOME/VERSION.txt" && ! -f "$METHYL_EXTRACTOR_HOME/marker" ]] || fail "upgrade did not replace"

# Checksum mismatch fails.
if install_methyl_extractor_local "$tarball" amd64 deadbeef 2>/dev/null; then
  fail "checksum mismatch should fail"
fi

# A local root under the work mount falls back to /tmp.
home="$(unset METHYL_EXTRACTOR_HOME; GOLIATH_LOCAL_ROOT="$tmp/work/opt" methyl_extractor_local_home amd64 2>/dev/null)"
[[ "$home" == "/tmp/goliath/methyl-extractor-amd64" ]] || fail "work-mount root should fall back to /tmp, got $home"
home="$(METHYL_EXTRACTOR_HOME="$tmp/work/me" methyl_extractor_local_home amd64 2>/dev/null)"
[[ "$home" == "/tmp/goliath/methyl-extractor-amd64" ]] || fail "work-mount home should fall back to /tmp, got $home"

# CLI entry point.
home="$(env -u METHYL_EXTRACTOR_HOME GOLIATH_LOCAL_ROOT="$tmp/cli" bash "$ROOT/scripts/methyl_extractor_local.sh" --tarball "$tarball" --print-home 2>/dev/null)"
[[ -x "$home/bin/MethylExtractor" && "$home" == "$tmp/cli/methyl-extractor-amd64" ]] || fail "CLI install failed"

echo "PASS test_methyl_extractor_local"
