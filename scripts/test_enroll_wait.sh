#!/bin/bash
# provision_worker_node.sh do_enroll waits (logging once) while the portal
# preregistration row is missing, then continues; other errors still fail.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cat >"$work/fake_py" <<'EOF'
#!/bin/bash
n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$COUNTER"
if [[ "$n" -le "${FAIL_TIMES:-0}" ]]; then
  echo "RuntimeError: HTTP 403 /v1/workers/enroll: enroll refused (portal IP/key not preregistered). ${MODE:-}" >&2
  exit 1
fi
[[ "${OTHER_ERROR:-0}" == 1 ]] && { echo "RuntimeError: HTTP 500 boom" >&2; exit 1; }
echo enrolled
EOF
chmod +x "$work/fake_py"

run_enroll() {
  (
    set -euo pipefail
    die() { echo "[ERROR] $*" >&2; exit 1; }
    run() { "$@"; }
    resolve_venv_python() { echo "$work/fake_py"; }
    worker_venv_local_dir() { echo "$work/venv"; }
    eval "$(sed -n '/^ensure_tmpdirs() {/,/^}/p;/^do_enroll() {/,/^}/p;/^enroll_public_ip() {/,/^}/p;/^enroll_registration_sql() {/,/^}/p' "$ROOT/scripts/provision_worker_node.sh")"
    # shellcheck disable=SC2034
    DRY_RUN=0 REQUIRE_ARC=0 CAPABILITY="" CLUSTER=goliath ROOT="$work" REPO_ROOT="$ROOT" ARCH=amd64
    export WORKER_API_BASE=https://gw.example/v1
    do_enroll
  )
}

export COUNTER="$work/n" GOLIATH_ENROLL_RETRY_SECS=1 GOLIATH_PUBLIC_IP=203.0.113.7
FAIL_TIMES=2 run_enroll >"$work/out" 2>&1 || { cat "$work/out"; fail "enroll should succeed after waiting"; }
[[ "$(grep -c '^\[enroll\] waiting for portal registration: cluster=goliath key=.* public_ip=203.0.113.7$' "$work/out")" == 1 ]] \
  || { cat "$work/out"; fail "waiting message should be logged exactly once"; }
grep -q 'enrolled' "$work/out" || fail "enroll output missing"
grep -qF "psql \"\$DATABASE_URL\" -c \"SELECT * FROM portal.sp_upsert_worker_enrollment('goliath', '203.0.113.7', '$(hostname -s)');\"" "$work/out" \
  || { cat "$work/out"; fail "ready-to-run psql preregistration missing"; }
[[ "$(grep -c 'sp_upsert_worker_enrollment(' "$work/out")" == 1 ]] || fail "SQL should be logged once"
grep -q 'gw.example' "$work/out" && fail "log must not include connection details"
[[ "$(cat "$COUNTER")" == 3 ]] || fail "expected 3 attempts"

rm -f "$COUNTER"
if OTHER_ERROR=1 run_enroll >"$work/out" 2>&1; then fail "other enroll errors must fail"; fi
grep -q 'waiting for portal' "$work/out" && fail "other errors must not wait"
grep -q 'enroll failed' "$work/out" || fail "failure should be reported"

rm -f "$COUNTER"
if FAIL_TIMES=100 GOLIATH_ENROLL_MAX_WAIT_SECS=2 run_enroll >"$work/out" 2>&1; then fail "max wait must stop"; fi
grep -q 'no portal registration after' "$work/out" || { cat "$work/out"; fail "max wait message missing"; }

# worker.env names a TMPDIR that does not exist on a fresh pod (the 2026.10.15
# failure: mktemp under /var/tmp/methyl-samtools before the wait loop). It is
# created before mktemp, and the wait loop still runs.
rm -f "$COUNTER"
mkdir -p "$work/env"
printf 'TMPDIR=%s\n' "$work/fresh/methyl-samtools" >"$work/env/worker.env"
METHYL_SAMTOOLS_TMPDIR="$work/fresh/methyl-samtools" FAIL_TIMES=1 run_enroll >"$work/out" 2>&1 \
  || { cat "$work/out"; fail "enroll must create TMPDIR from worker.env before mktemp"; }
[[ -d "$work/fresh/methyl-samtools" ]] || fail "TMPDIR from worker.env was not created"
grep -q 'waiting for portal registration' "$work/out" || fail "wait loop should run after the TMPDIR fix"
# An uncreatable TMPDIR falls back to /tmp instead of failing.
rm -f "$COUNTER"
: >"$work/blocker"
printf 'TMPDIR=%s\n' "$work/blocker/sub" >"$work/env/worker.env"
METHYL_SAMTOOLS_TMPDIR="$work/st" run_enroll >"$work/out" 2>&1 || { cat "$work/out"; fail "unusable TMPDIR should fall back to /tmp"; }
grep -q 'is unusable; using /tmp' "$work/out" || fail "fallback should be logged"
rm -f "$work/env/worker.env"

# start_shared_worker creates temp dirs at start and again after writing
# worker.env, before provision (enroll) runs.
m="$(sed -n '/^main() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
[[ "$(head -3 <<<"$m" | grep -c ensure_tmpdirs)" == 1 ]] || fail "main must create temp dirs first"
awk '/write_worker_env$/{w=1} w&&/ensure_tmpdirs/{e=1} e&&/run_provision/{ok=1} END{exit ok?0:1}' <<<"$m" \
  || fail "ensure_tmpdirs must run after write_worker_env and before run_provision"
grep -q '^ensure_tmpdirs$' "$ROOT/scripts/provision_worker_node.sh" || fail "provision must create temp dirs at start"

echo "test_enroll_wait: OK"
