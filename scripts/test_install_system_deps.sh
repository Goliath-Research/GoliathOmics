#!/usr/bin/env bash
# Tests for scripts/install_system_deps.sh with a hermetic PATH: apt-get,
# dnf, dpkg, dpkg-query, dpkg-deb and id are stubs; "installing" a package
# drops a fake binary into a dir on PATH. Covers the offline bundle path,
# online fallback, idempotence, apt lock retries, and failing loudly.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/install_system_deps.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

SYS="$TMP/sys" STUB="$TMP/stub" FAKEBIN="$TMP/fakebin" STATE="$TMP/state"
mkdir -p "$SYS" "$STUB" "$FAKEBIN" "$STATE"
for c in bash env cat cp mkdir mktemp rm sed awk grep sha256sum tail sort basename dirname chmod sleep touch tr head cut wc mv ls; do
  p="$(command -v "$c")" || fail "host lacks $c"
  ln -s "$p" "$SYS/$c"
done

# Test manifest: fake tool names so host binaries never satisfy them.
cat >"$TMP/tools.list" <<'L'
# binary  apt  dnf  apk
gtool-sam   gpkg-sam   gpkg-sam   gpkg-sam
gtool-bed   gpkg-bed   gpkg-bed   -
gtool-acl   gpkg-acl   gpkg-acl   gpkg-acl
L

# Package database: package -> binary it provides; "online repo" contents.
cat >"$STATE/repo" <<'R'
gpkg-sam gtool-sam
gpkg-bed gtool-bed
gpkg-acl gtool-acl
gpkg-lib -
R

cat >"$STUB/id" <<'S'
#!/usr/bin/env bash
[[ "$1" == -u ]] && { echo "${FAKE_UID:-0}"; exit 0; }
exec /usr/bin/id "$@"
S
cat >"$STUB/dpkg" <<'S'
#!/usr/bin/env bash
[[ "$1" == --print-architecture ]] && { echo "${FAKE_ARCH:-amd64}"; exit 0; }
exit 1
S
cat >"$STUB/dpkg-query" <<'S'
#!/usr/bin/env bash
pkg="${!#}"
grep -qx "$pkg" "$STATE/installed" 2>/dev/null && { printf 'install ok installed'; exit 0; }
exit 1
S
cat >"$STUB/dpkg-deb" <<'S'
#!/usr/bin/env bash
# dpkg-deb -f FILE Package
sed -n 's/^Package: //p' "$2"
S
# Shared install logic for apt-get/dnf stubs.
cat >"$STUB/_install" <<'S'
#!/usr/bin/env bash
install_pkg() {  # install_pkg NAME
  local bin
  bin="$(awk -v p="$1" '$1==p {print $2}' "$STATE/repo")"
  [[ -n "$bin" ]] || return 1
  echo "$1" >>"$STATE/installed"
  if [[ "$bin" != - ]]; then printf '#!/bin/sh\nexit 0\n' >"$FAKEBIN/$bin"; chmod +x "$FAKEBIN/$bin"; fi
}
S
cat >"$STUB/apt-get" <<'S'
#!/usr/bin/env bash
source "$(dirname "$0")/_install"
echo "apt-get $*" >>"$STATE/calls"
if [[ -n "${FAKE_FAIL_FIRST:-}" ]]; then
  n="$(cat "$STATE/failcount" 2>/dev/null || echo 0)"
  if [[ "$n" -lt "$FAKE_FAIL_FIRST" ]]; then echo $((n + 1)) >"$STATE/failcount"; echo "E: Could not get lock /var/lib/dpkg/lock-frontend" >&2; exit 100; fi
fi
action="" nodl=0 skip=0; pkgs=()
for a in "$@"; do
  [[ "$skip" == 1 ]] && { skip=0; continue; }
  case "$a" in
    -o) skip=1 ;;
    update|install) action="$a" ;;
    --no-download) nodl=1 ;;
    -*) ;;
    *) pkgs+=("$a") ;;
  esac
done
[[ "$action" == update ]] && { [[ "${FAKE_ONLINE:-1}" == 1 ]] || exit 100; exit 0; }
for p in "${pkgs[@]}"; do
  if [[ "$p" == /* || "$p" == ./* ]]; then
    install_pkg "$(sed -n 's/^Package: //p' "$p")" || exit 100
  else
    [[ "$nodl" == 0 && "${FAKE_ONLINE:-1}" == 1 ]] || exit 100
    install_pkg "$p" || { echo "E: Unable to locate package $p" >&2; exit 100; }
  fi
done
S
cat >"$STUB/dnf" <<'S'
#!/usr/bin/env bash
source "$(dirname "$0")/_install"
echo "dnf $*" >>"$STATE/calls"
for p in "$@"; do [[ "$p" == -* || "$p" == install ]] && continue; install_pkg "$p" || exit 1; done
S
chmod +x "$STUB"/*

printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$TMP/os-release"

make_bundle() {  # make_bundle DIR TARGET PKG...
  local d="$1/$2"; shift 2
  mkdir -p "$d"
  for p in "$@"; do printf 'Package: %s\n' "$p" >"$d/${p}_1.0_amd64.deb"; done
  (cd "$d" && sha256sum -- *.deb >SHA256SUMS)
}

reset() { rm -rf "${FAKEBIN:?}"/* "$STATE/calls" "$STATE/installed" "$STATE/failcount" "$TMP/local"; touch "$STATE/installed"; }

run() {  # run NAME [args]
  local name="$1"; shift
  set +e
  env -i PATH="$STUB:$FAKEBIN:$SYS" HOME="$TMP" STATE="$STATE" FAKEBIN="$FAKEBIN" \
    FAKE_UID="${FAKE_UID:-0}" FAKE_ONLINE="${FAKE_ONLINE:-1}" FAKE_FAIL_FIRST="${FAKE_FAIL_FIRST:-}" \
    GOLIATH_OS_RELEASE="$TMP/os-release" GOLIATH_SYSDEPS_STAGE_DIR="$TMP/local" \
    GOLIATH_SYSDEPS_RETRY_SECS=0 GOLIATH_SYSDEPS_RETRIES="${RETRIES:-3}" \
    bash "$SCRIPT" --manifest "$TMP/tools.list" "$@" >"$TMP/$name.out" 2>&1
  echo $? >"$TMP/$name.rc"
  set -e
}
rc() { cat "$TMP/$1.rc"; }
calls() { cat "$STATE/calls" 2>/dev/null || true; }

# 1. Everything present: no package manager call.
reset
for b in gtool-sam gtool-bed gtool-acl; do printf '#!/bin/sh\n' >"$FAKEBIN/$b"; chmod +x "$FAKEBIN/$b"; done
run present
[[ "$(rc present)" == 0 ]] || { cat "$TMP/present.out"; fail "all present should pass"; }
[[ -z "$(calls)" ]] || fail "nothing should be installed when all tools are present"

# 2. --check reports and fails, installs nothing.
reset
run check --check
[[ "$(rc check)" == 1 ]] && grep -q 'missing: gtool-sam gtool-bed gtool-acl' "$TMP/check.out" || { cat "$TMP/check.out"; fail "--check"; }
[[ -z "$(calls)" ]] || fail "--check must not install"

# 3. Offline bundle: installed from a local copy, no network, nothing from the bundle dir.
reset
make_bundle "$TMP/bundle" ubuntu-24.04-amd64 gpkg-sam gpkg-bed gpkg-acl gpkg-lib
FAKE_ONLINE=0 run bundle --bundle "$TMP/bundle"
[[ "$(rc bundle)" == 0 ]] || { cat "$TMP/bundle.out"; fail "bundle install should succeed offline"; }
grep -q 'installed from the release bundle' "$TMP/bundle.out" || fail "bundle path message"
calls | grep -q -- '--no-download' || fail "bundle install must be offline (--no-download)"
calls | grep -q ' update' && fail "bundle install must not apt-get update"
calls | grep -q "$TMP/bundle" && fail "apt must get the local copies, not the shared bundle dir"
calls | grep -q "$TMP/local/goliath-sysdeps" || fail "debs should be staged under the local stage dir"
[[ -z "$(ls -A "$TMP/local")" ]] || fail "local stage must be cleaned up"

# 4. Idempotent: second run does nothing.
: >"$STATE/calls"
FAKE_ONLINE=0 run again --bundle "$TMP/bundle"
[[ "$(rc again)" == 0 && -z "$(calls)" ]] || fail "second run should be a no-op"

# 5. Packages already installed are not reinstalled from the bundle.
reset
echo gpkg-lib >"$STATE/installed"
FAKE_ONLINE=0 run skipinst --bundle "$TMP/bundle"
[[ "$(rc skipinst)" == 0 ]] || { cat "$TMP/skipinst.out"; fail "skipinst"; }
calls | grep -q 'gpkg-lib_' && fail "an installed package must not be reinstalled/downgraded"

# 6. Corrupt bundle: ignored, online fallback used.
reset
cp -r "$TMP/bundle" "$TMP/badbundle"
printf 'Package: evil\n' >"$TMP/badbundle/ubuntu-24.04-amd64/gpkg-sam_1.0_amd64.deb"
run corrupt --bundle "$TMP/badbundle"
[[ "$(rc corrupt)" == 0 ]] || { cat "$TMP/corrupt.out"; fail "corrupt bundle should fall back online"; }
grep -q 'checksum mismatch' "$TMP/corrupt.out" || fail "checksum mismatch message"
grep -qx evil "$STATE/installed" && fail "corrupt deb must not be installed"
calls | grep -q 'install .*gpkg-sam' || fail "online fallback"

# 7. Bundle for another OS: online fallback.
reset
make_bundle "$TMP/other" ubuntu-22.04-amd64 gpkg-sam gpkg-bed gpkg-acl
run otheros --bundle "$TMP/other"
[[ "$(rc otheros)" == 0 ]] && grep -q 'none for ubuntu-24.04-amd64' "$TMP/otheros.out" || { cat "$TMP/otheros.out"; fail "other-OS bundle should fall back"; }

# 8. Partial bundle: the rest comes from the online repo.
reset
make_bundle "$TMP/partial" ubuntu-24.04-amd64 gpkg-sam
run partial --bundle "$TMP/partial"
[[ "$(rc partial)" == 0 ]] && grep -q 'still missing after the bundle: gtool-bed gtool-acl' "$TMP/partial.out" \
  || { cat "$TMP/partial.out"; fail "partial bundle"; }

# 9. Partial bundle and no network: fail loudly, naming the tools.
reset
FAKE_ONLINE=0 run offlinefail --bundle "$TMP/partial"
[[ "$(rc offlinefail)" != 0 ]] || fail "missing tools with no network must fail"
grep -q 'ERROR' "$TMP/offlinefail.out" || fail "loud error"

# 10. Package not in any repo: fail loudly, naming the tool.
reset
grep -v gpkg-acl "$STATE/repo" >"$STATE/repo.new" && cp "$STATE/repo" "$STATE/repo.bak" && mv "$STATE/repo.new" "$STATE/repo"
run norepo
[[ "$(rc norepo)" != 0 ]] || fail "uninstallable tool must fail"
grep -q 'gpkg-acl' "$TMP/norepo.out" || { cat "$TMP/norepo.out"; fail "error should name the package"; }
mv "$STATE/repo.bak" "$STATE/repo"

# 11. apt lock: retried, then succeeds.
reset
FAKE_FAIL_FIRST=2 RETRIES=4 run lock
[[ "$(rc lock)" == 0 ]] && grep -q 'retrying' "$TMP/lock.out" || { cat "$TMP/lock.out"; fail "apt lock retry"; }
calls | grep -q 'DPkg::Lock::Timeout' || fail "apt should wait for the dpkg lock"

# 12. Not root and no sudo: clear error.
reset
FAKE_UID=1000 run noroot
[[ "$(rc noroot)" != 0 ]] && grep -q 'not root and no passwordless sudo' "$TMP/noroot.out" || { cat "$TMP/noroot.out"; fail "non-root"; }

# 13. dnf host (no apt-get): installs with dnf; bundle ignored.
reset
mv "$STUB/apt-get" "$STUB/apt-get.off"
run dnf --bundle "$TMP/bundle"
[[ "$(rc dnf)" == 0 ]] && calls | grep -q '^dnf .*install' || { cat "$TMP/dnf.out"; fail "dnf path"; }

# 14. No package manager at all: clear error.
reset
mv "$STUB/dnf" "$STUB/dnf.off"
run nopm
[[ "$(rc nopm)" != 0 ]] && grep -q 'no supported package manager' "$TMP/nopm.out" || fail "no package manager"
mv "$STUB/apt-get.off" "$STUB/apt-get"; mv "$STUB/dnf.off" "$STUB/dnf"

# 15. The real manifest parses and the installer's --check runs against it.
set +e
bash "$SCRIPT" --check >"$TMP/real.out" 2>&1
real_rc=$?
set -e
[[ "$real_rc" == 0 || "$real_rc" == 1 ]] || { cat "$TMP/real.out"; fail "real manifest should parse (rc $real_rc)"; }

echo "PASS: install_system_deps"
