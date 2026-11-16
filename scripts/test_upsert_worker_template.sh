#!/usr/bin/env bash
# Mocked tests for deploy/runpod/upsert_worker_template.py.
# Starts a local HTTP server that speaks the Runpod REST v2 template
# endpoints. No network, no real API key, no POST/PATCH in --dry-run cases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/deploy/runpod/upsert_worker_template.py"
[[ -f "$SCRIPT" ]] || { echo "missing $SCRIPT" >&2; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-upsert-worker-template.XXXXXX")"
cleanup() { if [[ -n "${SERVER_PID:-}" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi; rm -rf "$TMP"; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

python3 -m py_compile "$SCRIPT" || fail "script does not compile"

# 1. Missing key fails a write (not --dry-run).
env -u RUNPOD_API_KEY python3 "$SCRIPT" >"$TMP/nokey.out" 2>"$TMP/nokey.err" && fail "missing RUNPOD_API_KEY must fail"
grep -q 'set RUNPOD_API_KEY' "$TMP/nokey.err" || fail "missing key message"

# 2. --dry-run without a key prints the planned v2 body and does not need the API.
env -u RUNPOD_API_KEY python3 "$SCRIPT" --dry-run >"$TMP/dry.out" 2>"$TMP/dry.err" || fail "dry-run without key should succeed"
grep -q 'RUNPOD_API_KEY unset' "$TMP/dry.out" || fail "dry-run should say the key is unset"
python3 - <<'PY' "$TMP/dry.out"
import json, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
body = json.loads(text.split("dry-run:", 1)[0])
assert body["name"] == "goliath-worker"
assert body["image"].startswith("ghcr.io/goliath-research/goliath-worker:")
assert "imageName" not in body
assert "containerDiskInGb" not in body
assert "dockerStartCmd" not in body
assert "isPublic" not in body
assert "isServerless" not in body
assert "mounts" not in body, "templates cannot carry a global volume; omit mounts"
assert "args" not in body, "send cmd only; args is derived"
assert body["cmd"][0:2] == ["bash", "-lc"]
assert "goliath-worker-bootstrap.sh" in body["cmd"][2]
assert body["disk"] == 30
assert body["public"] is False
assert body["serverless"] is False
assert body["startJupyter"] is False
assert body["startSsh"] is False
assert body["env"]["WORKER_API_BASE"] == "https://gateway.goliathresearch.com/v1"
assert "GOLIATH_GATEWAY_CA_PEM" not in body["env"]
assert "NGC_API_KEY" in body["env"]
blob = json.dumps(body)
assert "45.76" not in blob
assert "rest.runpod.io" not in blob
print("dry-run body ok")
PY
[[ ! -s "$TMP/dry.err" ]] || fail "dry-run without key wrote stderr: $(cat "$TMP/dry.err")"

# 3. Local v2 mock: pagination, update vs create, dry-run does not write.
PORT_FILE="$TMP/port"
REQS="$TMP/requests.log"
: >"$REQS"
cat >"$TMP/server.py" <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

root = os.environ["MOCK_ROOT"]
mode = os.environ["MOCK_MODE"]  # update | create | paged
log_path = os.path.join(root, "requests.log")
state_path = os.path.join(root, "state.json")

TPL = {
    "id": "nw0judtfil",
    "name": "goliath-worker",
    "image": "ghcr.io/goliath-research/goliath-worker:4.7.0-2",
    "disk": 30,
    "ports": ["8888/http", "22/tcp"],
    "cmd": ["bash", "-lc", "placeholder"],
    "env": {"WORKER_API_BASE": "https://gateway.goliathresearch.com/v1"},
    "registry": "cmvfgr6i1003bl3r5rmi350hr",
    "public": False,
    "serverless": False,
    "category": "NVIDIA",
    "startSsh": False,
    "startJupyter": False,
    "allowedCudaVersions": ["12.8", "12.9", "13.0", "13.1", "13.2", "13.3", "13.4"],
    "mounts": {},
    "args": "{\"cmd\":[]}",
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _record(self, extra=""):
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("%s %s%s\n" % (self.command, self.path, extra))

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if raw:
            dest = os.path.join(root, "last_body.json")
            dest.write(raw) if False else open(dest, "wb").write(raw)
        return raw

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record()
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/v2/templates":
            if mode == "paged":
                cursor = (qs.get("cursor") or [""])[0]
                if not cursor:
                    self._send(200, {
                        "templates": [{"id": "other", "name": "other", "serverless": False}],
                        "pagination": {"hasNextPage": True, "nextCursor": "page2"},
                    })
                    return
                if cursor == "page2":
                    self._send(200, {
                        "templates": [TPL],
                        "pagination": {"hasNextPage": False, "nextCursor": None},
                    })
                    return
                self._send(400, {"title": "Bad Request", "status": 400, "detail": "unknown cursor"})
                return
            if mode == "create":
                self._send(200, {"templates": [{"id": "srv", "name": "goliath-worker", "serverless": True}],
                                 "pagination": {"hasNextPage": False, "nextCursor": None}})
                return
            self._send(200, {"templates": [TPL],
                             "pagination": {"hasNextPage": False, "nextCursor": None}})
            return
        if parsed.path.startswith("/v2/templates/") and parsed.path != "/v2/templates/":
            tid = parsed.path.rsplit("/", 1)[-1]
            if tid == "nw0judtfil":
                self._send(200, TPL)
                return
            if tid == "newtpl1":
                created = dict(TPL)
                created["id"] = "newtpl1"
                self._send(200, created)
                return
            self._send(404, {"title": "Not Found", "status": 404, "detail": "no such template"})
            return
        self._send(404, {"title": "Not Found", "status": 404, "detail": "no such path"})

    def do_PATCH(self):
        raw = self._read_body()
        self._record(" body_bytes=" + str(len(raw)))
        self._send(200, TPL)

    def do_POST(self):
        raw = self._read_body()
        self._record(" body_bytes=" + str(len(raw)))
        created = dict(TPL)
        created["id"] = "newtpl1"
        self._send(201, created)

httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
sys.stdout.write("%s\n" % httpd.server_address[1])
sys.stdout.flush()
httpd.serve_forever()
PY

start_mock() {
  local mode="$1"
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
  : >"$REQS"
  rm -f "$TMP/last_body.json"
  MOCK_ROOT="$TMP" MOCK_MODE="$mode" python3 "$TMP/server.py" >"$PORT_FILE" &
  SERVER_PID=$!
  local i port
  for i in $(seq 1 50); do
    port="$(tr -d '[:space:]' <"$PORT_FILE" 2>/dev/null || true)"
    if [[ "$port" =~ ^[0-9]+$ ]]; then
      echo "$port"
      return 0
    fi
    sleep 0.05
  done
  fail "mock server did not print a port"
}

run_script() {
  local out="$1"; shift
  RUNPOD_API_KEY="test-key-not-real" \
    RUNPOD_API_BASE="http://127.0.0.1:${PORT}" \
    python3 "$SCRIPT" "$@" >"$out" 2>"$out.err" || return $?
}

PORT="$(start_mock update)"

# dry-run against a paged list must follow the cursor and not write
PORT="$(start_mock paged)"
run_script "$TMP/page.out" --dry-run || fail "paged dry-run failed: $(cat "$TMP/page.out.err")"
grep -q 'would PATCH /v2/templates/nw0judtfil' "$TMP/page.out" || fail "paged dry-run should target the live id"
grep -v '^$' "$REQS" | grep -q 'PATCH\|POST' && fail "dry-run must not PATCH or POST"
grep -q 'GET /v2/templates?' "$REQS" || fail "dry-run should list templates"
grep -q 'cursor=page2' "$REQS" || fail "dry-run should follow pagination.nextCursor"
grep -q 'GET /v2/templates/nw0judtfil' "$REQS" || fail "dry-run should GET the existing template"

# write path: existing template is PATCH
PORT="$(start_mock update)"
run_script "$TMP/upd.out" || fail "update run failed: $(cat "$TMP/upd.out.err")"
grep -q 'updated nw0judtfil' "$TMP/upd.out" || fail "update should print updated id"
grep -q '^PATCH /v2/templates/nw0judtfil' "$REQS" || fail "update should PATCH /v2/templates/{id}"
grep -q '^POST ' "$REQS" && fail "update must not POST"
python3 - <<'PY' "$TMP/last_body.json"
import json, sys
body = json.load(open(sys.argv[1]))
assert "serverless" not in body, "PATCH must omit serverless"
assert body["image"].startswith("ghcr.io/goliath-research/")
assert "imageName" not in body
assert body["public"] is False
assert "mounts" not in body
assert "args" not in body
assert body["cmd"][0] == "bash"
assert body["env"]["WORKER_API_BASE"] == "https://gateway.goliathresearch.com/v1"
assert "GOLIATH_GATEWAY_CA_PEM" not in body["env"]
print("patch body ok")
PY

# write path: only a serverless namesake exists, so POST a pod template
PORT="$(start_mock create)"
run_script "$TMP/cr.out" || fail "create run failed: $(cat "$TMP/cr.out.err")"
grep -q 'created newtpl1' "$TMP/cr.out" || fail "create should print new id"
grep -q '^POST /v2/templates' "$REQS" || fail "create should POST /v2/templates"
python3 - <<'PY' "$TMP/last_body.json"
import json, sys
body = json.load(open(sys.argv[1]))
assert body["serverless"] is False
assert body["name"] == "goliath-worker"
print("post body ok")
PY

echo "OK"
