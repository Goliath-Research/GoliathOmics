#!/usr/bin/env python3
"""Create or update the Runpod template goliath-worker via REST API v2.

Usage:
  RUNPOD_API_KEY=... python3 deploy/runpod/upsert_worker_template.py --dry-run
  RUNPOD_API_KEY=... python3 deploy/runpod/upsert_worker_template.py

The API key is read from the environment and is never printed. --dry-run
prints the planned v2 body and, when a key is set, whether the named
template already exists. It does not POST or PATCH.

Default WORKER_API_BASE is https://gateway.goliathresearch.com/v1.
GOLIATH_GATEWAY_CA_PEM is not set: the live gateway presents a public
certificate. Templates cannot carry a global or network volume, so this
script omits `mounts` (a PATCH of `{}` would also leave mounts unchanged).

RUNPOD_API_BASE defaults to https://api.runpod.io; paths are under /v2/.
RUNPOD_REGISTRY_AUTH_ID overrides the default GHCR credential id already
attached to the live goliath-worker template (an id, not a secret).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

NAME = "goliath-worker"
DEFAULT_API_BASE = "https://api.runpod.io"
DEFAULT_IMAGE = "ghcr.io/goliath-research/goliath-worker:4.7.0-2"
# Credential id already on the live goliath-worker template. Not a secret.
DEFAULT_REGISTRY = "cmvfgr6i1003bl3r5rmi350hr"
DEFAULT_DISK_GB = 30
# Matches the live template. create_worker.sh sends disk=200 on each pod.
ALLOWED_CUDA = ["12.8", "12.9", "13.0", "13.1", "13.2", "13.3", "13.4"]
PORTS = ["8888/http", "22/tcp"]
WORKER_API_BASE = "https://gateway.goliathresearch.com/v1"
USER_AGENT = "goliath-upsert-worker-template/1.0"
LIST_LIMIT = 100
LIST_PAGE_CAP = 50

# Container start command currently on the live goliath-worker template.
# Sent as v2 `cmd` (exec form). `args` is derived by the API; do not send both.
START = "set +x;python3 -c \"import os,json,base64,re,urllib.request as u;e=os.environ.get;t=e(\\\"GH_TOKEN\\\") or e(\\\"GITHUB_TOKEN\\\") or \\\"\\\";r=e(\\\"GOLIATH_GITHUB_REPO\\\") or \\\"Goliath-Research/GoliathOmics\\\";g=e(\\\"GOLIATH_RELEASE_TAG\\\") or \\\"\\\";a=(e(\\\"GOLIATH_GITHUB_API\\\") or \\\"https://api.github.com\\\").rstrip(\\\"/\\\");assert t and chr(10) not in t and chr(13) not in t and re.fullmatch(\\\"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\\\",r);h={\\\"Authorization\\\":\\\"Bearer \\\"+t};q=lambda n: json.load(u.urlopen(u.Request(a+n,headers=h),timeout=60));g=g or next((i[\\\"tag_name\\\"] for i in q(\\\"/repos/\\\"+r+\\\"/releases?per_page=100\\\") if isinstance(i,dict) and not i.get(\\\"draft\\\") and not i.get(\\\"prerelease\\\") and str(i.get(\\\"tag_name\\\") or \\\"\\\").startswith(\\\"goliathomics-\\\")), \\\"\\\");assert re.fullmatch(\\\"goliathomics-[A-Za-z0-9._+-]+\\\",g);b=base64.b64decode(q(\\\"/repos/\\\"+r+\\\"/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref=\\\"+g)[\\\"content\\\"]);assert b\\\"goliath-worker\\\" in b;open(\\\"/tmp/goliath-worker-bootstrap.sh\\\",\\\"wb\\\").write(b)\"&&exec bash /tmp/goliath-worker-bootstrap.sh \"$@\"||{ echo goliath-stub-failed >&2;exec \"${GOLIATH_START_KEEPALIVE:-/start.sh}\";}"

ENV = {
    "WORKER_API_BASE": WORKER_API_BASE,
    "GOLIATH_ENV": "goliath-test",
    "GOLIATH_GITHUB_REPO": "Goliath-Research/GoliathOmics",
    "CLUSTER_KEY": "goliath",
    "B2_BUCKET": "goliath-omics",
    "B2_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
    "LONGTERM_BUCKET": "goliath-omics",
    "LONGTERM_ENDPOINT": "https://s3.us-east-1.myqnapcloud.io",
    "LONGTERM_HOST": "s3.us-east-1.myqnapcloud.io",
    "LONGTERM_SHARE": "goliath-omics",
    "GH_TOKEN": "{{ RUNPOD_SECRET_GH_TOKEN }}",
    "CURSOR_API_KEY": "{{ RUNPOD_SECRET_CURSOR_API_KEY }}",
    "B2_ACCESS_KEY_ID": "{{ RUNPOD_SECRET_B2_ACCESS_KEY_ID }}",
    "B2_SECRET_ACCESS_KEY": "{{ RUNPOD_SECRET_B2_SECRET_ACCESS_KEY }}",
    "LONGTERM_ACCESS_KEY_ID": "{{ RUNPOD_SECRET_LONGTERM_ACCESS_KEY_ID }}",
    "LONGTERM_SECRET_ACCESS_KEY": "{{ RUNPOD_SECRET_LONGTERM_SECRET_ACCESS_KEY }}",
    "NGC_API_KEY": "{{ RUNPOD_SECRET_NGC_API_KEY }}",
}


def planned_body() -> dict[str, Any]:
    registry = os.environ.get("RUNPOD_REGISTRY_AUTH_ID") or DEFAULT_REGISTRY
    image = os.environ.get("GOLIATH_WORKER_IMAGE") or DEFAULT_IMAGE
    body: dict[str, Any] = {
        "name": NAME,
        "image": image,
        "disk": DEFAULT_DISK_GB,
        "ports": list(PORTS),
        "cmd": ["bash", "-lc", START],
        "env": dict(ENV),
        "public": False,
        "serverless": False,
        "category": "NVIDIA",
        "startSsh": False,
        "startJupyter": False,
        "allowedCudaVersions": list(ALLOWED_CUDA),
    }
    if registry:
        body["registry"] = registry
    return body


def api_base() -> str:
    raw = (os.environ.get("RUNPOD_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/")
    if not raw:
        sys.exit("RUNPOD_API_BASE is empty")
    return raw


def api_path(path: str) -> str:
    """Build a /v2/... path against RUNPOD_API_BASE, which may already end in /v2."""
    base = api_base()
    if not path.startswith("/"):
        path = "/" + path
    if base.endswith("/v2"):
        if path.startswith("/v2/"):
            return base + path[3:]
        return base + path
    if not path.startswith("/v2/"):
        path = "/v2" + path
    return base + path


def _redact(text: str) -> str:
    key = os.environ.get("RUNPOD_API_KEY") or ""
    if key and key in text:
        return text.replace(key, "<redacted>")
    return text


def _error_detail(payload: str) -> str:
    payload = _redact(payload)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:500]
    if not isinstance(data, dict):
        return payload[:500]
    parts = [str(data[k]) for k in ("title", "status", "detail") if data.get(k) not in (None, "")]
    errors = data.get("errors")
    if errors:
        parts.append(str(errors))
    if not parts and data.get("message"):
        parts.append(str(data["message"]))
    return ": ".join(parts) if parts else payload[:500]


def request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    key = os.environ.get("RUNPOD_API_KEY") or ""
    if not key:
        sys.exit("set RUNPOD_API_KEY")
    if any(ch in key for ch in " \t\n\r\"\\"):
        sys.exit("RUNPOD_API_KEY contains whitespace, quotes or backslashes; refusing to use it")
    url = api_path(path)
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc.read().decode("utf-8", "replace"))
        sys.exit("%s %s failed: %s %s" % (method, path, exc.code, detail))
    except urllib.error.URLError as exc:
        sys.exit("%s %s failed: %s" % (method, path, _redact(str(exc.reason))))


def list_templates() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(LIST_PAGE_CAP):
        q = {"limit": str(LIST_LIMIT)}
        if cursor:
            q["cursor"] = cursor
        path = "/v2/templates?" + urllib.parse.urlencode(q)
        payload = request("GET", path)
        if not isinstance(payload, dict) or not isinstance(payload.get("templates"), list):
            sys.exit("GET /v2/templates: response has no 'templates' list")
        items.extend(payload["templates"])
        pag = payload.get("pagination") or {}
        if not pag.get("hasNextPage"):
            return items
        cursor = pag.get("nextCursor") or ""
        if not cursor:
            sys.exit("GET /v2/templates: hasNextPage is true but nextCursor is empty")
    sys.exit("GET /v2/templates: too many pages")


def find_pod_template(templates: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [
        t for t in templates
        if isinstance(t, dict) and t.get("name") == NAME and t.get("serverless") is not True
    ]
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(str(t.get("id") or "?") for t in matches)
        sys.exit("more than one non-serverless template named %s: %s" % (NAME, ids))
    return matches[0]


COMPARE_FIELDS = (
    "name",
    "image",
    "disk",
    "ports",
    "cmd",
    "env",
    "registry",
    "public",
    "serverless",
    "category",
    "startSsh",
    "startJupyter",
    "allowedCudaVersions",
)


def describe_diff(planned: dict[str, Any], live: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in COMPARE_FIELDS:
        wanted, got = planned.get(key), live.get(key)
        if wanted != got:
            lines.append("%s: planned %s, live %s" % (key, json.dumps(wanted, sort_keys=True), json.dumps(got, sort_keys=True)))
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update the goliath-worker Runpod template via REST v2.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned body and do not POST or PATCH.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    body = planned_body()
    if "GOLIATH_GATEWAY_CA_PEM" in body["env"]:
        sys.exit("refusing to set GOLIATH_GATEWAY_CA_PEM")
    if args.dry_run:
        print(json.dumps(body, indent=2, sort_keys=True))
        if not os.environ.get("RUNPOD_API_KEY"):
            print("dry-run: not looking up the live template (RUNPOD_API_KEY unset)")
            return 0
        existing = find_pod_template(list_templates())
        if existing is None:
            print("dry-run: would POST /v2/templates (no non-serverless template named %s)" % NAME)
            return 0
        tid = existing.get("id")
        live = request("GET", "/v2/templates/%s" % tid) if tid else existing
        if not isinstance(live, dict):
            live = existing
        print("dry-run: would PATCH /v2/templates/%s" % tid)
        diffs = describe_diff(body, live)
        if diffs:
            print("dry-run: planned body differs from the live template:")
            for line in diffs:
                print("  " + line)
        else:
            print("dry-run: planned body matches the live template on compared fields")
            print("dry-run: omitted mounts (templates cannot hold a global volume; live mounts stay unchanged)")
            print("dry-run: omitted args (derived from cmd; live args is a stored encoding)")
        return 0

    if not os.environ.get("RUNPOD_API_KEY"):
        sys.exit("set RUNPOD_API_KEY")
    existing = find_pod_template(list_templates())
    if existing:
        tid = existing.get("id")
        if not tid:
            sys.exit("existing template %s has no id" % NAME)
        patch = {k: v for k, v in body.items() if k != "serverless"}
        request("PATCH", "/v2/templates/%s" % tid, patch)
        print("updated", tid)
    else:
        created = request("POST", "/v2/templates", body)
        if not isinstance(created, dict) or not created.get("id"):
            sys.exit("POST /v2/templates returned no id")
        tid = created["id"]
        print("created", tid)
    got = request("GET", "/v2/templates/%s" % tid) or {}
    env_keys = sorted((got.get("env") or {}).keys())
    print("env keys saved:", env_keys)
    print("cmd saved:", bool(got.get("cmd")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
