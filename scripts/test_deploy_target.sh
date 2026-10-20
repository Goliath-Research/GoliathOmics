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
# These boots run on the test machine. The 80 GB floor is covered by
# scripts/test_local_scratch.sh. Here the preflight only has to pass.
export METHYL_LOCAL_TMP_MIN_GB=1

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
api_pid=""
cleanup() {
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
    api_pid=""
  fi
  restore_real_profile
}
trap cleanup EXIT
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
bash "$ROOT/scripts/install_gateway_venv.sh" --help >/dev/null
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

# Workers merge API keys into the file methyl-worker.service loads. A dash
# login shell must not look like "file missing" and replace extractor settings.
# A later promote must keep the AWS keys that merge wrote.
assert_worker_env_merge() {
  local stage remote envfile overlay arch me_bin merged_keys
  stage="$(mktemp -d)"
  remote="$(mktemp -d)"
  chmod 700 "$stage"
  envfile="$remote/env/worker.env"
  mkdir -p "$(dirname "$envfile")" "$remote/tmp"
  cat >"$envfile" <<'EOF'
# Generated by scripts/write_worker_env.sh (release 9.9.9-test)
GOLIATH_ROOT=/work/goliath
METHYL_EXTRACTOR_BIN=/opt/goliath/methyl-extractor-amd64/bin/MethylExtractor
METHYL_PROFILE_DIR=/work/goliath/current/runtime-bundle/domain/profiles
TMPDIR=/var/tmp/methyl-samtools
METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara/clara-parabricks:4.5.1-1
METHYL_PARABRICKS_GPU_FLAGS="--gpus all"
EOF
  chmod 600 "$envfile"
  overlay="$stage/worker.env"
  cat >"$overlay" <<'EOF'
WORKER_API_BASE="https://gateway.example.com/v1"
WORKER_CAPABILITY="parabricks.fq2bam"
GOLIATH_ENV="goliath-test"
AWS_ACCESS_KEY_ID="AKIAtest"
AWS_SECRET_ACCESS_KEY="secret-value"
EOF
  chmod 600 "$overlay"

  # shellcheck disable=SC1090
  eval "$(awk '
    /^merge_env_overlay\(\)/ {p=1}
    /^fetch_remote_env\(\)/ {p=1}
    /^install_merged_env\(\)/ {p=1}
    p {print}
    /^}/ {p=0}
  ' "$ROOT/scripts/deploy_target.sh")"
  # install_merged_env reads this global after the eval above.
  # shellcheck disable=SC2034
  stage_dir="$stage"
  ssh_cmd() {
    shift
    local script rc=0
    script="$(mktemp)"
    {
      echo 'sudo() { while [ "${1#-}" != "$1" ]; do shift; done; "$@"; }'
      printf '%s\n' "$*"
    } >"$script"
    dash "$script" || rc=$?
    rm -f "$script"
    return "$rc"
  }
  scp_to() {
    local src="$2" dest_path="$3"
    install -m 600 "$src" "$dest_path"
  }
  install_merged_env ubuntu@worker "$envfile" "$overlay" "$remote/tmp/goliath-worker.env" \
    || fail "install_merged_env failed under dash"
  [[ ! -e "$remote/tmp/goliath-worker.env" ]] || fail "remote env temp file was left behind"
  for merged_keys in \
    METHYL_EXTRACTOR_BIN METHYL_PROFILE_DIR TMPDIR \
    METHYL_PARABRICKS_IMAGE METHYL_PARABRICKS_GPU_FLAGS \
    AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY WORKER_API_BASE
  do
    grep -q "^${merged_keys}=" "$envfile" || fail "workers merge dropped ${merged_keys}"
  done

  arch="amd64"
  me_bin="$remote/local/methyl-extractor-${arch}/bin/MethylExtractor"
  mkdir -p "$(dirname "$me_bin")" "$remote/current"
  printf '#!/bin/sh\n' >"$me_bin"
  chmod +x "$me_bin"
  cat >"$remote/current/manifest.json" <<'EOF'
{"version":"9.9.9-test","parabricks_image":"nvcr.io/nvidia/clara/clara-parabricks:4.5.1-1","docker_data_root":"/work/goliath/docker"}
EOF
  METHYL_EXTRACTOR_HOME="$remote/local/methyl-extractor-${arch}" GOLIATH_WORK_MOUNT=/nonexistent-work \
    bash "$ROOT/scripts/write_worker_env.sh" --root "$remote" --manifest "$remote/current/manifest.json" --arch "$arch" \
    >/dev/null
  for merged_keys in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY WORKER_API_BASE METHYL_EXTRACTOR_BIN METHYL_PROFILE_DIR TMPDIR; do
    grep -q "^${merged_keys}=" "$envfile" || fail "promote rewrite dropped ${merged_keys}"
  done
  grep -q "^METHYL_EXTRACTOR_BIN=${me_bin}\$" "$envfile" \
    || fail "write_worker_env should point METHYL_EXTRACTOR_BIN at the local install"
  rm -rf "$stage" "$remote"
}
assert_worker_env_merge


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
real = json.loads((root / "deploy/targets/goliath-test.json").read_text(encoding="utf-8"))
if real["workers"][0].get("local_disk_gb") != 200:
    print("goliath-test local_disk_gb must be 200", file=sys.stderr)
    sys.exit(1)
low = json.loads(json.dumps(real))
low["workers"][0]["local_disk_gb"] = 50
if not list(validator.iter_errors(low)):
    print("local_disk_gb 50 must fail the schema minimum of 100", file=sys.stderr)
    sys.exit(1)
floor = json.loads(json.dumps(real))
floor["workers"][0]["local_disk_gb"] = 100
floor_errors = sorted(validator.iter_errors(floor), key=lambda item: list(item.path))
if floor_errors:
    for err in floor_errors:
        print(f"local_disk_gb 100: {list(err.path)}: {err.message}", file=sys.stderr)
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
jq -e '
  .shared_storage.bucket == "goliath-omics"
  and .shared_storage.endpoint == "https://s3.us-east-005.backblazeb2.com"
  and .shared_storage.region == "us-east-005"
  and .long_term_storage.bucket == "goliath-omics"
  and .long_term_storage.endpoint == "https://s3.us-east-1.myqnapcloud.io"
  and .long_term_storage.host == "s3.us-east-1.myqnapcloud.io"
  and .long_term_storage.share == "goliath-omics"
  and (.shared_storage | has("bucket_id") | not)
  and (.long_term_storage | has("bucket_id") | not)
' "$real" >/dev/null || fail "goliath-test storage endpoints do not match the filled profile"

env -u NEON_TEST_PASSWORD -u B2_ACCESS_KEY_ID -u B2_SECRET_ACCESS_KEY \
  -u LONGTERM_ACCESS_KEY_ID -u LONGTERM_SECRET_ACCESS_KEY -u GOLIATH_DEPLOY_SSH_KEY \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --dry-run >"$out" 2>"$err"
grep -q 'secret:GOLIATH_DEPLOY_SSH_KEY' "$out" || fail "dry-run should list the ssh key secret name"
grep -q 'secret:NEON_TEST_PASSWORD' "$out" || fail "dry-run should list the neon secret"
grep -q '45.76.21.8' "$out" || fail "dry-run should show the gateway address"
grep -q 'tls=self-signed' "$out" || fail "dry-run should show self-signed TLS"
grep -q 'ep-wandering-pine-a5l8s690-pooler.us-east-2.aws.neon.tech' "$out" || fail "dry-run should show the pooled host"
grep -q 'schema_host=ep-wandering-pine-a5l8s690.us-east-2.aws.neon.tech' "$out" || fail "dry-run should show schema_host"
grep -q 'channel_binding=require' "$out" || fail "dry-run should show channel_binding"
grep -q 'schema: goliathapp=per-release apply=test-bed force=0' "$out" \
  || fail "dry-run should show the GoliathApp schema mode"
grep -q 'self_provision runpod amd64 goliath-worker' "$out" || fail "dry-run should describe the RunPod pool"
grep -q 'bucket=goliath-omics' "$out" || fail "dry-run should show the B2 bucket"
grep -q 'https://s3.us-east-005.backblazeb2.com' "$out" || fail "dry-run should show the B2 endpoint"
grep -q 'https://s3.us-east-1.myqnapcloud.io' "$out" || fail "dry-run should show the myQNAPCloud endpoint"
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
grep -q 'local_disk_gb=200' "$out" || fail "workers component should print local_disk_gb"
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
le_fixture="$ROOT/deploy/targets/tls-le-ci.json"
cleanup_fixtures() {
  rm -f "$all_fixture" "$schema_fixture" "$ssh_fixture" "$le_fixture"
  if [[ -n "${api_pid:-}" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
    api_pid=""
  fi
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
set +e
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_DEPLOY_SSH_KEY="$fake_key" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target ssh-key-ci --component gateway >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "gateway deploy without a release artifact must fail"
grep -q 'GOLIATH_RELEASE_SRC' "$err" || fail "gateway deploy should say the release artifact is required"
if [[ -s "$ssh_log" ]]; then
  fail "gateway deploy without a release artifact should fail before SSH"
fi
if grep -q 'neon-secret-value' "$out" "$err"; then
  fail "refused gateway deploy printed the database password"
fi

write_entry_point_wheel() {
  local dest="$1" name="$2" scripts="$3"
  python3 - "$dest" "$name" "$scripts" <<'PY'
import sys
import zipfile
from pathlib import Path

dest, name, scripts = sys.argv[1:4]
norm = name.replace("-", "_")
dist = "%s-1.0.0.dist-info" % norm
path = Path(dest) / ("%s-1.0.0-py3-none-any.whl" % norm)
path.parent.mkdir(parents=True, exist_ok=True)
meta = "Metadata-Version: 2.1\nName: %s\nVersion: 1.0.0\n\n" % name
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("%s/METADATA" % dist, meta)
    if scripts.strip():
        archive.writestr(
            "%s/entry_points.txt" % dist,
            "[console_scripts]\n%s\n" % scripts.strip(),
        )
PY
}

missing_wheel="$(mktemp -d)"
printf '%s\n' '{"version":"0.0.0-test"}' >"$missing_wheel/manifest.json"
mkdir -p "$missing_wheel/wheels"
write_entry_point_wheel "$missing_wheel/wheels" methyl-worker ""
: >"$ssh_log"
set +e
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_DEPLOY_SSH_KEY="$fake_key" \
  GOLIATH_RELEASE_SRC="$missing_wheel" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target ssh-key-ci --component gateway >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "gateway deploy without the methyl-gateway console script must fail"
grep -q 'entry_points.txt' "$err" || fail "gateway deploy should say the wheel was selected from entry_points.txt"
if [[ -s "$ssh_log" ]]; then
  fail "gateway deploy without the console script should fail before SSH"
fi
rm -rf "$missing_wheel"

release_src="$(mktemp -d)"
printf '%s\n' '{"version":"0.0.0-test"}' >"$release_src/manifest.json"
mkdir -p "$release_src/wheels"
write_entry_point_wheel "$release_src/wheels" corepkg $'methyl-gateway = rest.gateway:main\nmethyl-reclaim-leases = ops.reclaim_expired_leases:main'
: >"$ssh_log"
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_DEPLOY_SSH_KEY="$fake_key" \
  GOLIATH_RELEASE_SRC="$release_src" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target ssh-key-ci --component gateway >"$out" 2>"$err"
grep -q 'PasswordAuthentication=no' "$ssh_log" || fail "ssh client should disable password auth"
grep -q 'IdentitiesOnly=yes' "$ssh_log" || fail "ssh client should use only the provided key"
grep -qF "UserKnownHostsFile=${HOME}/.ssh/known_hosts" "$ssh_log" \
  || fail "ssh client should use ~/.ssh/known_hosts"
if grep -q 'StrictHostKeyChecking=no' "$ssh_log"; then
  fail "ssh client must not disable host key checking"
fi
[[ -f "$ssh_dir/copied-identity" ]] || fail "ssh client did not receive an identity file"
[[ "$(cat "$ssh_dir/ident-mode")" == "600" ]] || fail "identity file must be mode 600"
grep -q 'TESTKEYMATERIAL' "$ssh_dir/copied-identity" || fail "identity file should contain the key"
if grep -q 'TESTKEYMATERIAL' "$out" "$err" "$ssh_log"; then
  fail "deploy printed the ssh private key"
fi
if grep -q 'neon-secret-value' "$out" "$err" "$ssh_log"; then
  fail "deploy printed the database password"
fi
grep -q "install_gateway_systemd.sh --root '/opt/methyl-gateway' --release '/opt/methyl-gateway/gateway-dist'" "$ssh_log" \
  || fail "gateway deploy should install the venv from the staged release before enabling the service"
grep -q 'scripts/install_gateway_venv.sh' "$ROOT/scripts/deploy_target.sh" \
  || fail "gateway bundle must ship install_gateway_venv.sh"
grep -q 'scripts/setup_gateway_nginx.sh' "$ROOT/scripts/deploy_target.sh" \
  || fail "gateway bundle must ship setup_gateway_nginx.sh"
grep -q "setup_gateway_nginx.sh --public-host '127.0.0.1' --tls 'self-signed' --bind '127.0.0.1' --port '8080'" "$ssh_log" \
  || fail "gateway deploy should install nginx for the SSH host when public_base_url is unset"

: >"$ssh_log"
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_DEPLOY_SSH_KEY="$fake_key" \
  GOLIATH_RELEASE_SRC="$release_src" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target goliath-test --component gateway >"$out" 2>"$err"
grep -q "setup_gateway_nginx.sh --public-host '45.76.21.8' --tls 'self-signed' --bind '127.0.0.1' --port '8080' --san-ip '45.76.21.8'" "$ssh_log" \
  || fail "goliath-test gateway deploy should install nginx for the public IP"
if grep -q 'neon-secret-value' "$out" "$err" "$ssh_log"; then
  fail "goliath-test gateway deploy printed the database password"
fi

cat >"$le_fixture" <<'EOF'
{
  "name": "tls-le-ci",
  "rollouts_environment": "goliath-test",
  "database": {
    "kind": "neon",
    "host": "pooler.example",
    "dbname": "neondb",
    "user": "neondb_owner",
    "password": "secret:NEON_TEST_PASSWORD"
  },
  "schema": {"apply": "none"},
  "gateway": {
    "public_base_url": "https://10.0.0.8/v1",
    "tls": {"mode": "letsencrypt"},
    "hosts": [{"name": "goliath-gateway", "ssh": "linuxuser@10.0.0.8"}]
  }
}
EOF
: >"$ssh_log"
set +e
env NEON_TEST_PASSWORD='neon-secret-value' GOLIATH_RELEASE_SRC="$release_src" PATH="$ssh_dir:$PATH" \
  bash "$ROOT/scripts/deploy_target.sh" --target tls-le-ci --component gateway >"$out" 2>"$err"
status=$?
set -e
rm -f "$le_fixture"
[[ "$status" -ne 0 ]] || fail "letsencrypt without a domain must fail"
grep -q 'gateway.tls.domain' "$err" || fail "letsencrypt refusal should name gateway.tls.domain"
if [[ -s "$ssh_log" ]]; then
  fail "letsencrypt without a domain should fail before SSH"
fi
rm -rf "$release_src"

workflow="$ROOT/.github/workflows/release-deploy.yml"
grep -q 'GOLIATH_GATEWAY_KNOWN_HOSTS: \${{ secrets.GOLIATH_GATEWAY_KNOWN_HOSTS }}' "$workflow" \
  || fail "release-deploy should read the GOLIATH_GATEWAY_KNOWN_HOSTS secret"
grep -q 'mkdir -p "\${HOME}/.ssh"' "$workflow" || fail "release-deploy should create ~/.ssh"
grep -q 'chmod 700 "\${HOME}/.ssh"' "$workflow" || fail "release-deploy should set ~/.ssh to mode 700"
grep -q 'chmod 600 "\${HOME}/.ssh/known_hosts"' "$workflow" || fail "release-deploy should set known_hosts to mode 600"
grep -q 'GOLIATH_GATEWAY_KNOWN_HOSTS is empty' "$workflow" || fail "release-deploy should fail when the known hosts secret is empty"
if grep -E '^[[:space:]]*ssh-keyscan([[:space:]]|$)' "$workflow" || grep -q 'StrictHostKeyChecking=no' "$workflow"; then
  fail "release-deploy must not scan hosts or disable host key checking"
fi
# GoliathApp SQL ships in the release and is applied once per release
# (scripts/test_apply_goliathapp_schema.sh covers the apply itself).
grep -q 'GOLIATH_SCHEMA_FORCE: \${{ inputs.forceSchema' "$workflow" \
  || fail "release-deploy should pass the forceSchema input to deploy_target.sh"
grep -q -- '--sql-out "\$OUTPUT/goliathapp-sql"' "$ROOT/scripts/build_release.sh" \
  || fail "build_release.sh should bundle GoliathApp sql_pg into goliathapp-sql/"
grep -q 'Require GoliathApp PostgreSQL scripts' "$ROOT/.github/workflows/release.yml" \
  || fail "release.yml should require the GoliathApp SQL bundle"
grep -q '"goliathapp_sql": mp_manifest.get("goliathapp_sql")' "$ROOT/scripts/assemble_release.sh" \
  || fail "assemble_release.sh should keep goliathapp_sql in manifest.json"
jq -e '.schema.goliathapp == "per-release"' "$real" >/dev/null \
  || fail "goliath-test should apply GoliathApp SQL once per release"

awk '
  /name: Install gateway SSH known hosts/ { seen_hosts = 1 }
  /name: Deploy target profile/ {
    if (!seen_hosts) exit 1
    found_deploy = 1
  }
  END { if (!seen_hosts || !found_deploy) exit 1 }
' "$workflow" || fail "known hosts step must run before deploy SSH"

command -v shellcheck >/dev/null 2>&1 || fail "shellcheck is required"
shellcheck -S warning -e SC1091 \
  "$start_script" \
  "$ROOT/scripts/start_shared_worker.sh" \
  "$ROOT/scripts/check_local_scratch.sh" \
  "$ROOT/scripts/worker_venv_local.sh" \
  "$ROOT/scripts/test_worker_venv_local.sh" \
  "$ROOT/deploy/runpod/keepalive.sh" \
  "$ROOT/deploy/runpod/goliath-worker-bootstrap.sh" \
  "$ROOT/deploy/runpod/template-start-command.sh" \
  "$ROOT/scripts/setup_gateway_nginx.sh" \
  "$ROOT/scripts/provision_gateway_node.sh" \
  "$ROOT/scripts/test_setup_gateway_nginx.sh"

work_root="$(mktemp -d)"
# The worker start installs system tools (install_system_deps.sh) before
# anything else; these flows test the start itself, so stub the installer.
# scripts/test_install_system_deps.sh covers the real one.
printf '#!/usr/bin/env bash\necho stub-system-deps "$@" >>"%s/system-deps.calls"\n' "$work_root" >"$work_root/system-deps-stub.sh"
export GOLIATH_SYSTEM_DEPS_SCRIPT="$work_root/system-deps-stub.sh"
private_root="$(mktemp -d)"
token_file="$private_root/worker-token"
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
# The worker venv is pod-local (GOLIATH_LOCAL_ROOT), never on the work mount.
worker_local="$(mktemp -d)"
mkdir -p "$work_root/goliath/current" "$worker_local/venv-amd64/bin"
cat >"$worker_local/venv-amd64/bin/methyl-worker" <<'EOF'
#!/bin/bash
dump="${METHYL_WORKER_ENV_DUMP:-}"
if [[ -n "$dump" && ! -f "$dump" ]]; then
  {
    printf 'METHYL_EXTRACTOR_BIN=%s\n' "${METHYL_EXTRACTOR_BIN-}"
    printf 'METHYL_PARABRICKS_IMAGE=%s\n' "${METHYL_PARABRICKS_IMAGE-}"
    printf 'METHYL_SAMPLE_CAAS_ENABLED=%s\n' "${METHYL_SAMPLE_CAAS_ENABLED-}"
    printf 'PATH=%s\n' "${PATH-}"
    printf 'GOLIATH_GATEWAY_CA_FILE=%s\n' "${GOLIATH_GATEWAY_CA_FILE-}"
    printf 'REQUESTS_CA_BUNDLE=%s\n' "${REQUESTS_CA_BUNDLE-}"
    printf 'SSL_CERT_FILE=%s\n' "${SSL_CERT_FILE-}"
    printf 'B2_BUCKET=%s\n' "${B2_BUCKET-}"
    printf 'B2_ENDPOINT=%s\n' "${B2_ENDPOINT-}"
    printf 'LONGTERM_BUCKET=%s\n' "${LONGTERM_BUCKET-}"
    printf 'LONGTERM_ENDPOINT=%s\n' "${LONGTERM_ENDPOINT-}"
    printf 'LONGTERM_HOST=%s\n' "${LONGTERM_HOST-}"
    printf 'LONGTERM_SHARE=%s\n' "${LONGTERM_SHARE-}"
  } >"$dump"
fi
sleep 300
EOF
chmod +x "$worker_local/venv-amd64/bin/methyl-worker"
mkdir -p "$work_root/goliath/env"
cat >"$work_root/goliath/env/worker.env" <<'EOF'
# Generated by scripts/write_worker_env.sh (release 1.2.3)
GOLIATH_ROOT=/work/goliath
METHYL_EXTRACTOR_BIN=/opt/goliath/methyl-extractor-amd64/bin/MethylExtractor
HDF5_PLUGIN_PATH=/opt/goliath/methyl-extractor-amd64/lib/hdf5_zstd_plugin
PATH=/opt/goliath/venv/bin:/usr/bin
METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1
METHYL_SAMPLE_CAAS_ENABLED=0
METHYL_GPU_HBM_FREE_FRACTION=0.90
POSTGRES_PASSWORD=old-neon
AWS_ENDPOINT_URL=https://stale-aws.example
AWS_SECRET_ACCESS_KEY=stale-aws-sekret
GOLIATH_LONGTERM_BUCKET=stale-longterm-bucket
GOLIATH_LONGTERM_SECRET_ACCESS_KEY=stale-lt-sekret
EOF
chmod 600 "$work_root/goliath/env/worker.env"
printf 'system-ca\n' >"$work_root/system-ca.pem"

run_start() {
  env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
    -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_ENDPOINT_URL \
    -u AWS_DEFAULT_REGION -u AWS_SESSION_TOKEN \
    -u GOLIATH_SHARED_BUCKET -u GOLIATH_SHARED_PROVIDER \
    -u GOLIATH_LONGTERM_ACCESS_KEY_ID -u GOLIATH_LONGTERM_SECRET_ACCESS_KEY \
    -u GOLIATH_LONGTERM_BUCKET -u GOLIATH_LONGTERM_ENDPOINT \
    -u GOLIATH_LONGTERM_HOST -u GOLIATH_LONGTERM_SHARE \
    -u GOLIATH_LONGTERM_PROVIDER \
    GOLIATH_ROOT="$work_root/goliath" \
    GOLIATH_WORK_MOUNT="$work_root" \
    GOLIATH_LOCAL_ROOT="$worker_local" \
    GOLIATH_LOG_DIR="$worker_local/logs" \
    GOLIATH_START_NO_EXEC=1 \
    GOLIATH_PROVISION_SCRIPT="$work_root/provision" \
    GOLIATH_REPORT_SCRIPT="$work_root/report" \
    METHYL_WORKER_TOKEN_FILE="$token_file" \
    GOLIATH_PRIVATE_DIR="$private_root" \
    WORKER_API_BASE="https://45.76.21.8/v1" \
    RUNPOD_POD_ID="pod-test-1" \
    GOLIATH_WORKER_TOKEN="sekret-token" \
    B2_BUCKET="goliath-omics" \
    B2_ENDPOINT="https://s3.us-east-005.backblazeb2.com" \
    B2_ACCESS_KEY_ID="b2-key" \
    B2_SECRET_ACCESS_KEY="b2-sekret" \
    LONGTERM_BUCKET="goliath-omics" \
    LONGTERM_ENDPOINT="https://s3.us-east-1.myqnapcloud.io" \
    LONGTERM_HOST="s3.us-east-1.myqnapcloud.io" \
    LONGTERM_SHARE="goliath-omics" \
    LONGTERM_ACCESS_KEY_ID="lt-key" \
    LONGTERM_SECRET_ACCESS_KEY="lt-sekret" \
    CURSOR_API_KEY="cursor-sekret" \
    NEON_TEST_PASSWORD="neon-sekret" \
    GOLIATH_GATEWAY_CA_PEM=$'-----BEGIN CERTIFICATE-----\nPINNED-CA-MARKER\n-----END CERTIFICATE-----\n' \
    GOLIATH_SYSTEM_CA_BUNDLE="$work_root/system-ca.pem" \
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
grep -q 'node=pod-test-1' "$report_log" || fail "report should include the node id"
grep -q 'arch=amd64' "$report_log" || fail "report should include arch"
grep -q 'GPU=NVIDIA RTX 6000 Ada Generation' "$report_log" || fail "report should include the nvidia-smi GPU name"
grep -q 'host=' "$report_log" || fail "report should include host"
grep -q 'GPU detected via nvidia-smi' "$out" || fail "start script should log nvidia-smi detection"
grep -q 'local scratch preflight:' "$out" || fail "start script should run the local scratch preflight"
[[ "$(stat -c %a "$token_file")" == "600" ]] || fail "worker token file must be mode 600"
grep -q '^WORKER_ID=7$' "$token_file" || fail "enroll token should be kept"
supervisor_pid="$(cat "$worker_local/logs/methyl-worker.supervisor.pid")"
# Runtime logs are on local disk; /work keeps only per-node state.
[[ ! -e "$work_root/goliath/nodes/pod-test-1/logs" ]] || fail "worker logs must not be created on /work"
[[ ! -e "$work_root/goliath/logs/methyl-worker.supervisor.pid" ]] || fail "supervisor pid must not be on /work"
grep -q "runtime logs on local disk: $worker_local/logs" "$out" || fail "start should log the local log dir"
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
if grep -q 'POSTGRES_PASSWORD' "$work_root/goliath/env/worker.env" \
  "$work_root/goliath/nodes/pod-test-1/env/worker.env"; then
  fail "worker.env should not contain the Neon password by default"
fi
worker_env="$work_root/goliath/nodes/pod-test-1/env/worker.env"
secrets_env="$private_root/secrets.env"
[[ "$(stat -c %a "$worker_env")" == "644" ]] || fail "node worker.env must be world-readable"
grep -q '^GOLIATH_NODE_ID="pod-test-1"$' "$worker_env" \
  || fail "node worker.env should record GOLIATH_NODE_ID"
[[ "$(stat -c %a "$secrets_env")" == "600" ]] || fail "secrets env must be mode 600"
if grep -q 'b2-sekret\|lt-sekret\|stale-aws-sekret\|stale-lt-sekret' \
  "$work_root/goliath/env/worker.env" "$worker_env"; then
  fail "storage secret was written on the shared volume"
fi
grep -q '^B2_BUCKET="goliath-omics"$' "$worker_env" \
  || fail "node worker.env should record B2_BUCKET"
grep -q '^B2_ENDPOINT="https://s3.us-east-005.backblazeb2.com"$' "$worker_env" \
  || fail "node worker.env should record B2_ENDPOINT"
grep -q '^LONGTERM_BUCKET="goliath-omics"$' "$worker_env" \
  || fail "node worker.env should record LONGTERM_BUCKET"
grep -q '^LONGTERM_ENDPOINT="https://s3.us-east-1.myqnapcloud.io"$' "$worker_env" \
  || fail "node worker.env should record LONGTERM_ENDPOINT"
grep -q '^LONGTERM_HOST="s3.us-east-1.myqnapcloud.io"$' "$worker_env" \
  || fail "node worker.env should record LONGTERM_HOST"
grep -q '^LONGTERM_SHARE="goliath-omics"$' "$worker_env" \
  || fail "node worker.env should record LONGTERM_SHARE"
if grep -E -q '^(AWS_|GOLIATH_LONGTERM_|GOLIATH_SHARED_PROVIDER=|GOLIATH_SHARED_BUCKET=)' \
  "$worker_env" "$work_root/goliath/env/worker.env" "$secrets_env"; then
  fail "boot should not persist AWS_* or GOLIATH_LONGTERM_* storage names"
fi
grep -q '^METHYL_EXTRACTOR_BIN=/opt/goliath/methyl-extractor-amd64/bin/MethylExtractor$' "$worker_env" \
  || fail "promote METHYL_EXTRACTOR_BIN was dropped from worker.env"
grep -q '^METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1$' "$worker_env" \
  || fail "promote METHYL_PARABRICKS_IMAGE was dropped from worker.env"
grep -q '^PATH=/opt/goliath/venv/bin:/usr/bin$' "$worker_env" \
  || fail "promote PATH was dropped from worker.env"
grep -q '^METHYL_SAMPLE_CAAS_ENABLED=0$' "$worker_env" \
  || fail "promote METHYL_SAMPLE_CAAS_ENABLED was dropped from worker.env"
grep -q '^METHYL_GPU_HBM_FREE_FRACTION=0.90$' "$worker_env" \
  || fail "promote HBM setting was dropped from worker.env"
grep -q '^HDF5_PLUGIN_PATH=/opt/goliath/methyl-extractor-amd64/lib/hdf5_zstd_plugin$' "$worker_env" \
  || fail "promote HDF5_PLUGIN_PATH was dropped from worker.env"
extractor_lines="$(grep -c '^METHYL_EXTRACTOR_BIN=' "$worker_env" || true)"
[[ "$extractor_lines" == "1" ]] || fail "METHYL_EXTRACTOR_BIN should appear once"
grep -q '^B2_ACCESS_KEY_ID="b2-key"$' "$secrets_env" \
  || fail "secrets env should keep the B2 access key under B2_ACCESS_KEY_ID"
grep -q '^B2_SECRET_ACCESS_KEY="b2-sekret"$' "$secrets_env" \
  || fail "secrets env should keep the B2 secret under B2_SECRET_ACCESS_KEY"
grep -q '^LONGTERM_ACCESS_KEY_ID="lt-key"$' "$secrets_env" \
  || fail "secrets env should keep the long-term access key under LONGTERM_ACCESS_KEY_ID"
grep -q '^LONGTERM_SECRET_ACCESS_KEY="lt-sekret"$' "$secrets_env" \
  || fail "secrets env should keep the long-term secret under LONGTERM_SECRET_ACCESS_KEY"
if grep '^LONGTERM_' "$secrets_env" | grep -q 'b2-sekret'; then
  fail "B2 secret leaked into a LONGTERM_ secret"
fi
if grep '^B2_' "$secrets_env" | grep -q 'lt-sekret'; then
  fail "long-term secret leaked into a B2_ secret"
fi
seen="$work_root/methyl-worker-env"
for ((i = 0; i < 50; i++)); do
  [[ -s "$seen" ]] && break
  sleep 0.1
done
[[ -s "$seen" ]] || fail "methyl-worker did not record its environment"
grep -q '^METHYL_EXTRACTOR_BIN=/opt/goliath/methyl-extractor-amd64/bin/MethylExtractor$' "$seen" \
  || fail "supervisor did not load METHYL_EXTRACTOR_BIN"
grep -q '^METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara-parabricks:4.5.1$' "$seen" \
  || fail "supervisor did not load METHYL_PARABRICKS_IMAGE"
grep -q '^METHYL_SAMPLE_CAAS_ENABLED=0$' "$seen" \
  || fail "supervisor did not load METHYL_SAMPLE_CAAS_ENABLED"
grep -q '^PATH=/opt/goliath/venv/bin:/usr/bin$' "$seen" \
  || fail "supervisor did not load PATH from worker.env"
grep -q '^B2_BUCKET=goliath-omics$' "$seen" \
  || fail "supervisor did not load B2_BUCKET"
grep -q '^LONGTERM_ENDPOINT=https://s3.us-east-1.myqnapcloud.io$' "$seen" \
  || fail "supervisor did not load LONGTERM_ENDPOINT"
if grep -q 'b2-sekret\|lt-sekret\|b2-key\|lt-key' "$seen"; then
  fail "supervisor dump must not record storage keys"
fi
ca_file="$work_root/goliath/env/gateway-ca.pem"
bundle_file="$work_root/goliath/env/requests-ca-bundle.pem"
[[ -f "$ca_file" ]] || fail "start script should write the gateway CA"
grep -q 'PINNED-CA-MARKER' "$ca_file" || fail "gateway CA file should contain the PEM"
grep -q 'BEGIN CERTIFICATE' "$ca_file" || fail "gateway CA file should be PEM"
[[ "$(stat -c %a "$ca_file")" == "644" ]] || fail "gateway CA file must be mode 644"
[[ -f "$bundle_file" ]] || fail "start script should write a requests CA bundle"
grep -q 'system-ca' "$bundle_file" || fail "requests bundle should include the system CAs"
grep -q 'PINNED-CA-MARKER' "$bundle_file" || fail "requests bundle should include the gateway certificate"
grep -q "^GOLIATH_GATEWAY_CA_FILE=\"${ca_file}\"\$" "$worker_env" \
  || fail "worker.env should point GOLIATH_GATEWAY_CA_FILE at the PEM file"
grep -q "^REQUESTS_CA_BUNDLE=\"${bundle_file}\"\$" "$worker_env" \
  || fail "worker.env should point REQUESTS_CA_BUNDLE at the combined bundle"
grep -q "^SSL_CERT_FILE=\"${bundle_file}\"\$" "$worker_env" \
  || fail "worker.env should point SSL_CERT_FILE at the combined bundle"
if grep -q 'PINNED-CA-MARKER' "$worker_env" "$out" "$err" "$report_log" "$prov_log"; then
  fail "start script stored or printed the gateway PEM outside the CA file"
fi
grep -q "^GOLIATH_GATEWAY_CA_FILE=${ca_file}\$" "$seen" \
  || fail "supervisor did not load GOLIATH_GATEWAY_CA_FILE"
grep -q "^REQUESTS_CA_BUNDLE=${bundle_file}\$" "$seen" \
  || fail "supervisor did not load REQUESTS_CA_BUNDLE"
for layout_name in samples projects cache genomes site; do
  [[ -d "$work_root/$layout_name" ]] || fail "start script should create ${layout_name} on an empty layout"
done
[[ -d "$work_root/goliath/images" ]] || fail "start script should create the shared image cache"
grep -q 'container-local disk' "$out" || fail "start script should warn that Docker overlay stays off the network volume"

deny_root="$(mktemp -d)"
mkdir -p "$deny_root/bin" "$deny_root/work/goliath/current" "$deny_root/local/venv-amd64/bin" \
  "$deny_root/work/goliath/env" "$deny_root/private"
cat >"$deny_root/bin/chmod" <<EOF
#!/bin/bash
for arg in "\$@"; do
  case "\$arg" in
    "$deny_root/work"|"$deny_root/work"/*)
      echo "cannot change permissions of \$arg: Operation not permitted" >&2
      exit 1
      ;;
  esac
done
exec /bin/chmod "\$@"
EOF
chmod +x "$deny_root/bin/chmod"
cp "$worker_local/venv-amd64/bin/methyl-worker" "$deny_root/local/venv-amd64/bin/methyl-worker"
cp "$work_root/goliath/env/worker.env" "$deny_root/work/goliath/env/worker.env"
chmod +x "$deny_root/local/venv-amd64/bin/methyl-worker"
env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
  GOLIATH_ROOT="$deny_root/work/goliath" \
  GOLIATH_WORK_MOUNT="$deny_root/work" \
  GOLIATH_LOCAL_ROOT="$deny_root/local" \
  GOLIATH_PRIVATE_DIR="$deny_root/private" \
  GOLIATH_LOG_DIR="$deny_root/local/logs" \
  GOLIATH_START_NO_EXEC=1 \
  GOLIATH_PROVISION_SCRIPT="$work_root/provision" \
  GOLIATH_REPORT_SCRIPT="$work_root/report" \
  METHYL_WORKER_TOKEN_FILE="$deny_root/private/worker-token" \
  WORKER_API_BASE="https://45.76.21.8/v1" \
  RUNPOD_POD_ID="pod-chmod" \
  GOLIATH_WORKER_TOKEN="sekret-token" \
  B2_SECRET_ACCESS_KEY="b2-sekret" \
  PATH="$deny_root/bin:$work_root:$PATH" \
  bash "$start_script" >"$out" 2>"$err"
grep -q 'was not applied' "$out" || fail "chmod failure on /work should warn and continue"
[[ "$(grep -c 'was not applied' "$out")" == "1" ]] || fail "chmod rejections on /work should log one note, not one per path"
if grep -qi 'chmod' "$err"; then fail "expected chmod rejections on /work must not be errors on stderr"; fi
if grep -q 'cannot create' "$err"; then
  fail "mkdir on a chmod-rejecting volume should still create the work tree"
fi
[[ -d "$deny_root/work/goliath/nodes/pod-chmod/status" ]] || fail "per-node status dir should exist when chmod fails"
[[ ! -e "$deny_root/work/goliath/nodes/pod-chmod/logs" ]] || fail "per-node logs must be local, not on /work"
[[ -f "$deny_root/private/secrets.env" ]] || fail "secrets stay on local disk when /work rejects chmod"
[[ "$(stat -c %a "$deny_root/private/secrets.env")" == "600" ]] || fail "local secrets file must be mode 600"
if grep -R -q 'b2-sekret' "$deny_root/work"; then
  fail "chmod-rejecting /work must not contain the storage secret"
fi
if [[ -f "$deny_root/work/goliath/logs/methyl-worker.supervisor.pid" ]]; then
  kill "$(cat "$deny_root/work/goliath/logs/methyl-worker.supervisor.pid")" >/dev/null 2>&1 || true
fi
pod_pid="$deny_root/local/logs/methyl-worker.supervisor.pid"
if [[ -f "$pod_pid" ]]; then
  kill "$(cat "$pod_pid")" >/dev/null 2>&1 || true
fi
if [[ -d "$work_root/goliath/runpod" ]]; then
  fail "worker identity must not be stored under goliath/runpod"
fi
if grep -q 'b2-sekret' "$work_root/goliath/nodes/pod-test-1/status/joined" 2>/dev/null; then
  fail "join stamp must not contain the storage secret"
fi

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

keep_marker="$(mktemp)"
keep_script="$work_root/keepalive.sh"
cat >"$keep_script" <<EOF
#!/bin/bash
printf 'kept-alive\n' >"$keep_marker"
exit 0
EOF
chmod +x "$keep_script"
set +e
env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL -u GOLIATH_START_NO_EXEC \
  GOLIATH_WORKER_TOKEN="sekret-token" \
  GOLIATH_ROOT="$empty_root/goliath" \
  GOLIATH_WORK_MOUNT="$empty_root" \
  GOLIATH_START_KEEPALIVE="$keep_script" \
  PATH="$work_root:$PATH" \
  bash "$start_script" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -eq 0 ]] || fail "start failure should exec the keepalive command (status $status)"
grep -q 'kept-alive' "$keep_marker" || fail "start failure should keep the pod alive"
grep -q 'GOLIATH_RELEASE_URL' "$err" || fail "keepalive path should still log why startup failed"
grep -q 'keeping the process alive' "$err" || fail "keepalive path should say the process stays up"
if grep -q 'sekret-token' "$out" "$err"; then
  fail "keepalive path printed the worker token"
fi

layout_mount="$(mktemp -d)"
layout_log="$(mktemp)"
layout_token="$(mktemp)"
layout_prov="$(mktemp)"
layout_report="$(mktemp)"
mkdir -p "$layout_mount/goliath/current/runtime-bundle/scripts" \
  "$layout_mount.local/venv-amd64/bin" \
  "$layout_mount/goliath/env"
cp "$worker_local/venv-amd64/bin/methyl-worker" "$layout_mount.local/venv-amd64/bin/methyl-worker"
cp "$work_root/goliath/env/worker.env" "$layout_mount/goliath/env/worker.env"
chmod +x "$layout_mount.local/venv-amd64/bin/methyl-worker"
cat >"$layout_mount/goliath/current/runtime-bundle/scripts/init_work_layout.sh" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >"$layout_log"
exit 0
EOF
cat >"$layout_prov" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$layout_log"
mkdir -p "\$(dirname "$layout_token")"
printf 'WORKER_ID=7\nWORKER_TOKEN=minted-token\n' >"$layout_token"
chmod 600 "$layout_token"
exit 0
EOF
cat >"$layout_report" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$layout_prov" "$layout_report"
env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
  GOLIATH_ROOT="$layout_mount/goliath" \
  GOLIATH_WORK_MOUNT="$layout_mount" \
  GOLIATH_LOCAL_ROOT="$layout_mount.local" \
  GOLIATH_LOG_DIR="$layout_mount/goliath/logs" \
  GOLIATH_START_NO_EXEC=1 \
  GOLIATH_PROVISION_SCRIPT="$layout_prov" \
  GOLIATH_REPORT_SCRIPT="$layout_report" \
  METHYL_WORKER_TOKEN_FILE="$layout_token" \
  WORKER_API_BASE="https://45.76.21.8/v1" \
  RUNPOD_POD_ID="pod-layout" \
  PATH="$work_root:$PATH" \
  bash "$start_script" >"$out" 2>"$err"
grep -q -- "--work $layout_mount" "$layout_log" || fail "start script should run init_work_layout.sh from the release"
# GOLIATH_LOG_DIR on the work mount is refused; logs go to local disk.
grep -q "GOLIATH_LOG_DIR=$layout_mount/goliath/logs is on $layout_mount" "$out" || fail "a log dir on /work should be refused"
[[ ! -e "$layout_mount/goliath/logs/methyl-worker.supervisor.pid" ]] || fail "supervisor pid must not be on /work"
layout_logs="$(sed -n 's/.*runtime logs on local disk: //p' "$out" | tail -n 1)"
if [[ -n "$layout_logs" && -f "$layout_logs/methyl-worker.supervisor.pid" ]]; then
  kill "$(cat "$layout_logs/methyl-worker.supervisor.pid")" >/dev/null 2>&1 || true
  rm -f "$layout_logs/methyl-worker.supervisor.pid"
fi

# Size-based rotation of the local worker log.
rot_dir="$(mktemp -d)"
eval "$(sed -n '/^rotating_log() {/,/^}/p' "$ROOT/scripts/start_shared_worker.sh")"
for i in $(seq 1 3000); do printf 'line %05d padding-padding-padding-padding\n' "$i"; done \
  | rotating_log "$rot_dir/methyl-worker.log" 20000 2
[[ -f "$rot_dir/methyl-worker.log" && -f "$rot_dir/methyl-worker.log.1" && -f "$rot_dir/methyl-worker.log.2" ]] \
  || fail "rotating_log should keep the live file plus 2 rotations"
[[ ! -e "$rot_dir/methyl-worker.log.3" ]] || fail "rotating_log must not keep more than 2 rotations"
for f in "$rot_dir"/methyl-worker.log*; do
  (( $(stat -c %s "$f") < 30000 )) || fail "rotated file $f exceeds the cap"
done
tail -n 1 "$rot_dir/methyl-worker.log" | grep -q 'line 03000' || fail "newest line should be in the live log"
rm -rf "$rot_dir"

boot="$ROOT/deploy/runpod/goliath-worker-bootstrap.sh"
stub_cmd="$ROOT/deploy/runpod/template-start-command.sh"
python3 - "$ROOT/deploy/runpod/README.md" "$stub_cmd" <<'PY'
import pathlib, sys
readme_path, cmd_path = map(pathlib.Path, sys.argv[1:])
file_line = ""
for line in cmd_path.read_text().splitlines():
    if line.startswith("bash -lc '"):
        file_line = line
        break
if not file_line:
    raise SystemExit("template-start-command.sh has no bash -lc line")
text = readme_path.read_text()
begin = "<!-- goliath-template-start:begin -->"
end = "<!-- goliath-template-start:end -->"
if begin not in text or end not in text:
    raise SystemExit("readme is missing the template start markers")
chunk = text.split(begin, 1)[1].split(end, 1)[0].strip()
if not chunk.startswith("```text\n") or not chunk.endswith("\n```"):
    raise SystemExit("template start fence missing")
readme_line = chunk[len("```text\n"):-len("\n```")]
if "\n" in readme_line:
    raise SystemExit("template start command must be one line")
if readme_line != file_line:
    raise SystemExit("readme start command does not match template-start-command.sh")
if len(file_line) > 1200:
    raise SystemExit(f"template start command is {len(file_line)} chars; keep it <= 1200")
if not file_line.startswith("bash -lc '") or not file_line.endswith("'"):
    raise SystemExit("template start command must be bash -lc of one single-quoted script")
inner = file_line[len("bash -lc '"):-1]
if "'" in inner:
    raise SystemExit("template start command is not quote-safe: single quote inside bash -lc")
if "jq" in inner.split("python3", 1)[-1]:
    raise SystemExit("template start command must not require jq")
for needle in (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOLIATH_RELEASE_TAG",
    "GOLIATH_GITHUB_REPO",
    "Goliath-Research/GoliathOmics",
    "goliathomics-",
    "deploy/runpod/goliath-worker-bootstrap.sh",
    'exec "${GOLIATH_START_KEEPALIVE:-/start.sh}"',
    "draft",
    "prerelease",
    "set +x",
):
    if needle not in inner:
        raise SystemExit(f"template start command is missing {needle}")
start = inner.index('python3 -c "') + len('python3 -c "')
chars = []
i = start
while i < len(inner):
    ch = inner[i]
    if ch == '"':
        break
    if ch == "\\" and i + 1 < len(inner) and inner[i + 1] in '$`"\\':
        chars.append(inner[i + 1])
        i += 2
        continue
    chars.append(ch)
    i += 1
else:
    raise SystemExit("python -c string in the template start command is not closed")
program = "".join(chars)
if "'" in program:
    raise SystemExit("fetched-script python is not quote-safe")
compile(program, "<template-start>", "exec")
PY
bash -n "$stub_cmd"

boot_mount="$(mktemp -d)"
boot_bin="$(mktemp -d)"
boot_stub="$boot_mount/stub.sh"
boot_releases="$boot_mount/releases.json"
boot_contents="$boot_mount/contents.json"
curl_urls="$boot_mount/curl.urls"
curl_args="$boot_mount/curl.args"
curl_mode="$boot_mount/curl.mode"
curl_auth="$boot_mount/curl.auth"
cat >"$boot_stub" <<'EOF'
#!/usr/bin/env bash
# goliath-worker-start stub
printf 'stub-ran\n'
exit 0
EOF
python3 - "$boot_stub" "$boot_releases" "$boot_contents" <<'PY'
import base64, json, pathlib, sys
stub, releases, contents = map(pathlib.Path, sys.argv[1:])
releases.write_text(json.dumps([
    {"tag_name": "goliathomics-2099.1.0", "draft": True, "prerelease": False},
    {"tag_name": "v2026.10.2", "draft": False, "prerelease": False},
    {"tag_name": "goliathomics-2026.10.2", "draft": False, "prerelease": False},
]))
payload = base64.b64encode(stub.read_bytes()).decode("ascii")
contents.write_text(json.dumps({"encoding": "base64", "content": payload}))
PY
cat >"$boot_bin/curl" <<EOF
#!/bin/bash
url=""
out=""
header=""
printf '%s\n' "\$*" >>"$curl_args"
args=("\$@")
i=0
while [[ \$i -lt \${#args[@]} ]]; do
  a="\${args[\$i]}"
  case "\$a" in
    -o)
      i=\$((i + 1))
      out="\${args[\$i]}"
      ;;
    -H)
      i=\$((i + 1))
      h="\${args[\$i]}"
      case "\$h" in
        @*) header="\${h#@}" ;;
      esac
      ;;
    http*)
      url="\$a"
      ;;
  esac
  i=\$((i + 1))
done
printf '%s\n' "\$url" >>"$curl_urls"
if [[ -n "\$header" ]]; then
  stat -c %a "\$header" >"$curl_mode"
  if grep -q "^Authorization: Bearer " "\$header"; then
    printf 'bearer\n' >"$curl_auth"
  fi
fi
case "\$url" in
  *"/releases?per_page="*)
    cp "$boot_releases" "\$out"
    ;;
  *"/contents/deploy/runpod/goliath-worker-start.sh?ref="*)
    cp "$boot_contents" "\$out"
    ;;
  *"/contents/scripts/start_shared_worker.sh?ref="*)
    python3 -c 'import base64,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); b=b"#!/usr/bin/env bash\n# start_shared_worker\n"; p.write_text(json.dumps({"encoding":"base64","content":base64.b64encode(b).decode()}))' "\$out"
    ;;
  *"/contents/scripts/check_local_scratch.sh?ref="*)
    python3 -c 'import base64,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); b=b"#!/usr/bin/env bash\n# local-scratch\n"; p.write_text(json.dumps({"encoding":"base64","content":base64.b64encode(b).decode()}))' "\$out"
    ;;
  *"/contents/deploy/runpod/keepalive.sh?ref="*)
    python3 -c 'import base64,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); b=b"#!/usr/bin/env bash\n# goliath-runpod\n"; p.write_text(json.dumps({"encoding":"base64","content":base64.b64encode(b).decode()}))' "\$out"
    ;;
  *)
    printf 'unexpected url\n' >&2
    exit 1
    ;;
esac
exit 0
EOF
chmod +x "$boot_bin/curl"

run_boot() {
  env -u GITHUB_TOKEN -u GOLIATH_RELEASE_URL \
    GOLIATH_WORK_MOUNT="$boot_mount" \
    GOLIATH_ROOT="$boot_mount/goliath" \
    GH_TOKEN="gh-sekret-token" \
    CURSOR_API_KEY="cursor-sekret" \
    GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
    PATH="$boot_bin:$PATH" \
    bash "$boot" >"$out" 2>"$err"
}

: >"$curl_urls"
: >"$curl_args"
run_boot
grep -q 'stub-ran' "$out" || fail "bootstrap should run the downloaded start script"
grep -q 'goliathomics-2026.10.2' "$curl_urls" || fail "bootstrap should fetch the goliathomics release tag"
grep -q '/releases?per_page=' "$curl_urls" || fail "bootstrap should list releases when no tag is pinned"
[[ "$(cat "$curl_auth")" == "bearer" ]] || fail "bootstrap should send a bearer header"
[[ "$(cat "$curl_mode")" == "600" ]] || fail "GitHub header file must be mode 600"
[[ "$(stat -c %a "$boot_mount/goliath/bin/goliath-worker-start.sh")" == "755" ]] \
  || fail "installed start script must be mode 755"
cmp -s "$boot_stub" "$boot_mount/goliath/bin/goliath-worker-start.sh" \
  || fail "installed start script should match the release contents"
if find "$boot_mount/goliath/tmp" -type f 2>/dev/null | grep -q .; then
  fail "bootstrap left a GitHub header or download in tmp"
fi
if grep -q 'gh-sekret-token' "$out" "$err" "$curl_args" "$curl_urls"; then
  fail "bootstrap printed or passed GH_TOKEN on the curl command line"
fi
if grep -q 'cursor-sekret' "$out" "$err"; then
  fail "bootstrap printed CURSOR_API_KEY"
fi
url_lines="$(wc -l <"$curl_urls" | tr -d ' ')"
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_mount" \
  GOLIATH_ROOT="$boot_mount/goliath" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
grep -q 'using existing' "$out" || fail "second boot should reuse the installed start script"
url_lines_after="$(wc -l <"$curl_urls" | tr -d ' ')"
[[ "$url_lines_after" == "$url_lines" ]] || fail "second boot should not call GitHub"

rm -rf "$boot_mount/goliath"
mkdir -p "$boot_mount/goliath/current/runtime-bundle/deploy/runpod" \
  "$boot_mount/goliath/bin"
cat >"$boot_mount/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh" <<'EOF'
#!/usr/bin/env bash
# goliath-worker-start bundle
printf 'bundle-ran\n'
exit 0
EOF
cat >"$boot_mount/goliath/bin/goliath-worker-start.sh" <<'EOF'
#!/usr/bin/env bash
# goliath-worker-start old
printf 'old-ran\n'
exit 0
EOF
chmod 755 "$boot_mount/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh" \
  "$boot_mount/goliath/bin/goliath-worker-start.sh"
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_mount" \
  GOLIATH_ROOT="$boot_mount/goliath" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
grep -q 'bundle-ran' "$out" || fail "bootstrap should prefer the current release bundle"
if grep -q 'old-ran' "$out"; then
  fail "bootstrap ran the stale bin copy"
fi

: >"$curl_urls"
rm -rf "$boot_mount/goliath"
env -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_mount" \
  GOLIATH_ROOT="$boot_mount/goliath" \
  GH_TOKEN="gh-sekret-token" \
  GOLIATH_RELEASE_TAG="goliathomics-9.9.9" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
grep -q 'ref=goliathomics-9.9.9' "$curl_urls" || fail "pinned tag should be the contents ref"
if grep -q '/releases?per_page=' "$curl_urls"; then
  fail "pinned tag should not list releases"
fi

mkdir -p "$boot_mount/fresh"
set +e
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_mount/fresh" \
  GOLIATH_ROOT="$boot_mount/fresh/goliath" \
  GOLIATH_START_NO_EXEC=1 \
  CURSOR_API_KEY="cursor-sekret" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "bootstrap without a script or token should fail when NO_EXEC is set"
grep -q 'GH_TOKEN' "$err" || fail "missing token should name GH_TOKEN"
if grep -q 'cursor-sekret' "$out" "$err"; then
  fail "failed bootstrap printed CURSOR_API_KEY"
fi

# The mount check above used a path that was not created yet. Recreate a real
# empty mount for the keepalive and bad-tag cases.
rm -rf "$boot_mount/fresh"
mkdir -p "$boot_mount/fresh"
keep2="$boot_mount/keep.sh"
keep2_marker="$boot_mount/kept"
cat >"$keep2" <<EOF
#!/bin/bash
printf 'boot-kept\n' >"$keep2_marker"
exit 0
EOF
chmod +x "$keep2"
set +e
env -u GH_TOKEN -u GITHUB_TOKEN -u GOLIATH_START_NO_EXEC \
  GOLIATH_WORK_MOUNT="$boot_mount/fresh" \
  GOLIATH_ROOT="$boot_mount/fresh/goliath" \
  GOLIATH_START_KEEPALIVE="$keep2" \
  CURSOR_API_KEY="cursor-sekret" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -eq 0 ]] || fail "bootstrap failure should keep the pod alive (status $status)"
grep -q 'boot-kept' "$keep2_marker" || fail "bootstrap failure should exec keepalive"
grep -q 'keeping the pod alive' "$err" || fail "bootstrap failure should log that the pod stays up"
if grep -q 'cursor-sekret' "$out" "$err"; then
  fail "keepalive bootstrap printed CURSOR_API_KEY"
fi

set +e
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_mount/fresh" \
  GOLIATH_ROOT="$boot_mount/fresh/goliath" \
  GOLIATH_START_NO_EXEC=1 \
  GOLIATH_RELEASE_TAG="sekret-not-a-tag" \
  GH_TOKEN="gh-sekret-token" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "unsafe release tag should fail"
grep -q 'goliathomics-<version>' "$err" || fail "unsafe tag should explain the required shape"
if grep -q 'sekret-not-a-tag' "$out" "$err"; then
  fail "bootstrap printed the rejected release tag"
fi
if grep -q 'gh-sekret-token' "$out" "$err"; then
  fail "rejected tag path printed GH_TOKEN"
fi
if find "$boot_mount/fresh/goliath/tmp" -type f 2>/dev/null | grep -q .; then
  fail "rejected tag left a header file behind"
fi

boot_deny="$(mktemp -d)"
mkdir -p "$boot_deny/bin" "$boot_deny/work" "$boot_deny/private"
cat >"$boot_deny/bin/chmod" <<EOF
#!/bin/bash
for arg in "\$@"; do
  case "\$arg" in
    "$boot_deny/work"|"$boot_deny/work"/*)
      echo "cannot change permissions of \$arg: Operation not permitted" >&2
      exit 1
      ;;
  esac
done
exec /bin/chmod "\$@"
EOF
chmod +x "$boot_deny/bin/chmod"
: >"$curl_urls"
env -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$boot_deny/work" \
  GOLIATH_ROOT="$boot_deny/work/goliath" \
  GOLIATH_PRIVATE_DIR="$boot_deny/private" \
  GH_TOKEN="gh-sekret-token" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  PATH="$boot_deny/bin:$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err"
grep -q 'stub-ran' "$out" || fail "bootstrap should run the start script when chmod fails on /work"
grep -q 'was not applied' "$out" || fail "bootstrap should warn when chmod on /work fails"
if grep -q 'cannot create' "$err"; then
  fail "bootstrap should not treat a chmod failure as a missing directory"
fi
[[ -f "$boot_deny/work/goliath/bin/goliath-worker-start.sh" ]] \
  || fail "bootstrap should still install the start script when chmod fails"
[[ -f "$boot_deny/private/github.headers" ]] && fail "GitHub header should be removed"
if find "$boot_deny/work" -type f -print0 | xargs -0 grep -q 'gh-sekret-token' 2>/dev/null; then
  fail "GitHub token was stored on the chmod-rejecting volume"
fi

assemble="$ROOT/.github/workflows/release-assemble.yml"
grep -q 'test -s deploy/runpod/goliath-worker-bootstrap.sh' "$assemble" \
  || fail "release assemble should require the bootstrap script on the tagged commit"
grep -q 'test -s deploy/runpod/goliath-worker-start.sh' "$assemble" \
  || fail "release assemble should require the start script on the tagged commit"
grep -q 'test -s scripts/start_shared_worker.sh' "$assemble" \
  || fail "release assemble should require the shared worker start on the tagged commit"
grep -q 'test -s scripts/check_local_scratch.sh' "$assemble" \
  || fail "release assemble should require the local scratch preflight on the tagged commit"
if grep -q 'RUNPOD_' "$ROOT/scripts/start_shared_worker.sh"; then
  fail "shared worker start must not reference RUNPOD_ variables"
fi
if grep -q '/start.sh' "$ROOT/scripts/start_shared_worker.sh"; then
  fail "shared worker start must not reference /start.sh"
fi

# The template line fetches the bootstrap from a GitHub-shaped API, then the
# bootstrap fetches the start script with curl. Serve both from this host.
paste_mount="$(mktemp -d)"
api_urls="$paste_mount/api.urls"
api_auth="$paste_mount/api.auth"
api_port_file="$paste_mount/api.port"
api_body="$paste_mount/api.body"
api_fail="$paste_mount/api.fail"
api_delay="$paste_mount/api.delay"
api_py="$paste_mount/github_api.py"
: >"$api_urls"
: >"$api_auth"
: >"$api_delay"
python3 - "$boot_releases" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps([
    {"tag_name": "goliathomics-2099.1.0", "draft": True, "prerelease": False},
    {"tag_name": "goliathomics-2099.0.1", "draft": False, "prerelease": True},
    {"tag_name": "v2026.10.2", "draft": False, "prerelease": False},
    {"tag_name": "goliathomics-2026.10.2", "draft": False, "prerelease": False},
]))
PY
cat >"$api_body" <<'EOF'
#!/bin/bash
# goliath-worker fetched bootstrap
printf 'fetched-bootstrap\n'
exit 0
EOF
cat >"$api_py" <<'PY'
import base64, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

port_file, url_file, auth_file, body_file, releases_file, fail_file, delay_file = sys.argv[1:8]
expect = os.environ.get("GOLIATH_TEST_EXPECT_TOKEN", "")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        delay = Path(delay_file).read_text(encoding="utf-8").strip()
        if delay:
            time.sleep(float(delay))
        with open(url_file, "a", encoding="utf-8") as handle:
            handle.write(self.path + "\n")
        auth = self.headers.get("Authorization") or ""
        if expect and auth == "Bearer " + expect:
            Path(auth_file).write_text("bearer\n", encoding="utf-8")
        elif auth:
            Path(auth_file).write_text("other\n", encoding="utf-8")
        if Path(fail_file).exists():
            self.send_response(500)
            self.end_headers()
            return
        if "/releases?" in self.path:
            data = Path(releases_file).read_bytes()
        elif "/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref=" in self.path:
            raw = Path(body_file).read_bytes()
            payload = base64.b64encode(raw).decode("ascii")
            wrapped = "\n".join(payload[i:i + 60] for i in range(0, len(payload), 60))
            data = json.dumps({"encoding": "base64", "content": wrapped}).encode("ascii")
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
Path(port_file).write_text(str(server.server_address[1]), encoding="utf-8")
server.serve_forever()
PY
GOLIATH_TEST_EXPECT_TOKEN="gh-sekret-token" \
  python3 "$api_py" "$api_port_file" "$api_urls" "$api_auth" "$api_body" \
    "$boot_releases" "$api_fail" "$api_delay" &
api_pid=$!
for ((i = 0; i < 50; i++)); do
  [[ -s "$api_port_file" ]] && break
  sleep 0.05
done
[[ -s "$api_port_file" ]] || fail "template API fixture did not start"
api_port="$(cat "$api_port_file")"

run_stub() {
  # Export inside a subshell. A parameter expansion is not an assignment word,
  # and the token stays out of argv.
  (
    export GITHUB_TOKEN=""
    export GH_TOKEN="${STUB_TOKEN:-gh-sekret-token}"
    export GOLIATH_GITHUB_API="http://127.0.0.1:${api_port}"
    export GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics"
    export PATH="$boot_bin:$PATH"
    local extra
    for extra in "$@"; do
      export "${extra?}"
    done
    bash "$stub_cmd" >"$out" 2>"$err"
  )
}

: >"$api_urls"
: >"$api_auth"
printf '1.2\n' >"$api_delay"
GITHUB_TOKEN='' \
  GH_TOKEN="gh-sekret-token" \
  GOLIATH_GITHUB_API="http://127.0.0.1:${api_port}" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  PATH="$boot_bin:$PATH" \
  bash "$stub_cmd" >"$out" 2>"$err" &
stub_pid=$!
seen_py=0
for ((i = 0; i < 40; i++)); do
  for cmdf in /proc/[0-9]*/cmdline; do
    if grep -q -a 'goliath-worker-bootstrap.sh' "$cmdf" 2>/dev/null; then
      seen_py=1
      break
    fi
  done
  [[ "$seen_py" -eq 1 ]] && break
  sleep 0.05
done
[[ "$seen_py" -eq 1 ]] || fail "template stub python did not start"
token_on_cmdline=0
for cmdf in /proc/[0-9]*/cmdline; do
  if grep -q -a 'gh-sekret-token' "$cmdf" 2>/dev/null; then
    token_on_cmdline=1
  fi
done
[[ "$token_on_cmdline" -eq 0 ]] || fail "GH_TOKEN appeared on a process command line"
set +e
wait "$stub_pid"
status=$?
set -e
[[ "$status" -eq 0 ]] || fail "template stub should run the fetched bootstrap (status $status)"
grep -q 'fetched-bootstrap' "$out" || fail "template stub should run the script it downloaded"
grep -q '/releases?per_page=100' "$api_urls" || fail "unset tag should list GitHub releases"
grep -q 'ref=goliathomics-2026.10.2' "$api_urls" || fail "stub should select the newest non-draft goliathomics release"
if grep -q 'goliathomics-2099' "$api_urls"; then
  fail "stub selected a draft or prerelease tag"
fi
if grep -q 'ref=v2026.10.2' "$api_urls"; then
  fail "stub selected a release outside the goliathomics prefix"
fi
[[ "$(cat "$api_auth")" == "bearer" ]] || fail "stub should send a bearer header"
if grep -q 'gh-sekret-token' "$out" "$err" "$api_urls"; then
  fail "template stub printed GH_TOKEN"
fi
: >"$api_delay"

: >"$api_urls"
run_stub GOLIATH_RELEASE_TAG="goliathomics-9.9.9"
grep -q 'ref=goliathomics-9.9.9' "$api_urls" || fail "pinned tag should be the bootstrap contents ref"
if grep -q '/releases?per_page=' "$api_urls"; then
  fail "pinned tag should not list releases"
fi
grep -q 'fetched-bootstrap' "$out" || fail "pinned tag should still run the fetched bootstrap"

keep_stub="$paste_mount/keep.sh"
keep_marker="$paste_mount/kept"
cat >"$keep_stub" <<EOF
#!/bin/bash
printf 'stub-kept\n' >"$keep_marker"
exit 0
EOF
chmod +x "$keep_stub"
: >"$api_urls"
rm -f "$api_fail"
touch "$api_fail"
run_stub GOLIATH_START_KEEPALIVE="$keep_stub"
rm -f "$api_fail"
grep -q 'stub-kept' "$keep_marker" || fail "stub failure should exec /start.sh"
grep -q 'goliath-stub-failed' "$err" || fail "stub failure should log goliath-stub-failed"
if grep -q 'gh-sekret-token' "$out" "$err"; then
  fail "failed stub printed GH_TOKEN"
fi

: >"$api_urls"
rm -f "$keep_marker"
run_stub GOLIATH_RELEASE_TAG="sekret-not-a-tag" GOLIATH_START_KEEPALIVE="$keep_stub"
grep -q 'stub-kept' "$keep_marker" || fail "unsafe release tag should fall back to /start.sh"
if grep -q 'sekret-not-a-tag' "$out" "$err" "$api_urls"; then
  fail "stub printed or requested the rejected release tag"
fi
if grep -q 'gh-sekret-token' "$out" "$err"; then
  fail "rejected tag stub printed GH_TOKEN"
fi

: >"$api_urls"
rm -f "$keep_marker"
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_GITHUB_API="http://127.0.0.1:${api_port}" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  GOLIATH_START_KEEPALIVE="$keep_stub" \
  PATH="$boot_bin:$PATH" \
  bash "$stub_cmd" >"$out" 2>"$err"
grep -q 'stub-kept' "$keep_marker" || fail "missing GH_TOKEN should fall back to /start.sh"
[[ ! -s "$api_urls" ]] || fail "missing GH_TOKEN should not call GitHub"
if grep -q 'gh-sekret-token' "$out" "$err"; then
  fail "missing token stub printed GH_TOKEN"
fi

: >"$api_urls"
rm -f "$keep_marker"
GH_TOKEN=$'line1\nline2' \
  GITHUB_TOKEN='' \
  GOLIATH_GITHUB_API="http://127.0.0.1:${api_port}" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  GOLIATH_START_KEEPALIVE="$keep_stub" \
  PATH="$boot_bin:$PATH" \
  bash "$stub_cmd" >"$out" 2>"$err"
grep -q 'stub-kept' "$keep_marker" || fail "a GH_TOKEN with a newline should fall back to /start.sh"
[[ ! -s "$api_urls" ]] || fail "a GH_TOKEN with a newline should not call GitHub"
if grep -q 'line1' "$out" "$err"; then
  fail "stub printed a GH_TOKEN that contained a newline"
fi

printf '#!/bin/bash\nprintf pwned\\n\n' >"$api_body"
rm -f "$keep_marker"
: >"$api_urls"
run_stub GOLIATH_RELEASE_TAG="goliathomics-2026.10.2" GOLIATH_START_KEEPALIVE="$keep_stub"
grep -q 'stub-kept' "$keep_marker" || fail "a download that is not the bootstrap should fall back to /start.sh"
if grep -q 'pwned' "$out" "$err"; then
  fail "stub executed a file that was not the bootstrap"
fi

# The README line is the same command. It should download the real bootstrap,
# and that bootstrap should download and run the start script.
cp "$boot" "$api_body"
mkdir -p "$paste_mount"
: >"$curl_urls"
: >"$curl_args"
: >"$api_urls"
GH_TOKEN="gh-sekret-token" \
  GOLIATH_GITHUB_API="http://127.0.0.1:${api_port}" \
  GOLIATH_GITHUB_REPO="Goliath-Research/GoliathOmics" \
  GOLIATH_RELEASE_TAG="goliathomics-2026.10.2" \
  GOLIATH_WORK_MOUNT="$paste_mount" \
  GOLIATH_ROOT="$paste_mount/goliath" \
  PATH="$boot_bin:$PATH" \
  bash "$stub_cmd" >"$out" 2>"$err"
grep -q 'stub-ran' "$out" || fail "template command should download the bootstrap and run the start script"
grep -q 'ref=goliathomics-2026.10.2' "$api_urls" || fail "template command should fetch the bootstrap at the pinned tag"
grep -q 'goliath-worker-start.sh' "$curl_urls" || fail "bootstrap started by the template should fetch the start script"
if grep -q 'gh-sekret-token' "$out" "$err" "$curl_args" "$api_urls"; then
  fail "template command printed GH_TOKEN"
fi
kill "$api_pid" 2>/dev/null || true
set +e
wait "$api_pid"
set -e
api_pid=""

stop_mount="$(mktemp -d)"
mkdir -p "$stop_mount/goliath/bin"
cat >"$stop_mount/goliath/bin/goliath-worker-start.sh" <<'EOF'
#!/usr/bin/env bash
# goliath-worker-start sleeper
printf '%s\n' "$$" >"${STOP_PID:?}"
exec sleep 30
EOF
chmod 755 "$stop_mount/goliath/bin/goliath-worker-start.sh"
stop_pidfile="$stop_mount/child.pid"
env -u GH_TOKEN -u GITHUB_TOKEN \
  GOLIATH_WORK_MOUNT="$stop_mount" \
  GOLIATH_ROOT="$stop_mount/goliath" \
  STOP_PID="$stop_pidfile" \
  PATH="$boot_bin:$PATH" \
  bash "$boot" >"$out" 2>"$err" &
boot_pid=$!
for ((i = 0; i < 50; i++)); do
  [[ -s "$stop_pidfile" ]] && break
  sleep 0.1
done
[[ -s "$stop_pidfile" ]] || fail "bootstrap did not start the worker script"
kill -TERM "$boot_pid"
set +e
wait "$boot_pid"
status=$?
set -e
[[ "$status" -eq 0 ]] || fail "SIGTERM should stop the bootstrap without a failing exit (status $status)"
if kill -0 "$(cat "$stop_pidfile")" 2>/dev/null; then
  kill "$(cat "$stop_pidfile")" 2>/dev/null || true
  fail "SIGTERM should stop the start script"
fi

prov_root="$(mktemp -d)"
prov_local="$(mktemp -d)"
mkdir -p "$prov_local/venv-amd64/bin" "$prov_root/current"
printf '#!/bin/sh\nexit 0\n' >"$prov_local/venv-amd64/bin/python"
chmod +x "$prov_local/venv-amd64/bin/python"
env WORKER_API_BASE="https://45.76.21.8/v1" GOLIATH_LOCAL_ROOT="$prov_local" \
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
bash "$ROOT/scripts/test_setup_gateway_nginx.sh"
bash "$ROOT/scripts/test_install_gateway_venv.sh"
bash "$ROOT/scripts/test_worker_venv_local.sh"
echo "test_deploy_target ok"
