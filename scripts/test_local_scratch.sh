#!/usr/bin/env bash
# Local scratch preflight: free space on METHYL_LOCAL_TMP, never on /work.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/check_local_scratch.sh"
START="$ROOT/scripts/start_shared_worker.sh"
PROVISION="$ROOT/scripts/provision_worker_node.sh"
fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -x "$CHECK" || -f "$CHECK" ]] || fail "missing $CHECK"
grep -q 'METHYL_LOCAL_TMP:-/tmp/methyl-pb' "$CHECK" || fail "default scratch root must be /tmp/methyl-pb"
grep -q 'METHYL_LOCAL_TMP_MIN_GB:-80' "$CHECK" || fail "default free-space floor must be 80 GB"
grep -q 'check_local_scratch.sh' "$START" || fail "start_shared_worker.sh must run the scratch preflight"
grep -q 'check_local_scratch.sh' "$PROVISION" || fail "provision_worker_node.sh must run the scratch preflight"
grep -q 'does not skip this check' "$PROVISION" || fail "provision usage must say --skip-preflight does not skip the scratch check"
awk '
  /^main\(\) \{/ { in_main = 1 }
  in_main && /require_local_scratch/ { seen = 1 }
  in_main && seen && /wait_for_work/ { ok = 1 }
  END { exit ok ? 0 : 1 }
' "$START" || fail "start must check local scratch before waiting for the work mount"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/work"

run_check() {
  local name="$1"; shift
  set +e
  env "$@" bash "$CHECK" >"$work/$name.out" 2>"$work/$name.err"
  echo $? >"$work/$name.rc"
  set -e
}

# Enough space: directory is created and the success line names it.
run_check ok \
  METHYL_LOCAL_TMP="$work/scratch" \
  METHYL_LOCAL_TMP_MIN_GB=0 \
  GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/ok.rc")" == 0 ]] || { cat "$work/ok.err"; fail "zero minimum should pass"; }
[[ -d "$work/scratch" ]] || fail "preflight should create the scratch root"
grep -q "local scratch $work/scratch has " "$work/ok.out" || { cat "$work/ok.out"; fail "success line"; }
grep -q 'minimum 0 GB' "$work/ok.out" || fail "success line should name the minimum"

# Below the floor: clear error, no partial success line.
run_check low \
  METHYL_LOCAL_TMP="$work/scratch" \
  METHYL_LOCAL_TMP_MIN_GB=1000000 \
  GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/low.rc")" != 0 ]] || fail "a huge minimum must fail"
grep -q "local scratch $work/scratch has " "$work/low.err" || { cat "$work/low.err"; fail "low-space error should name the scratch path"; }
grep -q 'below the minimum of 1000000 GB' "$work/low.err" || { cat "$work/low.err"; fail "low-space error should name the minimum"; }
grep -q 'local_disk_gb' "$work/low.err" || fail "low-space error should point at local_disk_gb"
[[ ! -s "$work/low.out" ]] || fail "failure must not print a success line"

# Shared mount, including a symlink that lands on it, is refused before create.
run_check onwork \
  METHYL_LOCAL_TMP="$work/work/scratch" \
  METHYL_LOCAL_TMP_MIN_GB=0 \
  GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/onwork.rc")" != 0 ]] || fail "scratch on the work mount must fail"
grep -q 'shared work mount' "$work/onwork.err" || { cat "$work/onwork.err"; fail "work-mount error"; }
[[ ! -e "$work/work/scratch" ]] || fail "refused scratch path must not be created"

mkdir -p "$work/work/inside"
ln -s "$work/work/inside" "$work/linkscratch"
run_check link \
  METHYL_LOCAL_TMP="$work/linkscratch" \
  METHYL_LOCAL_TMP_MIN_GB=0 \
  GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/link.rc")" != 0 ]] || fail "a symlink onto the work mount must fail"
grep -q 'shared work mount' "$work/link.err" || fail "symlink error should name the work mount"

run_check rel METHYL_LOCAL_TMP="relative/scratch" METHYL_LOCAL_TMP_MIN_GB=0 GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/rel.rc")" != 0 ]] && grep -q 'absolute path' "$work/rel.err" || fail "relative scratch path must fail"

run_check badmin METHYL_LOCAL_TMP="$work/scratch" METHYL_LOCAL_TMP_MIN_GB=80gb GOLIATH_WORK_MOUNT="$work/work"
[[ "$(cat "$work/badmin.rc")" != 0 ]] && grep -q 'non-negative integer' "$work/badmin.err" || fail "bad minimum must fail"

# start_shared_worker refuses before it waits on /work.
mkdir -p "$work/bootwork"
set +e
timeout 20 env -u GH_TOKEN -u GITHUB_TOKEN \
  METHYL_LOCAL_TMP="$work/bootscratch" \
  METHYL_LOCAL_TMP_MIN_GB=1000000 \
  GOLIATH_WORK_MOUNT="$work/bootwork" \
  GOLIATH_ROOT="$work/bootwork/goliath" \
  GOLIATH_START_NO_EXEC=1 \
  bash "$START" >"$work/start.out" 2>"$work/start.err"
start_rc=$?
set -e
[[ "$start_rc" != 0 ]] || { cat "$work/start.out" "$work/start.err"; fail "start must refuse a tiny scratch disk"; }
grep -q 'below the minimum of 1000000 GB' "$work/start.err" || { cat "$work/start.err"; fail "start should surface the scratch error"; }
grep -q 'local scratch preflight:' "$work/start.out" || fail "start should log the preflight"
if grep -q 'persistent volume' "$work/start.err"; then
  fail "scratch preflight must run before the work-mount wait"
fi

# provision refuses even with --skip-preflight. --dry-run only prints the check.
set +e
timeout 20 env \
  METHYL_LOCAL_TMP="$work/provscratch" \
  METHYL_LOCAL_TMP_MIN_GB=1000000 \
  GOLIATH_WORK_MOUNT="$work/work" \
  bash "$PROVISION" --join-mode join --skip-preflight --skip-host-setup --skip-verify \
    --root "$work/root" >"$work/prov.out" 2>"$work/prov.err"
prov_rc=$?
set -e
[[ "$prov_rc" != 0 ]] || { cat "$work/prov.out" "$work/prov.err"; fail "provision must refuse a tiny scratch disk"; }
grep -q 'below the minimum of 1000000 GB' "$work/prov.err" || { cat "$work/prov.err"; fail "provision should surface the scratch error"; }
grep -q 'Local scratch' "$work/prov.out" || fail "provision should announce the scratch check"
if grep -q 'Enroll worker' "$work/prov.out"; then
  fail "provision must not enroll when scratch is too small"
fi

mkdir -p "$work/dryroot"
set +e
timeout 20 env \
  METHYL_LOCAL_TMP="$work/dryscratch" \
  METHYL_LOCAL_TMP_MIN_GB=1000000 \
  GOLIATH_WORK_MOUNT="$work/work" \
  bash "$PROVISION" --dry-run --join-mode join --skip-host-setup --skip-verify \
    --root "$work/dryroot" >"$work/dry.out" 2>"$work/dry.err"
dry_rc=$?
set -e
grep -q 'check_local_scratch.sh' "$work/dry.out" || { cat "$work/dry.out" "$work/dry.err"; fail "dry-run should print the scratch check"; }
grep -q '\[DRY-RUN\]' "$work/dry.out" || fail "dry-run should not execute the scratch check"
if grep -q 'below the minimum' "$work/dry.err"; then
  fail "dry-run must not apply the free-space floor"
fi
# Dry-run still plans later steps; it must not die inside the scratch check.
[[ "$dry_rc" != 124 ]] || fail "provision dry-run hung"

echo "PASS: local scratch preflight"
