#!/usr/bin/env bash
# Tests for scripts/install_system_deps.sh with a hermetic PATH: apt-get,
# dnf, dpkg, dpkg-query and id are stubs; "installing" a package drops a fake
# binary into a dir on PATH. The apt-get stub resolves exact (=) and minimum
# (>=) dependencies against the bundle's Packages index and the installed set
# the way apt does (upgrade a dependency only when needed, never downgrade
# unless asked, never remove under --no-remove). Covers the offline bundle
# path including version skew between the bundle and the host, the online
# fallback, idempotence, apt lock retries, and failing loudly.
# check_system_deps_bundle.sh runs the real apt in containers at release time.

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

# Package -> binary it provides; also the "online repo" contents.
cat >"$STATE/repo" <<'R'
gpkg-sam gtool-sam
gpkg-bed gtool-bed
gpkg-acl gtool-acl
gpkg-lib -
gpkg-libw -
gpkg-app -
R

cat >"$STUB/id" <<'S'
#!/usr/bin/env bash
[[ "$1" == -u ]] && { echo "${FAKE_UID:-0}"; exit 0; }
exec /usr/bin/id "$@"
S
cat >"$STUB/dpkg" <<'S'
#!/usr/bin/env bash
[[ "$1" == --print-architecture ]] && { echo "${FAKE_ARCH:-amd64}"; exit 0; }
if [[ "$1" == --compare-versions ]]; then  # numeric dotted versions
  a="$2" op="$3" b="$4"
  lo="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n 1)"
  case "$op" in
    lt) [[ "$a" != "$b" && "$lo" == "$a" ]] ;;
    gt) [[ "$a" != "$b" && "$lo" == "$b" ]] ;;
    le) [[ "$lo" == "$a" ]] ;;
    ge) [[ "$lo" == "$b" ]] ;;
    eq) [[ "$a" == "$b" ]] ;;
    *) exit 2 ;;
  esac
  exit
fi
exit 1
S
# $STATE/installed: "PKG" (version 1.0) or "PKG VERSION" per line.
# $STATE/instdeps: dependencies of installed packages, "PKG DEP OP VERSION".
cat >"$STUB/dpkg-query" <<'S'
#!/usr/bin/env bash
pkg="${!#}"
line="$(awk -v p="$pkg" '$1==p {print; exit}' "$STATE/installed" 2>/dev/null)"
[[ -n "$line" ]] || exit 1
v="$(awk '{print $2}' <<<"$line")"
case "$*" in
  *Status*Version*) printf 'install ok installed %s' "${v:-1.0}" ;;
  *Version*) printf '%s' "${v:-1.0}" ;;
  *) printf 'install ok installed' ;;
esac
S
# Shared install logic for apt-get/dnf stubs.
cat >"$STUB/_install" <<'S'
#!/usr/bin/env bash
install_pkg() {  # install_pkg NAME [VERSION]
  local bin
  bin="$(awk -v p="$1" '$1==p {print $2}' "$STATE/repo")"
  [[ -n "$bin" ]] || return 1
  awk -v p="$1" '$1!=p' "$STATE/installed" >"$STATE/installed.new"; mv "$STATE/installed.new" "$STATE/installed"
  echo "$1${2:+ $2}" >>"$STATE/installed"
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
action="" nodl=0 skip=0 nosrc=0 srclist="" sim=0 noremove=0 downgrade=0; pkgs=()
for a in "$@"; do
  if [[ "$skip" == 1 ]]; then
    skip=0
    case "$a" in Dir::Etc::SourceList=*) srclist="${a#*=}"; [[ -s "$srclist" ]] || nosrc=1 ;; esac
    continue
  fi
  case "$a" in
    -o) skip=1 ;;
    update|install) action="$a" ;;
    --no-download) nodl=1 ;;
    -s|--simulate) sim=1 ;;
    --no-remove) noremove=1 ;;
    --allow-downgrades) downgrade=1 ;;
    -*) ;;
    *) pkgs+=("$a") ;;
  esac
done
repo=""
if [[ -n "$srclist" && -s "$srclist" ]] && grep -q 'file:' "$srclist"; then
  repo="$(sed -n 's/.*file:\([^ ]*\) .*/\1/p' "$srclist")"
  [[ "$repo" == /* ]] || { echo "E: file: repository path is not absolute: $repo" >&2; exit 100; }
fi
if [[ "$action" == update ]]; then
  if [[ -n "$repo" ]]; then [[ -r "$repo/Packages" && -r "$repo/Release" ]] || exit 100; exit 0; fi
  [[ "${FAKE_ONLINE:-1}" == 1 ]] || exit 100; exit 0
fi
[[ "$action" == install ]] || exit 0
for p in "${pkgs[@]}"; do [[ "$p" != *.deb ]] || { echo "E: local .deb arguments are not expected" >&2; exit 100; }; done
if [[ -z "$repo" ]]; then  # online
  for p in "${pkgs[@]}"; do
    [[ "$nodl" == 0 && "$nosrc" == 0 && "${FAKE_ONLINE:-1}" == 1 ]] || { echo "E: Unable to locate package $p" >&2; exit 100; }
    install_pkg "$p" || { echo "E: Unable to locate package $p" >&2; exit 100; }
  done
  exit 0
fi
# ---- mini apt resolver over $repo/Packages and the installed set ----
field() { awk -v p="$1" -v f="$2" '/^Package: /{cur=$2} index($0, f ": ")==1 && cur==p {sub(/^[^:]*: /,""); print; exit}' "$repo/Packages"; }
bver() { field "$1" Version; }
iver() { local l; l="$(awk -v p="$1" '$1==p {print; exit}' "$STATE/installed")"; [[ -n "$l" ]] || return 1; l="$(awk '{print $2}' <<<"$l")"; echo "${l:-1.0}"; }
sat() { [[ -z "$2" ]] && return 0; if [[ "$2" == "=" ]]; then dpkg --compare-versions "$1" eq "$3"; else dpkg --compare-versions "$1" ge "$3"; fi; }
declare -A plan=() explicit=()
broken=()
need() {  # need FROM NAME OP VER
  local from="$1" n="$2" op="$3" v="$4" cur="" bv
  if [[ -n "${plan[$n]:-}" ]]; then
    sat "${plan[$n]}" "$op" "$v" || broken+=(" $from : Depends: $n ($op $v) but ${plan[$n]} is to be installed"); return
  fi
  if cur="$(iver "$n")"; then sat "$cur" "$op" "$v" && return; else cur=""; fi
  bv="$(bver "$n")"
  [[ -n "$bv" ]] || { broken+=(" $from : Depends: $n but it is not installable"); return; }
  if [[ -n "$cur" ]] && dpkg --compare-versions "$bv" lt "$cur"; then
    broken+=(" $from : Depends: $n ($op $v) but $cur is to be installed"); return
  fi
  sat "$bv" "$op" "$v" || { broken+=(" $from : Depends: $n ($op $v) but ${cur:-$bv} is to be installed"); return; }
  plan[$n]="$bv"; deps_of "$n"
}
deps_of() {
  local d n op v
  while IFS= read -r d; do
    [[ -n "${d// /}" ]] || continue
    if [[ "$d" =~ ^\ *([^ ]+)\ \((=|\>=)\ ([^\)]+)\) ]]; then n="${BASH_REMATCH[1]}" op="${BASH_REMATCH[2]}" v="${BASH_REMATCH[3]}"
    else n="${d// /}" op="" v=""; fi
    need "$1" "$n" "$op" "$v"
  done < <(field "$1" Depends | tr ',' '\n')
}
for r in "${pkgs[@]}"; do
  n="${r%%=*}"; v=""; [[ "$r" == *=* ]] && v="${r#*=}"
  bv="$(bver "$n")"
  [[ -n "$bv" ]] || { echo "E: Unable to locate package $n" >&2; exit 100; }
  if [[ -n "$v" ]]; then
    [[ "$v" == "$bv" ]] || { echo "E: Version '$v' for '$n' was not found" >&2; exit 100; }
    if cur="$(iver "$n")" && dpkg --compare-versions "$v" lt "$cur" && [[ "$downgrade" == 0 ]]; then
      echo "E: Packages were downgraded and -y was used without --allow-downgrades." >&2; exit 100
    fi
    plan[$n]="$v"; explicit[$n]=1
  fi
done
for n in "${!plan[@]}"; do deps_of "$n"; done
for r in "${pkgs[@]}"; do
  n="${r%%=*}"
  [[ -n "${plan[$n]:-}" ]] || iver "$n" >/dev/null || need "(request)" "$n" "" ""
done
# Installed packages whose dependencies the plan breaks: moved up to their
# bundled version when that fixes it (apt's resolver does the same; it never
# picks a downgrade by itself), else reported broken or removed.
remv=() changed=1
while [[ "$changed" == 1 ]]; do
  changed=0
  while read -r P D OP V; do
    [[ -n "${P:-}" && -n "${plan[$D]:-}" && -z "${plan[$P]:-}" ]] || continue
    sat "${plan[$D]}" "$OP" "$V" && continue
    pb="$(bver "$P")"
    if [[ -n "$pb" && "$(field "$P" Depends)" == *"$D (= ${plan[$D]})"* ]] && dpkg --compare-versions "$pb" gt "$(iver "$P")"; then
      plan[$P]="$pb"; changed=1
    elif [[ -n "$pb" ]]; then
      # Real apt: a package with another candidate is reported broken ...
      broken+=(" $P : Depends: $D ($OP $V) but ${plan[$D]} is to be installed")
    elif [[ " ${remv[*]} " != *" $P "* ]]; then
      remv+=("$P")  # ... one without is removed (refused by --no-remove).
    fi
  done < <(cat "$STATE/instdeps" 2>/dev/null)
done
if [[ "${#broken[@]}" -gt 0 ]]; then
  echo "The following packages have unmet dependencies:"; printf '%s\n' "${broken[@]}"
  echo "E: Unable to correct problems, you have held broken packages."; exit 100
fi
if [[ "${#remv[@]}" -gt 0 && "$noremove" == 1 ]]; then
  echo "E: Packages need to be removed but remove is disabled."; exit 100
fi
for P in "${remv[@]}"; do echo "Remv $P [$(iver "$P")]"; done
for n in "${!plan[@]}"; do
  if cur="$(iver "$n")"; then
    [[ "$cur" == "${plan[$n]}" ]] && continue
    echo "Inst $n [$cur] (${plan[$n]} bundle)"
  else
    echo "Inst $n (${plan[$n]} bundle)"
  fi
done
[[ "$sim" == 1 ]] && exit 0
for n in "${!plan[@]}"; do install_pkg "$n" "${plan[$n]}" || exit 100; done
S
cat >"$STUB/dnf" <<'S'
#!/usr/bin/env bash
source "$(dirname "$0")/_install"
echo "dnf $*" >>"$STATE/calls"
for p in "$@"; do [[ "$p" == -* || "$p" == install ]] && continue; install_pkg "$p" || exit 1; done
S
chmod +x "$STUB"/*

printf 'ID=ubuntu\nVERSION_ID="24.04"\n' >"$TMP/os-release"

# make_bundle DIR TARGET SPEC...; SPEC is PKG[=VERSION][:DEP (= V)|DEP (>= V)|DEP,...]
# Writes the debs (text stand-ins), Packages, Release and SHA256SUMS like
# bundle_system_deps.sh does.
make_bundle() {
  local d="$1/$2" spec pkg ver deps f; shift 2
  rm -rf "$d"; mkdir -p "$d"
  : >"$d/Packages"
  for spec in "$@"; do
    pkg="${spec%%:*}" deps=""; [[ "$spec" == *:* ]] && deps="${spec#*:}"
    ver=1.0; [[ "$pkg" == *=* ]] && { ver="${pkg#*=}"; pkg="${pkg%%=*}"; }
    f="${pkg}_${ver}_amd64.deb"
    printf 'Package: %s\nVersion: %s\n' "$pkg" "$ver" >"$d/$f"
    { printf 'Package: %s\nVersion: %s\nArchitecture: amd64\n' "$pkg" "$ver"
      [[ -z "$deps" ]] || printf 'Depends: %s\n' "$deps"
      printf 'Filename: ./%s\nSHA256: %s\n\n' "$f" "$(sha256sum "$d/$f" | cut -d' ' -f1)"; } >>"$d/Packages"
  done
  printf 'Origin: GoliathOmics\nSHA256:\n %s Packages\n' "$(sha256sum "$d/Packages" | cut -d' ' -f1)" >"$d/Release"
  (cd "$d" && sha256sum -- *.deb Packages Release >SHA256SUMS)
}

reset() { rm -rf "${FAKEBIN:?}"/* "$STATE/calls" "$STATE/installed" "$STATE/instdeps" "$STATE/failcount" "$TMP/local"; touch "$STATE/installed"; }

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
installed() { awk -v p="$1" '$1==p {print ($2 == "" ? "1.0" : $2)}' "$STATE/installed"; }
expect() {  # expect NAME PKG VERSION
  [[ "$(installed "$2")" == "$3" ]] || { cat "$TMP/$1.out"; echo "installed:"; cat "$STATE/installed"; fail "$1: $2 should be at $3 (is '$(installed "$2")')"; }
}
B="ubuntu-24.04-amd64"

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

# 3. Offline bundle: apt resolves from the staged local repository only.
reset
make_bundle "$TMP/bundle" "$B" "gpkg-sam:gpkg-lib (= 1.0)" gpkg-bed gpkg-acl gpkg-lib
FAKE_ONLINE=0 run bundle --bundle "$TMP/bundle"
[[ "$(rc bundle)" == 0 ]] || { cat "$TMP/bundle.out"; fail "bundle install should succeed offline"; }
grep -q 'installed from the release bundle' "$TMP/bundle.out" || fail "bundle path message"
expect bundle gpkg-lib 1.0
calls | grep -q -- '--no-download' && fail "--no-download breaks apt with local paths"
calls | grep -q '\.deb' && fail "apt should resolve from the bundle repository, not loose .deb arguments"
calls | grep -v "Dir::Etc::SourceList=$TMP/local/goliath-sysdeps" | grep -q . \
  && { calls; fail "every bundle apt call must use only the staged bundle source list"; }
grep -q "file:$TMP/local/goliath-sysdeps.*/repo ./" "$TMP/bundle.out" 2>/dev/null && fail "source list leaked to output"
calls | grep -q 'Dir::Cache::archives=' || fail "the archive cache must be private (archives disabled)"
calls | grep -q 'Dir::State::Lists=' || fail "apt lists must be private (no stale host lists)"
calls | grep -q -- ' -s .*install' || fail "the plan must be simulated before installing"
calls | grep -- ' install ' | grep -v -- ' -s ' | grep -q -- '--no-remove' || fail "the real install must refuse removals"
calls | grep -q "$TMP/bundle" && fail "apt must get the local copy, not the shared bundle dir"
[[ -z "$(ls -A "$TMP/local")" ]] || fail "local stage must be cleaned up"

# 4. Idempotent: second run does nothing.
: >"$STATE/calls"
FAKE_ONLINE=0 run again --bundle "$TMP/bundle"
[[ "$(rc again)" == 0 && -z "$(calls)" ]] || fail "second run should be a no-op"

# 5. A dependency already installed at a version that satisfies is left alone.
reset
make_bundle "$TMP/b5" "$B" "gpkg-sam:gpkg-lib (>= 1.0)" gpkg-bed gpkg-acl gpkg-lib=2.0
echo "gpkg-lib 1.5" >"$STATE/installed"
FAKE_ONLINE=0 run keep --bundle "$TMP/b5"
[[ "$(rc keep)" == 0 ]] || { cat "$TMP/keep.out"; fail "keep"; }
expect keep gpkg-lib 1.5

# 6. The runpod skew: the host has an OLDER build of a library the bundled
# tool needs exactly (libtinfo6 for libncurses6), plus an installed sibling
# pinned to that older build (libncursesw6). Both move up to the bundled
# versions in the same offline transaction.
reset
make_bundle "$TMP/skew" "$B" "gpkg-sam=1.0:gpkg-lib (= 2.2)" gpkg-bed gpkg-acl gpkg-lib=2.2 "gpkg-libw=2.2:gpkg-lib (= 2.2)"
printf 'gpkg-lib 2.0\ngpkg-libw 2.0\n' >"$STATE/installed"
echo "gpkg-libw gpkg-lib = 2.0" >"$STATE/instdeps"
FAKE_ONLINE=0 run skew --bundle "$TMP/skew"
[[ "$(rc skew)" == 0 ]] || { cat "$TMP/skew.out"; fail "an older host library must be upgraded to the bundled build"; }
expect skew gpkg-lib 2.2
expect skew gpkg-libw 2.2
grep -q 'WARNING: installing from the network' "$TMP/skew.out" && fail "skew must not need the network"
grep -q '2 changed to the bundled version' "$TMP/skew.out" || { cat "$TMP/skew.out"; fail "summary should count the upgrades"; }

# 7. Host NEWER than the bundle with an exact dependency: the library (and
# its installed sibling) move down to the bundled build, loudly.
reset
printf 'gpkg-lib 2.3\ngpkg-libw 2.3\n' >"$STATE/installed"
echo "gpkg-libw gpkg-lib = 2.3" >"$STATE/instdeps"
FAKE_ONLINE=0 run newer --bundle "$TMP/skew"
[[ "$(rc newer)" == 0 ]] || { cat "$TMP/newer.out"; fail "a newer host library with an exact dependency should move to the bundled build"; }
expect newer gpkg-lib 2.2
expect newer gpkg-libw 2.2
grep -q 'WARNING: moving 2 host package(s) DOWN' "$TMP/newer.out" || { cat "$TMP/newer.out"; fail "a downgrade must warn loudly"; }
grep -q 'gpkg-lib 2.3 -> 2.2' "$TMP/newer.out" || fail "the downgrade must be named"
calls | grep -- ' install ' | grep -v -- ' -s ' | grep -q -- '--allow-downgrades .*gpkg-lib=2.2' || { calls; fail "downgrade must be explicit"; }

# 8. Host newer and something installed needs that newer build: never
# remove or break it. Report the conflict and fall back online, loudly.
reset
printf 'gpkg-lib 2.3\ngpkg-app 1.0\n' >"$STATE/installed"
echo "gpkg-app gpkg-lib >= 2.3" >"$STATE/instdeps"
run conflict --bundle "$TMP/skew"
[[ "$(rc conflict)" == 0 ]] || { cat "$TMP/conflict.out"; fail "conflict should fall back online"; }
grep -q 'bundle: CONFLICT' "$TMP/conflict.out" || { cat "$TMP/conflict.out"; fail "conflict must be reported"; }
grep -q 'apt would have to remove: gpkg-app' "$TMP/conflict.out" || { cat "$TMP/conflict.out"; fail "conflict must name what would break"; }
grep -q 'WARNING: installing from the network' "$TMP/conflict.out" || fail "online fallback must warn loudly"
expect conflict gpkg-lib 2.3
expect conflict gpkg-app 1.0
reset
printf 'gpkg-lib 2.3\ngpkg-app 1.0\n' >"$STATE/installed"
echo "gpkg-app gpkg-lib >= 2.3" >"$STATE/instdeps"
FAKE_ONLINE=0 run conflictoff --bundle "$TMP/skew"
[[ "$(rc conflictoff)" != 0 ]] || fail "conflict with no network must fail"

# 9. Host newer without an exact dependency: kept, no downgrade.
reset
make_bundle "$TMP/b9" "$B" "gpkg-sam:gpkg-lib (>= 1.0)" gpkg-bed gpkg-acl gpkg-lib=2.0
echo "gpkg-lib 3.0" >"$STATE/installed"
FAKE_ONLINE=0 run nodowngrade --bundle "$TMP/b9"
[[ "$(rc nodowngrade)" == 0 ]] || { cat "$TMP/nodowngrade.out"; fail "nodowngrade"; }
expect nodowngrade gpkg-lib 3.0
calls | grep -q -- '--allow-downgrades' && fail "no downgrade without an exact dependency"

# 10. Corrupt bundle: ignored, online fallback used.
reset
cp -r "$TMP/bundle" "$TMP/badbundle"
printf 'Package: evil\n' >"$TMP/badbundle/$B/gpkg-sam_1.0_amd64.deb"
run corrupt --bundle "$TMP/badbundle"
[[ "$(rc corrupt)" == 0 ]] || { cat "$TMP/corrupt.out"; fail "corrupt bundle should fall back online"; }
grep -q 'checksum mismatch' "$TMP/corrupt.out" || fail "checksum mismatch message"
grep -q 'WARNING: installing from the network' "$TMP/corrupt.out" || fail "online fallback must warn loudly"
grep -qx evil "$STATE/installed" && fail "corrupt deb must not be installed"
calls | grep -q 'install .*gpkg-sam' || fail "online fallback"
# A tampered index is a checksum mismatch too.
reset
cp -r "$TMP/bundle" "$TMP/badindex"
printf '\nPackage: evil\nVersion: 9\n' >>"$TMP/badindex/$B/Packages"
run badindex --bundle "$TMP/badindex"
grep -q 'checksum mismatch' "$TMP/badindex.out" || { cat "$TMP/badindex.out"; fail "a tampered Packages must be rejected"; }

# 11. A bundle without the apt index (pre-2026.10.19 format) is ignored.
reset
cp -r "$TMP/bundle" "$TMP/oldfmt"
rm "$TMP/oldfmt/$B/Packages" "$TMP/oldfmt/$B/Release"
run oldfmt --bundle "$TMP/oldfmt"
[[ "$(rc oldfmt)" == 0 ]] && grep -q 'no apt repository index' "$TMP/oldfmt.out" || { cat "$TMP/oldfmt.out"; fail "old-format bundle"; }

# 12. Bundle for another OS: online fallback.
reset
make_bundle "$TMP/other" ubuntu-22.04-amd64 gpkg-sam gpkg-bed gpkg-acl
run otheros --bundle "$TMP/other"
[[ "$(rc otheros)" == 0 ]] && grep -q 'none for ubuntu-24.04-amd64' "$TMP/otheros.out" || { cat "$TMP/otheros.out"; fail "other-OS bundle should fall back"; }

# 13. Partial bundle: what it has installs offline, the rest comes online.
reset
make_bundle "$TMP/partial" "$B" gpkg-sam
run partial --bundle "$TMP/partial"
[[ "$(rc partial)" == 0 ]] && grep -q 'still missing after the release bundle: gtool-bed gtool-acl' "$TMP/partial.out" \
  || { cat "$TMP/partial.out"; fail "partial bundle"; }
grep -q 'bundle lacks gpkg-bed gpkg-acl' "$TMP/partial.out" || { cat "$TMP/partial.out"; fail "partial bundle should name what it lacks"; }

# 14. Partial bundle and no network: fail loudly, naming the tools.
reset
FAKE_ONLINE=0 run offlinefail --bundle "$TMP/partial"
[[ "$(rc offlinefail)" != 0 ]] || fail "missing tools with no network must fail"
grep -q 'ERROR' "$TMP/offlinefail.out" || fail "loud error"

# 15. Package not in any repo: fail loudly, naming the tool.
reset
grep -v gpkg-acl "$STATE/repo" >"$STATE/repo.new" && cp "$STATE/repo" "$STATE/repo.bak" && mv "$STATE/repo.new" "$STATE/repo"
run norepo
[[ "$(rc norepo)" != 0 ]] || fail "uninstallable tool must fail"
grep -q 'gpkg-acl' "$TMP/norepo.out" || { cat "$TMP/norepo.out"; fail "error should name the package"; }
mv "$STATE/repo.bak" "$STATE/repo"

# 16. apt lock: retried, then succeeds.
reset
FAKE_FAIL_FIRST=2 RETRIES=4 run lock
[[ "$(rc lock)" == 0 ]] && grep -q 'retrying' "$TMP/lock.out" || { cat "$TMP/lock.out"; fail "apt lock retry"; }
calls | grep -q 'DPkg::Lock::Timeout' || fail "apt should wait for the dpkg lock"

# 17. Not root and no sudo: clear error.
reset
FAKE_UID=1000 run noroot
[[ "$(rc noroot)" != 0 ]] && grep -q 'not root and no passwordless sudo' "$TMP/noroot.out" || { cat "$TMP/noroot.out"; fail "non-root"; }

# 18. dnf host (no apt-get): installs with dnf; bundle ignored.
reset
mv "$STUB/apt-get" "$STUB/apt-get.off"
run dnf --bundle "$TMP/bundle"
[[ "$(rc dnf)" == 0 ]] && calls | grep -q '^dnf .*install' || { cat "$TMP/dnf.out"; fail "dnf path"; }

# 19. No package manager at all: clear error.
reset
mv "$STUB/dnf" "$STUB/dnf.off"
run nopm
[[ "$(rc nopm)" != 0 ]] && grep -q 'no supported package manager' "$TMP/nopm.out" || fail "no package manager"
mv "$STUB/apt-get.off" "$STUB/apt-get"; mv "$STUB/dnf.off" "$STUB/dnf"

# 20. The real manifest parses and the installer's --check runs against it.
set +e
bash "$SCRIPT" --check >"$TMP/real.out" 2>&1
real_rc=$?
set -e
[[ "$real_rc" == 0 || "$real_rc" == 1 ]] || { cat "$TMP/real.out"; fail "real manifest should parse (rc $real_rc)"; }

echo "PASS: install_system_deps"
