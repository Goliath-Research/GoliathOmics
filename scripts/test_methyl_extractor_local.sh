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

# A binary that needs a bundled lib (like libhts.so.3) runs only when the
# installer puts <home>/lib on LD_LIBRARY_PATH; otherwise exit 127.
mkdir -p "$tmp/hts/bin" "$tmp/hts/lib"
cat >"$tmp/hts/bin/MethylExtractor" <<'SH'
#!/bin/bash
here="$(cd "$(dirname "$0")/.." && pwd)"
case ":${LD_LIBRARY_PATH:-}:" in
  *":$here/lib:"*) [[ -f "$here/lib/libhts.so.3" ]] && exit 0 ;;
esac
echo "libhts.so.3: cannot open shared object file" >&2
exit 127
SH
printf 'hts\n' >"$tmp/hts/lib/libhts.so.3.1"
ln -s libhts.so.3.1 "$tmp/hts/lib/libhts.so.3"
hts_tb="$tmp/work/goliath/releases/1.0.0/methyl-extractor-linux-aarch64.tar.gz"
tar -czf "$hts_tb" -C "$tmp/hts" bin lib
(
  unset LD_LIBRARY_PATH METHYL_EXTRACTOR_HOME
  install_methyl_extractor_local "$hts_tb" aarch64 2>/dev/null || fail "bundled-lib run check failed"
  [[ ":$LD_LIBRARY_PATH:" == *":$METHYL_EXTRACTOR_HOME/lib:"* ]] || fail "LD_LIBRARY_PATH missing local lib: ${LD_LIBRARY_PATH:-}"
  grep -q 'LD_LIBRARY_PATH="'"$METHYL_EXTRACTOR_HOME"'/lib' "$METHYL_EXTRACTOR_HOME/env.sh" || fail "env.sh lacks LD_LIBRARY_PATH"
  # shellcheck source=/dev/null
  ( unset LD_LIBRARY_PATH; . "$METHYL_EXTRACTOR_HOME/env.sh"; "$METHYL_EXTRACTOR_BIN" ) || fail "env.sh does not make the binary runnable"
)
# Without the lib in the tarball the run check reports exit 127.
rm -f "$tmp/hts/lib/libhts.so.3" "$tmp/hts/lib/libhts.so.3.1"
tar -czf "$hts_tb" -C "$tmp/hts" bin lib
if (unset METHYL_EXTRACTOR_HOME; install_methyl_extractor_local "$hts_tb" aarch64 2>/dev/null); then
  fail "missing libhts should fail the run check"
fi

# bundle_methyl_extractor_libs.sh: copies libhts (symlink resolved) and
# libdeflate/libhtscodecs from --lib-dir, writes env.sh, and is idempotent.
mkdir -p "$tmp/sys" "$tmp/bare/bin"
printf 'real hts\n' >"$tmp/sys/libhts.so.3.1.13"
ln -s libhts.so.3.1.13 "$tmp/sys/libhts.so.3"
printf 'deflate\n' >"$tmp/sys/libdeflate.so.0"
printf 'codecs\n' >"$tmp/sys/libhtscodecs.so.2.1.1"
ln -s libhtscodecs.so.2.1.1 "$tmp/sys/libhtscodecs.so.2"
cp "$tmp/hts/bin/MethylExtractor" "$tmp/bare/bin/"
bare_tb="$tmp/methyl-extractor-linux-amd64.tar.gz"
tar -czf "$bare_tb" -C "$tmp/bare" bin
bash "$ROOT/scripts/bundle_methyl_extractor_libs.sh" --tarball "$bare_tb" --lib-dir "$tmp/sys" 2>/dev/null \
  || fail "bundle_methyl_extractor_libs.sh failed"
listing="$(tar -tzvf "$bare_tb")"
[[ "$listing" == *"lib/libhts.so.3.1.13"* ]] || fail "real libhts file not bundled: $listing"
[[ "$listing" == *"lib/libhts.so.3 -> libhts.so.3.1.13"* ]] || fail "libhts soname link not bundled: $listing"
[[ "$listing" == *"lib/libdeflate.so.0"* ]] || fail "libdeflate not bundled"
[[ "$listing" == *"lib/libhtscodecs.so.2 -> libhtscodecs.so.2.1.1"* ]] || fail "libhtscodecs not bundled"
[[ "$listing" == *"env.sh"* ]] || fail "env.sh not written"
sum1="$(sha256sum "$bare_tb")"
bash "$ROOT/scripts/bundle_methyl_extractor_libs.sh" --tarball "$bare_tb" --lib-dir "$tmp/sys" 2>/dev/null || fail "re-bundle failed"
[[ "$(sha256sum "$bare_tb")" == "$sum1" ]] || fail "re-bundle should be a no-op"
(
  unset LD_LIBRARY_PATH METHYL_EXTRACTOR_HOME
  install_methyl_extractor_local "$bare_tb" amd64 2>/dev/null || fail "bundled tarball does not install/run"
)
if bash "$ROOT/scripts/bundle_methyl_extractor_libs.sh" --tarball "$bare_tb" --lib-dir "$tmp/nolibs" --force 2>/dev/null; then
  fail "bundle should fail when libhts cannot be found"
fi

echo "PASS test_methyl_extractor_local"
