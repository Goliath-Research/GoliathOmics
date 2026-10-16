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
         deploy/runpod/goliath-worker-start.sh scripts/report_deploy.sh \
         scripts/deploy_workflow_definitions.sh scripts/deploy_mc_workflow_definitions.sh; do
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

echo "test_current_pointer: OK"
