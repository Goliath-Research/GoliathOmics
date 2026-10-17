#!/usr/bin/env bash
# Contract tests for the provider-neutral deploy/workers/create_worker.sh.
# Uses a fake provider (no cloud, no network) to prove the generic flow: name
# selection, the /work policy, the --yes gate, --dry-run, mount verification
# after create, the summary, and that unimplemented hooks fail clearly.
# The Runpod provider is covered by scripts/test_create_worker_pod.sh.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/deploy/workers/create_worker.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

PROV="$TMP/providers"
STATE="$TMP/state"
mkdir -p "$PROV" "$STATE"
cp "$ROOT/deploy/workers/providers/_interface.sh" "$PROV/"

cat >"$PROV/fake.sh" <<'FAKE'
# Fake provider: state in $FAKE_STATE, behavior from FAKE_* env vars.
provider_defaults() {
  PROVIDER_DEFAULT_TEMPLATE=img-default PROVIDER_DEFAULT_STORAGE=share
  PROVIDER_DEFAULT_GPU=gpu-x PROVIDER_DEFAULT_CLOUD=""
}
provider_require_env() { [[ -n "${FAKE_TOKEN:-}" ]] || die "FAKE_TOKEN is not set"; }
provider_resolve_template() { TEMPLATE_ID="tpl-$OPT_TEMPLATE"; }
provider_resolve_storage() {
  [[ "$OPT_STORAGE" == share ]] || die "storage '$OPT_STORAGE' not found"
  STORAGE_ID=vol-123
}
provider_list_worker_names() { printf '%s\n' ${FAKE_NAMES:-}; }
provider_price() { log "price: fake \$0.50/hr"; }
provider_build_request() {
  jq -n --arg n "$WORKER_NAME" --arg t "$TEMPLATE_ID" --arg s "$STORAGE_ID" --arg p "$MOUNT_PATH" \
    --arg g "$OPT_GPU" --argjson c "$OPT_GPU_COUNT" \
    '{name:$n, image:$t, gpu:$g, count:$c, storage:{id:$s, at:$p}}' >"$1"
  [[ -z "${FAKE_BAD_REQUEST:-}" ]] || jq '.storage = null' "$1" >"$1.x" && mv "$1.x" "$1" 2>/dev/null || true
}
provider_check_request() { jq -e --arg s "$STORAGE_ID" --arg p "$MOUNT_PATH" '.storage.id == $s and .storage.at == $p' "$1" >/dev/null; }
provider_request_target() { printf 'fake create'; }
provider_create() { echo create >>"$FAKE_STATE/calls"; cp "$1" "$FAKE_STATE/created.json"; WORKER_ID=w-42; }
provider_describe() {
  echo "describe $1" >>"$FAKE_STATE/calls"
  W_NAME="$(jq -r .name "$FAKE_STATE/created.json")"; W_STATUS=RUNNING
  W_MOUNTS="${FAKE_MOUNTS-vol-123:/work}"; W_MOUNTS_RAW="[$W_MOUNTS]"
  W_SSH="ssh root@198.51.100.7"; W_PUBLIC_IP=198.51.100.7
}
provider_wait_ready() { :; }
provider_terminate_hint() { echo "fake-cli terminate $1"; }
FAKE

# A provider that only overrides some hooks.
cat >"$PROV/partial.sh" <<'P'
provider_defaults() { PROVIDER_DEFAULT_STORAGE=share; }
provider_require_env() { :; }
P

run() {
  local name="$1"; shift
  rm -f "$STATE"/*
  set +e
  env CREATE_WORKER_PROVIDERS_DIR="$PROV" FAKE_STATE="$STATE" FAKE_TOKEN="${TOKEN-tok}" \
    CREATE_WORKER_WAIT_SECS=0 CREATE_WORKER_DESCRIBE_RETRY_SECS=0 \
    bash "$SCRIPT" "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"
  echo $? >"$TMP/$name.rc"
  set -e
}
rc() { cat "$TMP/$1.rc"; }
created() { [[ -f "$STATE/created.json" ]]; }

# 1. --provider is required; unknown provider fails.
run noprov --dry-run
[[ "$(rc noprov)" != 0 ]] && grep -q -- '--provider is required' "$TMP/noprov.err" || fail "--provider must be required"
run badprov --provider nope --dry-run
[[ "$(rc badprov)" != 0 ]] && grep -q "unknown provider 'nope'" "$TMP/badprov.err" || fail "unknown provider must fail"
grep -q 'fake' "$TMP/badprov.err" || fail "unknown provider should list available ones"

# 2. Dry run: next free name, request printed, nothing created.
FAKE_NAMES="goliath-worker-1 goliath-worker-3 other" run dry --provider fake --dry-run
[[ "$(rc dry)" == 0 ]] || { cat "$TMP/dry.err"; fail "dry run should succeed"; }
created && fail "dry run must not create"
jq -e '.name == "goliath-worker-2" and .storage == {"id":"vol-123","at":"/work"} and .image == "tpl-img-default"' "$TMP/dry.out" >/dev/null \
  || { cat "$TMP/dry.out"; fail "dry-run request"; }
grep -q 'price: fake' "$TMP/dry.err" || fail "price must be shown"

# 3. Provider env check runs; failures stop before anything else.
TOKEN="" run notok --provider fake --dry-run
[[ "$(rc notok)" != 0 ]] && grep -q 'FAKE_TOKEN is not set' "$TMP/notok.err" || fail "provider_require_env must run"

# 4. Mount path policy.
run path --provider fake --dry-run --mount-path /data
[[ "$(rc path)" != 0 ]] && grep -q 'allow-nonstandard-path' "$TMP/path.err" || fail "nonstandard path must be refused"
run path2 --provider fake --dry-run --mount-path /data/ --allow-nonstandard-path
[[ "$(rc path2)" == 0 ]] && jq -e '.storage.at == "/data"' "$TMP/path2.out" >/dev/null || fail "override path"
run root --provider fake --dry-run --mount-path / --allow-nonstandard-path
[[ "$(rc root)" != 0 ]] || fail "/ must be refused"

# 5. Request without the mount is never sent.
FAKE_BAD_REQUEST=1 run badreq --provider fake --yes
[[ "$(rc badreq)" != 0 ]] && grep -q 'request lacks the shared storage' "$TMP/badreq.err" || fail "request check"
created && fail "bad request must not be created"

# 6. --yes gate.
run noyes --provider fake
[[ "$(rc noyes)" != 0 ]] && grep -q 'billable' "$TMP/noyes.err" || fail "--yes gate"
created && fail "no create without --yes"

# 7. Create, verify, summary.
FAKE_NAMES="goliath-worker-1" run create --provider fake --yes
[[ "$(rc create)" == 0 ]] || { cat "$TMP/create.err"; fail "create should succeed"; }
grep -q '^describe w-42$' "$STATE/calls" || fail "worker must be described after create"
for want in 'pod_id=w-42' 'name=goliath-worker-2' 'status=RUNNING' 'mount=vol-123:/work' \
            'ssh=ssh root@198.51.100.7' 'public_ip=198.51.100.7'; do
  grep -qxF "$want" "$TMP/create.out" || { cat "$TMP/create.out"; fail "missing output: $want"; }
done

# 8. Worker comes back with the storage elsewhere: exit 3, terminate advice, no delete.
FAKE_MOUNTS="vol-123:/workspace" run badmount --provider fake --yes
[[ "$(rc badmount)" == 3 ]] || fail "wrong mount must exit 3 (got $(rc badmount))"
grep -q 'Terminate it' "$TMP/badmount.err" && grep -q 'fake-cli terminate w-42' "$TMP/badmount.err" \
  || { cat "$TMP/badmount.err"; fail "terminate advice"; }
[[ ! -s "$TMP/badmount.out" ]] || fail "no summary on mount failure"
FAKE_MOUNTS="" run nomount --provider fake --yes
[[ "$(rc nomount)" == 3 ]] || fail "missing mount must exit 3"

# 9. Duplicate --name refused; bad names refused.
FAKE_NAMES="goliath-worker-1" run dup --provider fake --dry-run --name goliath-worker-1
[[ "$(rc dup)" != 0 ]] && grep -q 'already exists' "$TMP/dup.err" || fail "duplicate name"
run badname --provider fake --dry-run --name 'a b'
[[ "$(rc badname)" != 0 ]] || fail "invalid name"

# 10. Unimplemented hooks fail clearly (abstract base).
run partial --provider partial --dry-run
[[ "$(rc partial)" != 0 ]] && grep -q 'provider_resolve_template not implemented by provider partial' "$TMP/partial.err" \
  || { cat "$TMP/partial.err"; fail "unimplemented hook message"; }

# 11. Every real provider overrides every hook; generic code is provider-free.
# shellcheck disable=SC1091
source "$ROOT/deploy/workers/providers/_interface.sh"
for f in "$ROOT"/deploy/workers/providers/*.sh; do
  [[ "$(basename "$f")" == _* ]] && continue
  for hook in "${PROVIDER_HOOKS[@]}"; do
    grep -qE "^${hook}\(\)" "$f" || fail "$(basename "$f") does not implement $hook"
  done
done
if grep -inE 'runpod|lambda|nebius|aws|azure|gcp|google|https?://|/v2/' "$SCRIPT" "$ROOT/deploy/workers/providers/_interface.sh"; then
  fail "generic code must not mention providers, URLs or API paths"
fi

echo "PASS: create_worker (generic, fake provider)"
