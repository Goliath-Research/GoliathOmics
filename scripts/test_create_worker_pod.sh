#!/usr/bin/env bash
# Contract tests for the Runpod provider (deploy/workers/providers/runpod.sh),
# run through the deploy/runpod/create_worker_pod.sh wrapper and directly via
# create_worker.sh --provider runpod. No network: curl is a
# PATH stub that answers the Runpod v2 API from fixtures.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/deploy/runpod/create_worker_pod.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

FAKE_KEY="rpa_TESTKEY0123456789"
BIN="$TMP/bin"
FIX="$TMP/fix"
LOG="$TMP/log"
mkdir -p "$BIN" "$FIX" "$LOG"

cat >"$BIN/curl" <<'STUB'
#!/usr/bin/env bash
out="" fmt="" method=GET data="" url="" cfg=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -w) fmt="$2"; shift 2 ;;
    -X) method="$2"; shift 2 ;;
    -K) cfg="$2"; shift 2 ;;
    --data-binary) data="$2"; shift 2 ;;
    -H|--connect-timeout|--max-time) shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
printf '%s\n' "$*" >>"$CURL_LOG/argv"
printf '%s %s\n' "$method" "${url#"$RUNPOD_API_BASE"}" >>"$CURL_LOG/requests"
if [[ -n "$cfg" ]] && grep -q 'Authorization: Bearer ' "$cfg"; then echo yes; else echo no; fi >>"$CURL_LOG/auth"
path="${url#"$RUNPOD_API_BASE"}"; path="${path%%\?*}"
code=200 file=""
case "$method $path" in
  "GET /v2/templates") file=templates.json ;;
  "GET /v2/templates/"*) file="template_${path##*/}.json"; [[ -f "$FIXTURES/$file" ]] || code=404 ;;
  "GET /v2/global-volumes") file=volumes.json ;;
  "GET /v2/pods") file=pods.json ;;
  "GET /v2/pods/"*) file=pod_get.json ;;
  "GET /v2/catalog/gpus/"*) file=gpu.json ;;
  "POST /v2/pods") file=pod_created.json; code=201; cp "${data#@}" "$CURL_LOG/post_body.json" ;;
  *) code=599 ;;
esac
if [[ -n "$file" && -f "$FIXTURES/$file" ]]; then cat "$FIXTURES/$file" >"$out"; else printf '{"title":"Not Found"}' >"$out"; fi
[[ "$fmt" == "%{http_code}" ]] && printf '%s' "$code"
exit 0
STUB
chmod +x "$BIN/curl"

write_fixtures() {
  cat >"$FIX/templates.json" <<'J'
{"templates":[{"id":"tplother","name":"other","serverless":false,"mounts":{}},
 {"id":"tplsrv","name":"goliath-worker","serverless":true,"mounts":{}},
 {"id":"nw0judtfil","name":"goliath-worker","serverless":false,"mounts":{}}],
 "pagination":{"hasNextPage":false,"nextCursor":null}}
J
  cat >"$FIX/template_nw0judtfil.json" <<'J'
{"id":"nw0judtfil","name":"goliath-worker","serverless":false,"mounts":{},"image":"runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404","disk":30}
J
  cat >"$FIX/volumes.json" <<'J'
{"globalVolumes":[{"id":"gvolother","name":"scratch"},{"id":"cmueim6m6000006i69kwh1xax","name":"work"}],
 "pagination":{"hasNextPage":false,"nextCursor":null}}
J
  cat >"$FIX/pods.json" <<'J'
{"pods":[{"id":"p1","name":"goliath-worker-1"},{"id":"p3","name":"goliath-worker-3"}],
 "pagination":{"hasNextPage":false,"nextCursor":null}}
J
  cat >"$FIX/gpu.json" <<'J'
{"id":"NVIDIA L40S","name":"L40S","secure":true,"community":true,"price":{"secure":1.09,"community":0.79}}
J
  cat >"$FIX/pod_created.json" <<'J'
{"id":"podnew123","name":"goliath-worker-2","status":"PROVISIONING","cost":1.09}
J
  cat >"$FIX/pod_get.json" <<'J'
{"id":"podnew123","name":"goliath-worker-2","status":"RUNNING",
 "mounts":{"global":[{"volumeId":"cmueim6m6000006i69kwh1xax","path":"/work"}]},
 "ssh":{"proxy":{"command":"ssh podnew123-abc@ssh.runpod.io"},"direct":{"host":"203.0.113.9","port":40022,"username":"root","command":"ssh root@203.0.113.9 -p 40022"}},
 "runtime":{"ports":[{"private":22,"public":40022,"type":"tcp","ip":"203.0.113.9"}]}}
J
}

# run NAME [args...]: runs the script with the stub; output in $TMP/NAME.out,
# exit status in $TMP/NAME.rc.
run() {
  local name="$1"; shift
  rm -f "$LOG"/*
  set +e
  env PATH="$BIN:$PATH" CURL_LOG="$LOG" FIXTURES="$FIX" \
    RUNPOD_API_BASE="https://api.test" RUNPOD_API_KEY="${KEY-$FAKE_KEY}" \
    CREATE_WORKER_POD_WAIT_SECS=0 CREATE_WORKER_POD_POLL_SECS=1 \
    bash "$SCRIPT" "$@" >"$TMP/$name.out" 2>&1
  echo $? >"$TMP/$name.rc"
  set -e
}
rc() { cat "$TMP/$1.rc"; }
no_post() { ! grep -q '^POST' "$LOG/requests" 2>/dev/null; }
no_key_leak() {
  ! grep -qF "$FAKE_KEY" "$TMP/$1.out" || fail "$1: API key printed"
  ! grep -qF "$FAKE_KEY" "$LOG/argv" 2>/dev/null || fail "$1: API key on the curl command line"
}

write_fixtures

# 1. Dry run: body carries the global volume at /work, resolved by name.
run dry --dry-run
[[ "$(rc dry)" == 0 ]] || { cat "$TMP/dry.out"; fail "dry run should succeed"; }
no_post || fail "dry run must not POST"
no_key_leak dry
grep -qx yes "$LOG/auth" || fail "requests must send the key via the curl config"
grep -q 'no' "$LOG/auth" && fail "every request must carry auth"
body="$(sed -n '/^{/,/^}/p' "$TMP/dry.out")"
jq -e '.mounts.global == [{"volumeId":"cmueim6m6000006i69kwh1xax","path":"/work"}]' <<<"$body" >/dev/null \
  || { echo "$body"; fail "dry-run body must mount global volume work at /work"; }
jq -e '.templateId == "nw0judtfil" and .name == "goliath-worker-2" and .cloud == "SECURE"
       and .gpu == {"id":"NVIDIA L40S","count":1} and .disk == 200 and (.mounts | keys) == ["global"]' <<<"$body" >/dev/null \
  || { echo "$body"; fail "dry-run body fields (template, next free name, gpu, cloud, disk)"; }
grep -q 'template disk is 30 GB; requesting container disk 200 GB (containerDiskInGb)' "$TMP/dry.out" \
  || fail "a 30 GB template disk must be overridden by local_disk_gb"
grep -qF "price: NVIDIA L40S on SECURE cloud: \$1.09/hr" "$TMP/dry.out" || fail "price should be printed"

# 2. Missing key fails before any request.
KEY="" run nokey --dry-run
[[ "$(rc nokey)" != 0 ]] || fail "missing RUNPOD_API_KEY must fail"
grep -q 'RUNPOD_API_KEY is not set' "$TMP/nokey.out" || fail "missing key message"
[[ ! -s "$LOG/requests" ]] || fail "missing key must not call the API"

# 3. Unknown global volume fails.
run novol --dry-run --volume nope
[[ "$(rc novol)" != 0 ]] || fail "unknown volume must fail"
grep -q "global volume 'nope' not found" "$TMP/novol.out" || fail "missing volume message"
cp "$FIX/volumes.json" "$TMP/volumes.bak"
printf '{"globalVolumes":[],"pagination":{"hasNextPage":false,"nextCursor":null}}' >"$FIX/volumes.json"
run novol2 --dry-run
[[ "$(rc novol2)" != 0 ]] || fail "default volume 'work' missing must fail"
cp "$TMP/volumes.bak" "$FIX/volumes.json"

# 4. Unknown template fails; a template with a persistent mount fails.
run notpl --dry-run --template nope
[[ "$(rc notpl)" != 0 ]] || fail "unknown template must fail"
grep -q "pod template 'nope' not found" "$TMP/notpl.out" || fail "missing template message"
cp "$FIX/template_nw0judtfil.json" "$TMP/tpl.bak"
jq '.mounts = {"persistent":{"size":20,"path":"/workspace"}}' "$TMP/tpl.bak" >"$FIX/template_nw0judtfil.json"
run persist --dry-run
[[ "$(rc persist)" != 0 ]] || fail "template with a persistent mount must fail"
cp "$TMP/tpl.bak" "$FIX/template_nw0judtfil.json"

# 5. A mount path other than /work needs --allow-nonstandard-path.
run path --dry-run --mount-path /workspace
[[ "$(rc path)" != 0 ]] || fail "nonstandard mount path must fail without the override"
run path2 --dry-run --mount-path /workspace --allow-nonstandard-path
[[ "$(rc path2)" == 0 ]] || { cat "$TMP/path2.out"; fail "override should allow the path"; }
sed -n '/^{/,/^}/p' "$TMP/path2.out" | jq -e '.mounts.global[0].path == "/workspace"' >/dev/null || fail "override path in body"

# 6. Without --yes nothing is created.
run noyes
[[ "$(rc noyes)" != 0 ]] || fail "create without --yes must fail"
no_post || fail "no POST without --yes"

# 7. --yes creates, verifies the mount, prints id/name/ssh/ip.
run create --yes --name goliath-worker-2
[[ "$(rc create)" == 0 ]] || { cat "$TMP/create.out"; fail "create should succeed"; }
no_key_leak create
jq -e '.mounts.global == [{"volumeId":"cmueim6m6000006i69kwh1xax","path":"/work"}]' "$LOG/post_body.json" >/dev/null \
  || fail "POSTed body must carry the global mount"
grep -q '^GET /v2/pods/podnew123$' "$LOG/requests" || fail "pod must be read back after create"
for want in 'pod_id=podnew123' 'name=goliath-worker-2' 'ssh=ssh root@203.0.113.9 -p 40022' 'public_ip=203.0.113.9'; do
  grep -qxF "$want" "$TMP/create.out" || { cat "$TMP/create.out"; fail "missing output: $want"; }
done

# 8. Pod comes back without the global mount: error, exit non-zero, no delete.
jq '.mounts = {}' "$FIX/pod_get.json" >"$TMP/pod.json" && cp "$TMP/pod.json" "$FIX/pod_get.json"
run badmount --yes
[[ "$(rc badmount)" != 0 ]] || fail "missing mount after create must fail"
grep -q 'Terminate it' "$TMP/badmount.out" || fail "should tell the user to terminate the pod"
grep -q '^DELETE' "$LOG/requests" && fail "must not terminate the pod automatically"

# 9. An existing --name is refused.
run dup --dry-run --name goliath-worker-1
[[ "$(rc dup)" != 0 ]] || fail "duplicate name must fail"

# 10. --image and --registry-auth land on the v2 create body.
run imgreg --dry-run --image ghcr.io/example/goliath-worker:t --registry-auth reg_test123
[[ "$(rc imgreg)" == 0 ]] || { cat "$TMP/imgreg.out"; fail "image+registry dry-run should succeed"; }
imgreg_body="$(sed -n '/^{/,/^}/p' "$TMP/imgreg.out")"
jq -e '.image == "ghcr.io/example/goliath-worker:t" and .registry == "reg_test123"' <<<"$imgreg_body" >/dev/null \
  || { echo "$imgreg_body"; fail "body must carry v2 image + registry"; }

# 11. --local-disk-gb is the v2 disk field (containerDiskInGb). Below 100 is refused
# before any create, and the value overrides the template's 30 GB disk.
run tinyd --dry-run --local-disk-gb 30
[[ "$(rc tinyd)" != 0 ]] || fail "30 GB container disk must be refused"
grep -q 'at least 100' "$TMP/tinyd.out" || { cat "$TMP/tinyd.out"; fail "minimum disk message"; }
no_post || fail "a too-small disk must not POST"
run disk150 --dry-run --local-disk-gb 150
[[ "$(rc disk150)" == 0 ]] || { cat "$TMP/disk150.out"; fail "150 GB disk should be accepted"; }
sed -n '/^{/,/^}/p' "$TMP/disk150.out" | jq -e '.disk == 150' >/dev/null \
  || fail "create body disk must follow --local-disk-gb"
grep -q 'requesting container disk 150 GB (containerDiskInGb)' "$TMP/disk150.out" \
  || fail "template disk warning should name the requested size"

# 12. The generic entry point gives the identical body.

SCRIPT="$ROOT/deploy/workers/create_worker.sh"
run generic --provider runpod --dry-run
[[ "$(rc generic)" == 0 ]] || { cat "$TMP/generic.out"; fail "create_worker.sh --provider runpod should succeed"; }
[[ "$(sed -n '/^{/,/^}/p' "$TMP/generic.out")" == "$body" ]] || fail "generic and wrapper bodies differ"

echo "PASS: create_worker_pod (runpod provider)"
