#!/usr/bin/env bash
# Dry-run contract for deploy/targets profiles. No SSH and no secret values.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

example="$ROOT/deploy/targets/goliath-test.example.json"
real="$ROOT/deploy/targets/goliath-test.json"
schema="$ROOT/deploy/targets/target.schema.json"
[[ -f "$example" ]] || fail "missing example profile"
[[ -f "$real" ]] || fail "missing goliath-test profile"
[[ -f "$schema" ]] || fail "missing schema"
start_script="$ROOT/deploy/runpod/goliath-worker-start.sh"
[[ -f "$start_script" ]] || fail "missing RunPod start script"

real_backup=""
hide_real_profile() {
  if [[ -f "$real" ]]; then
    real_backup="$(mktemp)"
    mv "$real" "$real_backup"
  fi
}
restore_real_profile() {
  if [[ -n "$real_backup" && -f "$real_backup" ]]; then
    mv "$real_backup" "$real"
    real_backup=""
  fi
}
trap 'restore_real_profile' EXIT
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
# The real profile wins over the example. Hide it so the example contract stays covered.
hide_real_profile
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
restore_real_profile

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

if ! python3 -c 'import jsonschema' >/dev/null 2>&1; then
  python3 -m pip install -q --user 'jsonschema==4.23.0' \
    || python3 -m pip install -q 'jsonschema==4.23.0'
fi
python3 - "$ROOT" <<'PY' || fail "profile does not match target.schema.json"
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

root = Path(sys.argv[1])
schema = json.loads((root / "deploy/targets/target.schema.json").read_text(encoding="utf-8"))
validator = Draft7Validator(schema)
for name in ("goliath-test.json", "goliath-test.example.json"):
    instance = json.loads((root / "deploy/targets" / name).read_text(encoding="utf-8"))
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.path))
    if errors:
        for err in errors:
            print(f"{name}: {list(err.path)}: {err.message}", file=sys.stderr)
        sys.exit(1)
PY

if jq -e '[.. | strings | select(test("secret:") | not) | select(test("(?i)(password|api[_-]?key|secret_access|BEGIN .*PRIVATE KEY)\\s*[:=]\\s*\\S"))] | length == 0' "$real" >/dev/null; then
  :
else
  fail "goliath-test profile appears to embed a secret value"
fi
jq -e '
  [
    .database.password,
    .gateway.hosts[0].identity_file,
    .shared_storage.access_key_id,
    .shared_storage.secret_access_key,
    .long_term_storage.access_key_id,
    .long_term_storage.secret_access_key
  ] | all(test("^secret:[A-Za-z_][A-Za-z0-9_]*$"))
' "$real" >/dev/null || fail "goliath-test secret fields must be secret:NAME references"
jq -e '.workers[0].self_provision == true and .workers[0].ssh == null' "$real" >/dev/null \
  || fail "goliath-test worker must be self-provision and have no ssh host"
jq -e '.database.dbname == "GoliathOmics"' "$real" >/dev/null \
  || fail "goliath-test database must be GoliathOmics"

env -u NEON_TEST_PASSWORD -u B2_ACCESS_KEY_ID -u B2_SECRET_ACCESS_KEY \
  -u LONGTERM_ACCESS_KEY_ID -u LONGTERM_SECRET_ACCESS_KEY -u GOLIATH_DEPLOY_SSH_KEY \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --dry-run >"$out" 2>"$err"
grep -q 'secret:GOLIATH_DEPLOY_SSH_KEY' "$out" || fail "dry-run should list the ssh key secret name"
grep -q 'secret:NEON_TEST_PASSWORD' "$out" || fail "dry-run should list the neon secret"
grep -q '45.76.21.8' "$out" || fail "dry-run should show the gateway address"
grep -q 'ep-wandering-pine-a5l8s690-pooler.us-east-2.aws.neon.tech' "$out" || fail "dry-run should show the pooled host"
grep -q 'schema_host=ep-wandering-pine-a5l8s690.us-east-2.aws.neon.tech' "$out" || fail "dry-run should show schema_host"
grep -q 'channel_binding=require' "$out" || fail "dry-run should show channel_binding"
grep -q 'self_provision runpod amd64 runpod-torch-v280' "$out" || fail "dry-run should describe the RunPod pool"
grep -q 'TODO-set-b2-bucket' "$out" || fail "dry-run should show the B2 bucket placeholder"
grep -q 'TODO-set-myqnapcloud-endpoint' "$out" || fail "dry-run should show the myQNAPCloud placeholder"
grep -q 'PasswordAuthentication=no' "$out" || fail "dry-run should require key-only ssh"
if grep -q 'NEON_TEST_PASSWORD=' "$out" "$err"; then
  fail "dry-run printed an assignment for a secret"
fi
if grep -q 'Using example profile' "$out" "$err"; then
  fail "real profile should be preferred over the example"
fi

ssh_log="$(mktemp)"
ssh_dir="$(mktemp -d)"
cat >"$ssh_dir/ssh" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$ssh_log"
args=("\$@")
i=0
while [[ \$i -lt \${#args[@]} ]]; do
  if [[ "\${args[\$i]}" == "-i" ]]; then
    ident="\${args[\$((i + 1))]}"
    stat -c %a "\$ident" >"$ssh_dir/ident-mode"
    cp "\$ident" "$ssh_dir/copied-identity"
  fi
  i=\$((i + 1))
done
cat >/dev/null
exit 0
EOF
chmod +x "$ssh_dir/ssh"

env -u NEON_TEST_PASSWORD -u B2_ACCESS_KEY_ID -u B2_SECRET_ACCESS_KEY \
  -u LONGTERM_ACCESS_KEY_ID -u LONGTERM_SECRET_ACCESS_KEY -u GOLIATH_DEPLOY_SSH_KEY \
  PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --component workers >"$out" 2>"$err"
grep -q 'self-provision worker goliath-worker-1' "$out" || fail "workers component should describe the pool"
grep -q 'did not SSH' "$out" || fail "workers component should say it did not SSH"
grep -q 'RTX 6000 Ada' "$out" || fail "workers component should print the GPU spec"
grep -q 'deploy/runpod/goliath-worker-start.sh' "$out" || fail "workers component should name the start script"
if [[ -s "$ssh_log" ]]; then
  fail "workers component invoked ssh"
fi

env PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --component runtime >"$out" 2>"$err"
grep -q 'skipping SSH promote' "$out" || fail "runtime component should skip SSH promote"
if [[ -s "$ssh_log" ]]; then
  fail "runtime component invoked ssh"
fi

fixture_dir="$(mktemp -d)"
# Profiles must live next to the schema path the driver opens.
all_fixture="$ROOT/deploy/targets/self-provision-ci.json"
schema_fixture="$ROOT/deploy/targets/schema-host-ci.json"
ssh_fixture="$ROOT/deploy/targets/ssh-key-ci.json"
cleanup_fixtures() {
  rm -f "$all_fixture" "$schema_fixture" "$ssh_fixture"
  restore_real_profile
  if [[ -n "${supervisor_pid:-}" ]]; then
    local pgid
    pgid="$(ps -o pgid= -p "$supervisor_pid" 2>/dev/null | tr -d ' ' || true)"
    if [[ -n "$pgid" && "$pgid" == "$supervisor_pid" ]]; then
      kill -- "-${supervisor_pid}" >/dev/null 2>&1 || true
    else
      kill "$supervisor_pid" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup_fixtures EXIT

cat >"$all_fixture" <<'EOF'
{
  "name": "self-provision-ci",
  "rollouts_environment": "goliath-test",
  "database": {
    "kind": "neon",
    "host": "pooler.example",
    "schema_host": "direct.example",
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": "secret:NEON_TEST_PASSWORD",
    "sslmode": "require",
    "channel_binding": "require"
  },
  "schema": {"apply": "none"},
  "workers": [
    {
      "name": "goliath-worker-1",
      "self_provision": true,
      "provider": "runpod",
      "arch": "amd64",
      "gpu": {"model": "RTX 6000 Ada", "count": 1, "memory_gb": 48},
      "vcpu": 16,
      "memory_gb": 62,
      "volume_mount": "/work",
      "template": "runpod-torch-v280",
      "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
      "install_root": "/work/goliath"
    }
  ]
}
EOF
: >"$ssh_log"
env -u NEON_TEST_PASSWORD -u GOLIATH_DEPLOY_SSH_KEY PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target self-provision-ci --component all >"$out" 2>"$err"
grep -q 'schema.apply=none' "$out" || fail "all should skip schema when apply is none"
grep -q 'no gateway hosts' "$out" || fail "all should skip a missing gateway"
grep -q 'did not SSH' "$out" || fail "all should handle a self-provision worker without SSH"
if [[ -s "$ssh_log" ]]; then
  fail "all component invoked ssh for a self-provision target"
fi

cat >"$schema_fixture" <<'EOF'
{
  "name": "schema-host-ci",
  "rollouts_environment": "goliath-test",
  "database": {
    "kind": "neon",
    "host": "pooler.example",
    "schema_host": "direct.example",
    "port": 5432,
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": "secret:NEON_TEST_PASSWORD",
    "sslmode": "require",
    "channel_binding": "require"
  },
  "schema": {"apply": "test-bed"}
}
EOF
psql_log="$(mktemp)"
cat >"$ssh_dir/psql" <<EOF
#!/bin/bash
printf 'PGHOST=%s\nPGCHANNELBINDING=%s\nPGSSLMODE=%s\n' "\${PGHOST:-}" "\${PGCHANNELBINDING:-}" "\${PGSSLMODE:-}" >>"$psql_log"
exit 0
EOF
chmod +x "$ssh_dir/psql"
env -u CURSOR_API_KEY NEON_TEST_PASSWORD='neon-secret-value' PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target schema-host-ci --component schema >"$out" 2>"$err"
grep -q 'PGHOST=direct.example' "$psql_log" || fail "schema apply should use schema_host"
grep -q 'PGCHANNELBINDING=require' "$psql_log" || fail "schema apply should set channel_binding"
if grep -q 'pooler.example' "$psql_log"; then
  fail "schema apply used the pooled host"
fi
if grep -q 'neon-secret-value' "$out" "$err" "$psql_log"; then
  fail "schema apply printed the database password"
fi
if grep -q 'test-bed-token' "$out" "$err" "$psql_log"; then
  fail "schema apply printed the fixture token"
fi

cat >"$ssh_fixture" <<'EOF'
{
  "name": "ssh-key-ci",
  "rollouts_environment": "goliath-test",
  "database": {
    "kind": "neon",
    "host": "pooler.example",
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": "secret:NEON_TEST_PASSWORD",
    "sslmode": "require",
    "channel_binding": "require"
  },
  "schema": {"apply": "none"},
  "gateway": {
    "install_root": "/opt/methyl-gateway",
    "hosts": [
      {
        "name": "goliath-gateway",
        "ssh": "linuxuser@127.0.0.1",
        "identity_file": "secret:GOLIATH_DEPLOY_SSH_KEY"
      }
    ]
  }
}
EOF
: >"$ssh_log"
fake_key=$'-----BEGIN OPENSSH PRIVATE KEY-----\nTESTKEYMATERIAL\n-----END OPENSSH PRIVATE KEY-----'
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_DEPLOY_SSH_KEY="$fake_key" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target ssh-key-ci --component gateway >"$out" 2>"$err"
grep -q 'PasswordAuthentication=no' "$ssh_log" || fail "ssh client should disable password auth"
grep -q 'IdentitiesOnly=yes' "$ssh_log" || fail "ssh client should use only the provided key"
[[ -f "$ssh_dir/copied-identity" ]] || fail "ssh client did not receive an identity file"
[[ "$(cat "$ssh_dir/ident-mode")" == "600" ]] || fail "identity file must be mode 600"
grep -q 'TESTKEYMATERIAL' "$ssh_dir/copied-identity" || fail "identity file should contain the key"
if grep -q 'TESTKEYMATERIAL' "$out" "$err" "$ssh_log"; then
  fail "deploy printed the ssh private key"
fi
if grep -q 'neon-secret-value' "$out" "$err" "$ssh_log"; then
  fail "deploy printed the database password"
fi

command -v shellcheck >/dev/null 2>&1 || fail "shellcheck is required"
shellcheck -S warning -e SC1091 "$start_script"

work_root="$(mktemp -d)"
token_file="$work_root/worker-token"
prov_log="$(mktemp)"
report_log="$(mktemp)"
cat >"$work_root/provision" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$prov_log"
mkdir -p "\$(dirname "$token_file")"
printf 'WORKER_ID=7\nWORKER_TOKEN=minted-token\n' >"$token_file"
chmod 600 "$token_file"
exit 0
EOF
cat >"$work_root/report" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$report_log"
exit 0
EOF
cat >"$work_root/nvidia-smi" <<'EOF'
#!/bin/bash
echo "NVIDIA RTX 6000 Ada Generation"
EOF
chmod +x "$work_root/provision" "$work_root/report" "$work_root/nvidia-smi"
mkdir -p "$work_root/goliath/current" "$work_root/goliath/venv-amd64/bin"
cat >"$work_root/goliath/venv-amd64/bin/methyl-worker" <<'EOF'
#!/bin/bash
dump="${METHYL_WORKER_ENV_DUMP:-}"
if [[ -n "$dump" && ! -f "$dump" ]]; then
  {
    printf 'METHYL_EXTRACTOR_BIN=%s\n' "${METHYL_EXTRACTOR_BIN-}"
    printf 'METHYL_PARABRICKS_IMAGE=%s\n' "${METHYL_PARABRICKS_IMAGE-}"
    printf 'METHYL_SAMPLE_CAAS_ENABLED=%s\n' "${METHYL_SAMPLE_CAAS_ENABLED-}"
    printf 'PATH=%s\n' "${PATH-}"
  } >"$dump"
fi
sleep 300
EOF
chmod +x "$work_root/goliath/venv-amd64/bin/methyl-worker"
mkdir -p "$work_root/goliath/env"
cat >"$work_root/goliath/env/worker.env" <<'EOF'
# Generated by scripts/write_worker_env.sh (release 1.2.3)
GOLIATH_ROOT=/work/goliath
METHYL_EXTRACTOR_BIN=/work/goliath/methyl-extractor-amd64/bin/MethylExtractor
HDF5_PLUGIN_PATH=/work/goliath/methyl-extractor-amd64/lib/hdf5_zstd_plugin
PATH=/opt/goliath/venv/bin:/usr/bin
METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1
METHYL_SAMPLE_CAAS_ENABLED=0
METHYL_GPU_HBM_FREE_FRACTION=0.90
POSTGRES_PASSWORD=old-neon
EOF
chmod 600 "$work_root/goliath/env/worker.env"

run_start() {
  env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
    GOLIATH_ROOT="$work_root/goliath" \
    GOLIATH_WORK_MOUNT="$work_root" \
    GOLIATH_LOG_DIR="$work_root/goliath/logs" \
    GOLIATH_START_NO_EXEC=1 \
    GOLIATH_PROVISION_SCRIPT="$work_root/provision" \
    GOLIATH_REPORT_SCRIPT="$work_root/report" \
    METHYL_WORKER_TOKEN_FILE="$token_file" \
    WORKER_API_BASE="https://45.76.21.8/v1" \
    RUNPOD_POD_ID="pod-test-1" \
    GOLIATH_WORKER_TOKEN="sekret-token" \
    B2_SECRET_ACCESS_KEY="b2-sekret" \
    CURSOR_API_KEY="cursor-sekret" \
    NEON_TEST_PASSWORD="neon-sekret" \
    METHYL_WORKER_ENV_DUMP="$work_root/methyl-worker-env" \
    PATH="$work_root:$PATH" \
    bash "$start_script" >"$out" 2>"$err"
}

run_start
grep -q -- '--join-mode' "$prov_log" || fail "start script should pass --join-mode"
grep -q -- '--arch' "$prov_log" || fail "start script should pass --arch"
grep -q 'amd64' "$prov_log" || fail "start script should pass arch amd64"
grep -q -- '--gpu' "$prov_log" || fail "start script should pass --gpu"
if grep -q -- '--capability' "$prov_log"; then
  fail "start script should let enroll probe GPU capability instead of passing the product string"
fi
grep -q 'RUNPOD_POD_ID=pod-test-1' "$report_log" || fail "report should include RUNPOD_POD_ID"
grep -q 'arch=amd64' "$report_log" || fail "report should include arch"
grep -q 'GPU=NVIDIA RTX 6000 Ada Generation' "$report_log" || fail "report should include the nvidia-smi GPU name"
grep -q 'host=' "$report_log" || fail "report should include host"
grep -q 'GPU detected via nvidia-smi' "$out" || fail "start script should log nvidia-smi detection"
[[ "$(stat -c %a "$token_file")" == "600" ]] || fail "worker token file must be mode 600"
grep -q '^WORKER_ID=7$' "$token_file" || fail "enroll token should be kept"
supervisor_pid="$(cat "$work_root/goliath/logs/methyl-worker.supervisor.pid")"
kill -0 "$supervisor_pid" || fail "worker supervisor should be running"
prov_lines="$(wc -l <"$prov_log" | tr -d ' ')"
run_start
prov_lines_after="$(wc -l <"$prov_log" | tr -d ' ')"
[[ "$prov_lines_after" == "$prov_lines" ]] || fail "second boot should skip provision"
if grep -q 'sekret-token' "$out" "$err" "$report_log" "$prov_log"; then
  fail "start script printed the worker token"
fi
if grep -q 'b2-sekret' "$out" "$err" "$report_log"; then
  fail "start script printed the B2 secret"
fi
if grep -q 'cursor-sekret' "$out" "$err" "$report_log"; then
  fail "start script printed CURSOR_API_KEY"
fi
if grep -q 'neon-sekret' "$out" "$err"; then
  fail "start script printed the Neon password"
fi
# The worker does not need the database password unless asked.
if grep -q 'POSTGRES_PASSWORD' "$work_root/goliath/env/worker.env"; then
  fail "worker.env should not contain the Neon password by default"
fi
worker_env="$work_root/goliath/env/worker.env"
[[ "$(stat -c %a "$worker_env")" == "600" ]] || fail "worker.env must be mode 600"
grep -q '^METHYL_EXTRACTOR_BIN=/work/goliath/methyl-extractor-amd64/bin/MethylExtractor$' "$worker_env" \
  || fail "promote METHYL_EXTRACTOR_BIN was dropped from worker.env"
grep -q '^METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1$' "$worker_env" \
  || fail "promote METHYL_PARABRICKS_IMAGE was dropped from worker.env"
grep -q '^PATH=/opt/goliath/venv/bin:/usr/bin$' "$worker_env" \
  || fail "promote PATH was dropped from worker.env"
grep -q '^METHYL_SAMPLE_CAAS_ENABLED=0$' "$worker_env" \
  || fail "promote METHYL_SAMPLE_CAAS_ENABLED was dropped from worker.env"
grep -q '^METHYL_GPU_HBM_FREE_FRACTION=0.90$' "$worker_env" \
  || fail "promote HBM setting was dropped from worker.env"
grep -q '^HDF5_PLUGIN_PATH=/work/goliath/methyl-extractor-amd64/lib/hdf5_zstd_plugin$' "$worker_env" \
  || fail "promote HDF5_PLUGIN_PATH was dropped from worker.env"
extractor_lines="$(grep -c '^METHYL_EXTRACTOR_BIN=' "$worker_env" || true)"
[[ "$extractor_lines" == "1" ]] || fail "METHYL_EXTRACTOR_BIN should appear once"
grep -q '^AWS_SECRET_ACCESS_KEY="b2-sekret"$' "$worker_env" \
  || fail "worker.env should still receive storage keys"
seen="$work_root/methyl-worker-env"
for ((i = 0; i < 50; i++)); do
  [[ -s "$seen" ]] && break
  sleep 0.1
done
[[ -s "$seen" ]] || fail "methyl-worker did not record its environment"
grep -q '^METHYL_EXTRACTOR_BIN=/work/goliath/methyl-extractor-amd64/bin/MethylExtractor$' "$seen" \
  || fail "supervisor did not load METHYL_EXTRACTOR_BIN"
grep -q '^METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1$' "$seen" \
  || fail "supervisor did not load METHYL_PARABRICKS_IMAGE"
grep -q '^METHYL_SAMPLE_CAAS_ENABLED=0$' "$seen" \
  || fail "supervisor did not load METHYL_SAMPLE_CAAS_ENABLED"
grep -q '^PATH=/opt/goliath/venv/bin:/usr/bin$' "$seen" \
  || fail "supervisor did not load PATH from worker.env"

empty_root="$(mktemp -d)"
set +e
env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
  GOLIATH_WORKER_TOKEN="sekret-token" \
  GOLIATH_ROOT="$empty_root/goliath" \
  GOLIATH_WORK_MOUNT="$empty_root" \
  GOLIATH_START_NO_EXEC=1 \
  PATH="$work_root:$PATH" \
  bash "$start_script" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "missing current and missing GitHub token should fail"
grep -q 'GOLIATH_RELEASE_URL' "$err" || fail "missing release should name GOLIATH_RELEASE_URL"
if grep -q 'sekret-token' "$out" "$err"; then
  fail "failed bootstrap printed the worker token"
fi

prov_root="$(mktemp -d)"
mkdir -p "$prov_root/venv-amd64/bin" "$prov_root/current"
printf '#!/bin/sh\nexit 0\n' >"$prov_root/venv-amd64/bin/python"
chmod +x "$prov_root/venv-amd64/bin/python"
env WORKER_API_BASE="https://45.76.21.8/v1" \
  bash "$ROOT/scripts/provision_worker_node.sh" \
  --dry-run --join-mode join --arch amd64 --gpu --skip-preflight \
  --skip-host-setup --skip-verify --enroll-worker \
  --root "$prov_root" >"$out" 2>"$err"
grep -q 'Skipping host and Docker setup' "$out" || fail "skip-host-setup should be visible"
grep -q 'Skipping verify_e2e_node.sh' "$out" || fail "skip-verify should be visible"
if grep -q 'setup_host.sh' "$out" "$err"; then
  fail "skip-host-setup still planned setup_host.sh"
fi
if grep -E 'verify_e2e_node.sh' "$out" "$err" | grep -v 'Skipping verify_e2e_node.sh' | grep -q .; then
  fail "skip-verify still planned verify_e2e_node.sh"
fi
grep -q 'methyl_worker' "$out" || fail "dry-run join should still enroll"

rm -rf "$fixture_dir"
echo "test_deploy_target ok"
