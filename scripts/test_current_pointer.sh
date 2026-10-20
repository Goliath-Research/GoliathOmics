#!/bin/bash
# /work/goliath/current is a pointer file (shared volumes cannot store
# symlinks: ln -s left an empty 0-byte file). Readers accept a pointer file,
# a symlink or a directory, and treat an empty/invalid file as missing.
# start_shared_worker refreshes a current release older than the one the
# bootstrap resolved; the bootstrap picks the highest goliathomics-* version.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
g="$work/goliath"
mkdir -p "$g/releases/2026.10.9" "$g/releases/2026.10.12" "$g/bin"
printf '{"version": "2026.10.9"}\n' >"$g/releases/2026.10.9/manifest.json"
printf '{"version": "2026.10.12"}\n' >"$g/releases/2026.10.12/manifest.json"

# shellcheck source=detect_platform.sh
source "$ROOT/scripts/detect_platform.sh"
if goliath_current_dir "$g" >/dev/null; then fail "missing current must not resolve"; fi
: >"$g/current"
if goliath_current_dir "$g" >/dev/null; then fail "empty 0-byte current must count as missing"; fi
printf '/nonexistent/release\n' >"$g/current"
if goliath_current_dir "$g" >/dev/null; then fail "pointer to a missing dir must count as missing"; fi
goliath_write_current "$g" "$g/releases/2026.10.12" || fail "write_current failed"
[[ -f "$g/current" && ! -L "$g/current" ]] || fail "current should be a regular pointer file"
[[ "$(cat "$g/current")" == "$g/releases/2026.10.12" ]] || fail "pointer should hold the release path"
[[ "$(goliath_current_dir "$g")" == "$g/releases/2026.10.12" ]] || fail "pointer should resolve"
rm -f "$g/current"
ln -s "$g/releases/2026.10.9" "$g/current"
[[ "$(goliath_current_dir "$g")" == "$g/releases/2026.10.9" ]] || fail "legacy symlink should resolve"
goliath_write_current "$g" "$g/releases/2026.10.12" || fail "write over a symlink failed"
[[ ! -L "$g/current" ]] || fail "symlink should be replaced by a pointer file"
printf 'releases/2026.10.9\n' >"$g/current"
[[ "$(goliath_current_dir "$g")" == "$g/releases/2026.10.9" ]] || fail "relative pointer resolves under root"

# The standalone copies of the resolver match detect_platform.sh.
want_fn="$(sed -n '/^goliath_current_dir() {/,/^}/p' "$ROOT/scripts/detect_platform.sh")"
for f in scripts/start_shared_worker.sh deploy/runpod/goliath-worker-bootstrap.sh \
         deploy/runpod/goliath-worker-start.sh scripts/report_deploy.sh; do
  [[ "$(sed -n '/^goliath_current_dir() {/,/^}/p' "$ROOT/$f")" == "$want_fn" ]] || fail "$f resolver differs"
done
grep -q 'goliath_write_current "\$ROOT" "\$RELEASE_DIR"' "$ROOT/scripts/promote_release.sh" || fail "promote must write the pointer"
if grep -n 'ln -s' "$ROOT/scripts/promote_release.sh" | grep -v '#'; then fail "promote must not symlink current"; fi
if grep -nE '"\$ROOT/current/' "$ROOT/scripts/start_shared_worker.sh" "$ROOT/deploy/runpod/goliath-worker-bootstrap.sh" \
   "$ROOT/deploy/runpod/goliath-worker-start.sh"; then
  fail "boot scripts must resolve current instead of traversing \$ROOT/current/"
fi

# start_shared_worker helpers: wanted release from .release and version order.
eval "$(sed -n '/^goliath_current_dir() {/,/^resolve_tool() {/p' "$ROOT/scripts/start_shared_worker.sh" | sed '$d')"
eval "$(sed -n '/^current_is_usable() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
ROOT_SAVE="$ROOT"; ROOT="$g"
printf 'goliathomics-2026.10.12\n' >"$g/bin/.release"
[[ "$(wanted_release_version)" == "2026.10.12" ]] || fail "wanted version should come from bin/.release"
version_older 2026.10.9 2026.10.12 || fail "2026.10.9 < 2026.10.12"
if version_older 2026.10.12 2026.10.9; then fail "2026.10.12 is not older"; fi
printf '%s\n' "$g/releases/2026.10.9" >"$g/current"
if current_is_usable; then fail "current 2026.10.9 must be refreshed when 2026.10.12 is wanted"; fi
printf '%s\n' "$g/releases/2026.10.12" >"$g/current"
current_is_usable || fail "current matching the wanted release is usable"
: >"$g/current"
if current_is_usable; then fail "empty current is not usable"; fi
ROOT="$ROOT_SAVE"

# Bootstrap tag choice: highest version, not first listed.
py="$(sed -n '/RELEASE_TAG="\$(python3 - "\$META_FILE" <<PY/,/^PY$/p' "$ROOT/deploy/runpod/goliath-worker-bootstrap.sh" | sed '1d;$d')"
printf '[{"tag_name":"goliathomics-2026.10.9"},{"tag_name":"v2026.10.12"},{"tag_name":"goliathomics-2026.10.12"},{"tag_name":"goliathomics-2026.10.13","draft":true}]' >"$work/rel.json"
got="$(python3 -c "$py" "$work/rel.json")"
[[ "$got" == "goliathomics-2026.10.12" ]] || fail "bootstrap should pick goliathomics-2026.10.12, got $got"

# Promote race (worker boot 2026-10-17): promoting 2026.10.17 left current
# momentarily unresolved and the start adapter from the old 2026.10.15 bundle
# was re-run. 1) The flip is an atomic rename: a reader never sees a missing
# current. 2) The re-exec uses the release this boot promoted, by explicit
# path, not the adapter path computed from the old current.
for v in 2026.10.15 2026.10.17; do
  mkdir -p "$g/releases/$v/runtime-bundle/deploy/runpod" "$g/releases/$v/runtime-bundle/scripts"
  printf '{"version": "%s"}\n' "$v" >"$g/releases/$v/manifest.json"
  printf '#!/bin/bash\n# goliath-worker-start\necho "adapter %s release_dir=${GOLIATH_RELEASE_DIR:-}"\n' "$v" \
    >"$g/releases/$v/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  printf '#!/bin/bash\n# start_shared_worker\necho "core %s bundled=${GOLIATH_BUNDLED_START:-}"\n' "$v" \
    >"$g/releases/$v/runtime-bundle/scripts/start_shared_worker.sh"
done
grep -q 'mv -fT "\$tmp" "\$cur"' "$ROOT/scripts/detect_platform.sh" || fail "goliath_write_current must rename atomically"
goliath_write_current "$g" "$g/releases/2026.10.15" || fail "write 10.15"
( for _ in $(seq 1 300); do
    goliath_current_dir "$g" >/dev/null || { echo unresolved >"$work/race"; exit 0; }
  done ) &
reader=$!
for _ in $(seq 1 100); do
  goliath_write_current "$g" "$g/releases/2026.10.17" >/dev/null || fail "flip to 10.17"
  goliath_write_current "$g" "$g/releases/2026.10.15" >/dev/null || fail "flip to 10.15"
done
wait "$reader"
[[ ! -e "$work/race" ]] || fail "current was momentarily unresolved during a promote"
ls "$g"/.current.* >/dev/null 2>&1 && fail "temp pointer files left behind"
# Legacy symlink is replaced in one rename, without an rm gap.
rm -f "$g/current"; ln -s "$g/releases/2026.10.15" "$g/current"
goliath_write_current "$g" "$g/releases/2026.10.17" || fail "flip over symlink"
[[ ! -L "$g/current" && "$(goliath_current_dir "$g")" == "$g/releases/2026.10.17" ]] || fail "symlink not replaced"

# start_shared_worker re-exec: GOLIATH_BUNDLED_START was computed by the old
# adapter (current -> 2026.10.15) before this boot promoted 2026.10.17.
eval "$(sed -n '/^target_bundled_start() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
eval "$(sed -n '/^maybe_reexec() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
log() { :; }
goliath_write_current "$g" "$g/releases/2026.10.15" >/dev/null
got="$(
  unset GOLIATH_START_REEXECED GOLIATH_BUNDLED_START_REL
  export GOLIATH_BUNDLED_START="$g/releases/2026.10.15/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  export GOLIATH_ADAPTER_SCRIPT="$g/bin/goliath-worker-start.sh"
  # shellcheck disable=SC2034  # read by maybe_reexec
  TARGET_RELEASE="$g/releases/2026.10.17"
  maybe_reexec
)"
[[ "$got" == "adapter 2026.10.17 release_dir=$g/releases/2026.10.17" ]] \
  || fail "re-exec must run the promoted release's adapter, got: $got"
# Also when the old adapter only knew the literal \$ROOT/current/... path.
got="$(
  unset GOLIATH_START_REEXECED
  export GOLIATH_BUNDLED_START_REL=deploy/runpod/goliath-worker-start.sh
  export GOLIATH_BUNDLED_START="$g/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  export GOLIATH_ADAPTER_SCRIPT="$g/bin/goliath-worker-start.sh"
  # shellcheck disable=SC2034  # read by maybe_reexec
  TARGET_RELEASE="$g/releases/2026.10.17"
  maybe_reexec
)"
[[ "$got" == adapter\ 2026.10.17* ]] || fail "re-exec via REL must use 10.17, got: $got"

# The adapter honours the explicit GOLIATH_RELEASE_DIR over a stale current.
got="$(env -u GOLIATH_BUNDLED_START -u GOLIATH_SHARED_WORKER_SCRIPT GOLIATH_ROOT="$g" \
  GOLIATH_RELEASE_DIR="$g/releases/2026.10.17" GOLIATH_START_KEEPALIVE=/bin/true \
  bash "$ROOT/deploy/runpod/goliath-worker-start.sh")"
[[ "$got" == "core 2026.10.17 bundled=$g/releases/2026.10.17/runtime-bundle/deploy/runpod/goliath-worker-start.sh" ]] \
  || fail "adapter must use GOLIATH_RELEASE_DIR, got: $got"

# First boot of a new release (worker-1, 2026.10.18): current still names
# the previous release and the wanted one is not on the volume yet; the
# bootstrap installed the wanted release's start into $ROOT/bin. The adapter
# must run that start, not current's older one (which re-exec'd the old
# release's adapter).
goliath_write_current "$g" "$g/releases/2026.10.15" >/dev/null
mkdir -p "$g/bin"
printf '#!/bin/bash\n# start_shared_worker\necho "core bin"\n' >"$g/bin/start_shared_worker.sh"
printf 'goliathomics-2026.10.17\n' >"$g/bin/.release"
got="$(env -u GOLIATH_BUNDLED_START -u GOLIATH_SHARED_WORKER_SCRIPT -u GOLIATH_RELEASE_DIR GOLIATH_ROOT="$g" \
  GOLIATH_START_KEEPALIVE=/bin/true bash "$ROOT/deploy/runpod/goliath-worker-start.sh")"
[[ "$got" == "core bin" ]] || fail "adapter must run the bootstrap's newer start over current's, got: $got"
# $ROOT/bin from an older release than current: current's start wins.
printf 'goliathomics-2026.10.9\n' >"$g/bin/.release"
got="$(env -u GOLIATH_BUNDLED_START -u GOLIATH_SHARED_WORKER_SCRIPT -u GOLIATH_RELEASE_DIR GOLIATH_ROOT="$g" \
  GOLIATH_START_KEEPALIVE=/bin/true bash "$ROOT/deploy/runpod/goliath-worker-start.sh")"
[[ "$got" == core\ 2026.10.15* ]] || fail "older \$ROOT/bin must not win over current, got: $got"
# An explicit GOLIATH_RELEASE_DIR still wins over a newer \$ROOT/bin.
printf 'goliathomics-2026.10.99\n' >"$g/bin/.release"
got="$(env -u GOLIATH_BUNDLED_START -u GOLIATH_SHARED_WORKER_SCRIPT GOLIATH_ROOT="$g" \
  GOLIATH_RELEASE_DIR="$g/releases/2026.10.17" GOLIATH_START_KEEPALIVE=/bin/true \
  bash "$ROOT/deploy/runpod/goliath-worker-start.sh")"
[[ "$got" == core\ 2026.10.17* ]] || fail "GOLIATH_RELEASE_DIR must win, got: $got"
rm -f "$g/bin/start_shared_worker.sh" "$g/bin/.release"

# Bootstrap: scripts come from the wanted release's own directory.
eval "$(sed -n '/^cur_dir() {/,/^}/p;/^bundle_dir() {/,/^}/p' "$ROOT/deploy/runpod/goliath-worker-bootstrap.sh")"
ROOT_SAVE="$ROOT"; ROOT="$g"
# shellcheck disable=SC2034  # read by bundle_dir
WANTED_TAG=goliathomics-2026.10.17
[[ "$(bundle_dir)" == "$g/releases/2026.10.17" ]] || fail "bootstrap must use releases/2026.10.17, not current (10.15)"
# shellcheck disable=SC2034
WANTED_TAG=""
[[ "$(bundle_dir)" == "$g/releases/2026.10.15" ]] || fail "no wanted tag falls back to current"
ROOT="$ROOT_SAVE"


# Post-promote steps must use TARGET_RELEASE, not a stale $ROOT/current
# (worker boot of 2026.10.20: promoted 2026.10.20, then install_system_deps
# and worker_venv_local resolved releases/2026.10.19 because cur_dir re-read
# the pointer on a shared volume that still named the previous release).
eval "$(sed -n '/^goliath_current_dir() {/,/^resolve_tool() {/p' "$ROOT/scripts/start_shared_worker.sh" | sed '$d')"
ROOT_SAVE="$ROOT"; ROOT="$g"
# shellcheck disable=SC2034  # read by cur_dir
TARGET_RELEASE=""
goliath_write_current "$g" "$g/releases/2026.10.15" >/dev/null
[[ "$(cur_dir)" == "$g/releases/2026.10.15" ]] || fail "cur_dir falls back to current when TARGET_RELEASE is empty"
TARGET_RELEASE="$g/releases/2026.10.17"
[[ "$(cur_dir)" == "$g/releases/2026.10.17" ]] \
  || fail "cur_dir must prefer TARGET_RELEASE over stale current, got: $(cur_dir)"
# Missing TARGET_RELEASE dir falls through to the pointer.
TARGET_RELEASE="$g/releases/does-not-exist"
[[ "$(cur_dir)" == "$g/releases/2026.10.15" ]] \
  || fail "invalid TARGET_RELEASE must fall back to current, got: $(cur_dir)"
TARGET_RELEASE=""
ROOT="$ROOT_SAVE"

# ensure_current: GOLIATH_RELEASE_DIR matching the wanted release wins over a
# stale current (re-exec after promote; pointer may still name the old one).
eval "$(sed -n '/^ensure_current() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
acquire_promote_lock() { fail "ensure_current must not re-promote when GOLIATH_RELEASE_DIR matches"; }
release_promote_lock() { :; }
download_latest_release() { fail "must not download when GOLIATH_RELEASE_DIR matches"; }
log() { :; }
die() { fail "die: $*"; }
ROOT_SAVE="$ROOT"; ROOT="$g"
TARGET_RELEASE=""
export GOLIATH_RELEASE_DIR="$g/releases/2026.10.17"
printf 'goliathomics-2026.10.17\n' >"$g/bin/.release"
goliath_write_current "$g" "$g/releases/2026.10.15" >/dev/null
ensure_current
[[ "$TARGET_RELEASE" == "$g/releases/2026.10.17" ]] \
  || fail "ensure_current must keep GOLIATH_RELEASE_DIR, got: $TARGET_RELEASE"
# An older GOLIATH_RELEASE_DIR than wanted must not short-circuit (would need
# refresh). Stub only the early check: leave TARGET_RELEASE unset and expect
# current_is_usable false then the lock path — exercise by clearing the env
# and confirming a matching current still works without GOLIATH_RELEASE_DIR.
unset GOLIATH_RELEASE_DIR
TARGET_RELEASE=""
goliath_write_current "$g" "$g/releases/2026.10.17" >/dev/null
ensure_current
[[ "$TARGET_RELEASE" == "$g/releases/2026.10.17" ]] \
  || fail "ensure_current keeps matching current, got: $TARGET_RELEASE"
ROOT="$ROOT_SAVE"
unset -f acquire_promote_lock release_promote_lock download_latest_release

echo "test_current_pointer: OK"
