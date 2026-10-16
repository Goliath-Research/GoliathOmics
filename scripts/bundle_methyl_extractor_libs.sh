#!/bin/bash
# Bundle MethylExtractor's non-base shared libraries (htslib and its deps)
# into a methyl-extractor-linux-<arch>.tar.gz, in place.
#
# MethylExtractor links libhts.so.3 dynamically. The MethylExtractor release
# tarball does not ship it and the RunPod worker image does not have it, so
# the binary fails to load (exit 127). This script repackages the tarball:
#   lib/libhts.so.3 (+ real versioned file), libdeflate.so.0, libhtscodecs.so.2
#   bin/MethylExtractor RUNPATH=$ORIGIN/../lib (patchelf, when available)
#   env.sh exports LD_LIBRARY_PATH=<root>/lib
# Re-running on an already bundled tarball is a no-op.
#
# Libraries come from Ubuntu .debs for the target arch (no root, works for
# aarch64 on an amd64 runner) or from --lib-dir.
#
# Usage:
#   bundle_methyl_extractor_libs.sh --tarball PATH [--arch aarch64|amd64]
#       [--lib-dir DIR] [--suite jammy] [--debs "libhts3 libdeflate0 libhtscodecs2"]
#       [--require-patchelf] [--force]
#
# Env: METHYL_EXTRACTOR_BUNDLE_SUITE (default jammy, the RunPod base: it has
# libhdf5_serial.so.103), METHYL_EXTRACTOR_BUNDLE_DEBS,
# UBUNTU_ARCHIVE_URL (amd64), UBUNTU_PORTS_URL (arm64).

set -euo pipefail

info() { echo "[INFO] $*" >&2; }
warn() { echo "[WARN] $*" >&2; }
die() { echo "[ERROR] $*" >&2; exit 1; }

TARBALL=""
ARCH=""
LIB_DIR=""
SUITE="${METHYL_EXTRACTOR_BUNDLE_SUITE:-jammy}"
DEBS="${METHYL_EXTRACTOR_BUNDLE_DEBS:-libhts3 libdeflate0 libhtscodecs2}"
REQUIRE_PATCHELF=0
FORCE=0
# Sonames that must end up in lib/. libdeflate and libhtscodecs are libhts3
# (jammy 1.13) dependencies that are not in the Ubuntu base image; zlib,
# bz2, lzma and libc are.
REQUIRED_LIBS="${METHYL_EXTRACTOR_BUNDLE_LIBS:-libhts.so.3 libdeflate.so.0 libhtscodecs.so.2}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tarball) TARBALL="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --lib-dir) LIB_DIR="${2:-}"; shift 2 ;;
    --suite) SUITE="${2:-}"; shift 2 ;;
    --debs) DEBS="${2:-}"; shift 2 ;;
    --require-patchelf) REQUIRE_PATCHELF=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ -f "$TARBALL" ]] || die "--tarball is required and must exist (got '${TARBALL}')"
if [[ -z "$ARCH" ]]; then
  if [[ "$(basename "$TARBALL")" =~ methyl-extractor-linux-(aarch64|amd64) ]]; then
    ARCH="${BASH_REMATCH[1]}"
  else
    die "--arch is required when the tarball name has no arch"
  fi
fi
case "$ARCH" in
  aarch64|arm64) ARCH=aarch64; DEB_ARCH=arm64; MIRROR="${UBUNTU_PORTS_URL:-http://ports.ubuntu.com/ubuntu-ports}" ;;
  amd64|x86_64|x64) ARCH=amd64; DEB_ARCH=amd64; MIRROR="${UBUNTU_ARCHIVE_URL:-http://archive.ubuntu.com/ubuntu}" ;;
  *) die "Unknown arch: $ARCH" ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/stage"
mkdir -p "$STAGE"
tar -xzf "$TARBALL" -C "$STAGE"
[[ -f "$STAGE/bin/MethylExtractor" ]] || die "${TARBALL} has no bin/MethylExtractor"
mkdir -p "$STAGE/lib"

have_all() {
  local so
  for so in $REQUIRED_LIBS; do
    [[ -e "$STAGE/lib/$so" ]] || return 1
  done
  return 0
}

if have_all && [[ "$FORCE" -eq 0 ]] && grep -q LD_LIBRARY_PATH "$STAGE/env.sh" 2>/dev/null; then
  info "$(basename "$TARBALL") already bundles: ${REQUIRED_LIBS}"
  exit 0
fi

# Copy SONAME (symlink chain resolved: the real versioned file plus the
# soname link) from SRC_DIR into lib/.
copy_lib() {
  local src_dir="$1" so="$2" real
  [[ -e "$src_dir/$so" ]] || return 1
  real="$(readlink -f "$src_dir/$so")"
  [[ -f "$real" ]] || return 1
  cp -L "$real" "$STAGE/lib/$(basename "$real")"
  chmod 0644 "$STAGE/lib/$(basename "$real")"
  if [[ "$(basename "$real")" != "$so" ]]; then
    ln -sfn "$(basename "$real")" "$STAGE/lib/$so"
  fi
  info "bundled ${so} -> $(basename "$real")"
}

# Unpack the Ubuntu .debs for DEB_ARCH into $WORK/root and print its lib dir.
fetch_debs() {
  local root="$WORK/root" pkg
  mkdir -p "$root" "$WORK/debs"
  # shellcheck disable=SC2086  # DEBS is a word list
  python3 - "$MIRROR" "$SUITE" "$DEB_ARCH" "$WORK/debs" $DEBS <<'PY'
import gzip, sys, urllib.request
mirror, suite, arch, out, *pkgs = sys.argv[1:]
want = set(pkgs)
found = {}
for pocket in (f"{suite}-updates", f"{suite}-security", suite):
    for comp in ("main", "universe"):
        url = f"{mirror}/dists/{pocket}/{comp}/binary-{arch}/Packages.gz"
        try:
            data = gzip.decompress(urllib.request.urlopen(url, timeout=120).read()).decode()
        except Exception as e:  # missing pocket/component is fine
            print(f"[WARN] {url}: {e}", file=sys.stderr)
            continue
        for stanza in data.split("\n\n"):
            fields = dict(l.split(": ", 1) for l in stanza.splitlines() if ": " in l and not l.startswith(" "))
            name = fields.get("Package")
            if name in want and name not in found:
                found[name] = fields["Filename"]
    if want <= found.keys():
        break
missing = want - found.keys()
if missing:
    sys.exit(f"[ERROR] not found in {mirror} {suite} ({arch}): {' '.join(sorted(missing))}")
for name, fn in found.items():
    dest = f"{out}/{name}.deb"
    print(f"[INFO] {name}: {mirror}/{fn}", file=sys.stderr)
    urllib.request.urlretrieve(f"{mirror}/{fn}", dest)
PY
  for pkg in "$WORK"/debs/*.deb; do
    dpkg-deb -x "$pkg" "$root"
  done
}

lib_search_dirs() {
  if [[ -n "$LIB_DIR" ]]; then
    printf '%s\n' "$LIB_DIR"
  else
    find "$WORK/root" -type d \( -path '*/lib/*-linux-gnu' -o -path '*/lib' \) 2>/dev/null
  fi
}

[[ -n "$LIB_DIR" ]] || fetch_debs
for so in $REQUIRED_LIBS; do
  done_one=0
  while IFS= read -r d; do
    if copy_lib "$d" "$so"; then done_one=1; break; fi
  done < <(lib_search_dirs)
  [[ "$done_one" -eq 1 ]] || die "could not find ${so} (searched: $(lib_search_dirs | tr '\n' ' '))"
done

if command -v patchelf >/dev/null 2>&1; then
  # shellcheck disable=SC2016
  if ! patchelf --set-rpath '$ORIGIN/../lib' "$STAGE/bin/MethylExtractor"; then
    [[ "$REQUIRE_PATCHELF" -eq 1 ]] && die "patchelf failed on bin/MethylExtractor"
    warn "patchelf failed on bin/MethylExtractor; relying on LD_LIBRARY_PATH"
  fi
  for f in "$STAGE"/lib/*.so.*; do
    [[ -L "$f" ]] && continue
    # shellcheck disable=SC2016
    patchelf --set-rpath '$ORIGIN' "$f" 2>/dev/null || warn "patchelf failed on $(basename "$f")"
  done
  info "RUNPATH set to \$ORIGIN/../lib"
elif [[ "$REQUIRE_PATCHELF" -eq 1 ]]; then
  die "patchelf is required (apt-get install patchelf)"
else
  warn "patchelf not found; relying on LD_LIBRARY_PATH from env.sh / worker.env"
fi

# Report anything still unresolved when the binary is for this machine.
# (ldd prints nothing useful for a foreign-arch binary.)
if command -v ldd >/dev/null 2>&1; then
  missing="$(for f in "$STAGE/bin/MethylExtractor" "$STAGE"/lib/*.so*; do
    LD_LIBRARY_PATH="$STAGE/lib" ldd "$f" 2>/dev/null | grep 'not found' || true
  done | sort -u)"
  if [[ -n "$missing" ]]; then
    warn "still unresolved on this build host (may exist on the worker image):"
    printf '%s\n' "$missing" >&2
  fi
fi

cat >"$STAGE/env.sh" <<'ENVEOF'
#!/bin/sh
# Source after extract: . <install root>/env.sh
_ROOT="$(cd "$(dirname "$0")" && pwd)"
export METHYL_EXTRACTOR_BIN="$_ROOT/bin/MethylExtractor"
export HDF5_PLUGIN_PATH="$_ROOT/lib/hdf5_zstd_plugin"
export LD_LIBRARY_PATH="$_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$_ROOT/bin:$PATH"
ENVEOF
chmod 0755 "$STAGE/env.sh" "$STAGE/bin/MethylExtractor"

out="$WORK/out.tar.gz"
( cd "$STAGE" && tar -czf "$out" --owner=0 --group=0 ./* )
mv -f "$out" "$TARBALL"
info "Repackaged $(basename "$TARBALL") (sha256=$(sha256sum "$TARBALL" | awk '{print $1}'))"
