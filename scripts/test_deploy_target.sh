#!/usr/bin/env bash
# Dry-run contract for deploy/targets profiles. No SSH and no secret values.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

example="$ROOT/deploy/targets/goliath-test.example.json"
schema="$ROOT/deploy/targets/target.schema.json"
[[ -f "$example" ]] || fail "missing example profile"
[[ -f "$schema" ]] || fail "missing schema"
jq -e . "$example" >/dev/null
jq -e . "$schema" >/dev/null

if jq -e '[.. | strings | select(test("secret:") | not) | select(test("(?i)(password|api[_-]?key|secret_access)\\s*[:=]\\s*\\S"))] | length == 0' "$example" >/dev/null; then
  :
else
  fail "example profile appears to embed a secret value"
fi

# Every secret-looking field is a secret:NAME reference.
jq -e '
  [
    .database.password,
    .shared_storage.access_key_id,
    .shared_storage.secret_access_key,
    .long_term_storage.access_key_id,
    .long_term_storage.secret_access_key
  ] | all(test("^secret:[A-Za-z_][A-Za-z0-9_]*$"))
' "$example" >/dev/null || fail "secret fields must be secret:NAME references"

out="$(mktemp)"
err="$(mktemp)"
env -u NEON_TEST_PASSWORD -u B2_ACCESS_KEY_ID -u B2_SECRET_ACCESS_KEY \
  -u LONGTERM_ACCESS_KEY_ID -u LONGTERM_SECRET_ACCESS_KEY \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --dry-run >"$out" 2>"$err"
grep -q 'secret:NEON_TEST_PASSWORD' "$out" || fail "dry-run should list the secret name"
grep -q 'rollouts_environment=goliath-test' "$out" || fail "dry-run should show the rollouts environment"
grep -q 'gateway.example.com' "$out" || fail "dry-run should show the placeholder gateway host"
if grep -q 'NEON_TEST_PASSWORD=' "$out" "$err"; then
  fail "dry-run printed an assignment for a secret"
fi

set +e
env bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --component schema >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "real deploy of the example profile must be refused"
grep -q 'Refusing' "$err" || fail "refusal should explain why"

# Help paths stay quiet and succeed.
bash "$ROOT/scripts/report_deploy.sh" --help >/dev/null
bash "$ROOT/scripts/deploy_target.sh" --help >/dev/null
bash "$ROOT/scripts/promote_release.sh" --help >/dev/null
bash "$ROOT/scripts/install_gateway_systemd.sh" --help >/dev/null
bash "$ROOT/scripts/install_worker_systemd.sh" --help >/dev/null
bash "$ROOT/scripts/verify_setup.sh" --help >/dev/null

bed="$ROOT/workflow_engine/sql_pg/deploy_test_bed.sh"
set +e
env -u GOLIATH_ALLOW_TEST_BED_TOKEN -u GOLIATH_ENV -u GOLIATH_DEPLOY_TARGET \
  PGHOST=db.example.com PGDATABASE=goliath PGUSER=goliath \
  bash "$bed" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "test-bed fixture must be refused for a non test-bed target"
grep -q 'GOLIATH_ALLOW_TEST_BED_TOKEN' "$err" || fail "refusal should name the override"
if grep -q 'test-bed-token' "$out" "$err"; then
  fail "refusal printed the fixture token"
fi

assert_past_guard() {
  local label="$1"
  shift
  set +e
  env -u GOLIATH_ALLOW_TEST_BED_TOKEN "$@" \
    PGCONNECT_TIMEOUT=1 PGHOST=127.0.0.1 PGPORT=1 PGDATABASE=goliath PGUSER=goliath \
    bash "$bed" >"$out" 2>"$err"
  status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "$label still needs a database"
  if grep -q 'Refusing to apply the test-bed token' "$err"; then
    fail "$label should pass the token guard"
  fi
}
assert_past_guard "explicit override" GOLIATH_ALLOW_TEST_BED_TOKEN=1
assert_past_guard "test-bed environment" GOLIATH_ENV=goliath-test

echo "test_deploy_target ok"
