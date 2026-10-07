#!/usr/bin/env python3
"""Report a deployment to Cursor Rollouts.

Entry points: bootstrap, start, finish.

The environment and service are never hard-coded. Each caller sets
CHANGE_MONITOR_ENV and CHANGE_MONITOR_SERVICE. CURSOR_API_KEY is read from
the environment. DEPLOY_SOURCE_URI, DEPLOY_VERSION, and DEPLOY_ACTOR identify
the ship. finish also reads DEPLOY_OUTCOME (success, failure, or cancelled).

A re-run looks up an existing deployment for this actor and will not append a
second completed event. Requests time out so a report cannot stall the ship.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

TOKEN_URL = "https://api2.cursor.sh/auth/exchange_user_api_key"
FACTORY_URL = "https://api.cursor.com/factory.v1.DeploymentsService"
REQUEST_TIMEOUT_SECONDS = 20


def state_path() -> str:
    override = os.environ.get("ROLLOUTS_STATE_FILE", "").strip()
    if override:
        return override
    root = os.environ.get("RUNNER_TEMP", "").strip() or "/tmp"
    return os.path.join(root, "rollouts-deployment.json")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set")
    if any(ch in value for ch in '"\n\r'):
        raise SystemExit(f"{name} contains a character the reporter cannot send")
    return value


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        status = error.code
    if not raw.strip():
        return status, {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {"_raw": raw[:500]}
    if not isinstance(body, dict):
        return status, {"_raw": raw[:500]}
    return status, body


def exchange_token(api_key: str) -> str:
    status, body = post_json(
        TOKEN_URL,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        {},
    )
    token = body.get("accessToken")
    if status < 200 or status >= 300 or not isinstance(token, str) or not token:
        raise SystemExit(f"token exchange failed (HTTP {status})")
    return token


def factory_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }


def factory_post(token: str, method: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return post_json(f"{FACTORY_URL}/{method}", factory_headers(token), payload)


def is_already_exists(status: int, body: dict[str, Any]) -> bool:
    if status == 409:
        return True
    code = str(body.get("code", "")).replace("-", "_").lower()
    return code in {"already_exists", "alreadyexists"}


def ensure_ok(status: int, body: dict[str, Any], what: str) -> None:
    if 200 <= status < 300 or is_already_exists(status, body):
        return
    raise SystemExit(f"{what} failed (HTTP {status})")


def deployment_name(body: dict[str, Any]) -> str:
    deployment = body.get("deployment")
    if isinstance(deployment, dict) and isinstance(deployment.get("name"), str):
        return deployment["name"]
    name = body.get("name")
    if isinstance(name, str) and name:
        return name
    raise SystemExit("CreateDeployment response did not include deployment.name")


def event_list(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    events = deployment.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def event_is_terminal(event: dict[str, Any]) -> bool:
    if event.get("completed") is not None and "completed" in event:
        return True
    if event.get("aborted") is not None and "aborted" in event:
        return True
    return False


def select_reusable(
    deployments: list[dict[str, Any]], actor: str
) -> tuple[str | None, bool]:
    """Return (name, already_terminal) for the deployment this actor opened."""
    for deployment in deployments:
        name = deployment.get("name")
        if not isinstance(name, str) or not name:
            continue
        ours = [event for event in event_list(deployment) if event.get("actor") == actor]
        if not ours:
            continue
        terminal = any(event_is_terminal(event) for event in ours)
        return name, terminal
    return None, False


def deployments_from_list(body: dict[str, Any]) -> list[dict[str, Any]]:
    deployments = body.get("deployments")
    if not isinstance(deployments, list):
        return []
    return [item for item in deployments if isinstance(item, dict)]


def list_filter(env: str, service: str, version: str) -> str:
    return (
        f'environment = "environments/{env}" AND '
        f'deploy_version = "{version}" AND '
        f'service = "services/{service}"'
    )


def list_deployments(
    token: str, source_uri: str, env: str, service: str, version: str
) -> list[dict[str, Any]]:
    status, body = factory_post(
        token,
        "ListDeployments",
        {
            "deploySourceUri": source_uri,
            "filter": list_filter(env, service, version),
            "pageSize": 100,
            "readMask": "events",
        },
    )
    ensure_ok(status, body, "ListDeployments")
    return deployments_from_list(body)


def completion_body(outcome: str, message: str) -> dict[str, Any]:
    if outcome in {"success", "succeeded"}:
        return {"completed": {"succeeded": {}}}
    if outcome in {"failure", "failed"}:
        detail = message.strip() or "deploy failed"
        return {"completed": {"failed": {"message": detail}}}
    if outcome in {"cancelled", "canceled", "aborted"}:
        return {"aborted": {}}
    raise SystemExit(f"unknown deploy outcome: {outcome}")


def load_state() -> dict[str, Any]:
    path = state_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        return {}
    return loaded


def write_state(name: str, terminal: bool, version: str, actor: str) -> None:
    path = state_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "name": name,
        "terminal": terminal,
        "version": version,
        "actor": actor,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def api_key() -> str:
    return require_env("CURSOR_API_KEY")


def monitor_pair() -> tuple[str, str]:
    return require_env("CHANGE_MONITOR_ENV"), require_env("CHANGE_MONITOR_SERVICE")


def cmd_bootstrap() -> None:
    env, service = monitor_pair()
    token = exchange_token(api_key())
    status, body = factory_post(
        token,
        "CreateEnvironment",
        {"environmentId": env, "environment": {"displayName": env}},
    )
    ensure_ok(status, body, "CreateEnvironment")
    status, body = factory_post(
        token,
        "CreateService",
        {"serviceId": service, "service": {"displayName": service}},
    )
    ensure_ok(status, body, "CreateService")
    print(f"catalog ready for environment={env} service={service}")


def cmd_start() -> None:
    env, service = monitor_pair()
    source_uri = require_env("DEPLOY_SOURCE_URI")
    version = require_env("DEPLOY_VERSION")
    actor = require_env("DEPLOY_ACTOR")
    token = exchange_token(api_key())
    existing, terminal = select_reusable(
        list_deployments(token, source_uri, env, service, version), actor
    )
    if existing:
        write_state(existing, terminal, version, actor)
        print(f"reusing deployment {existing} terminal={str(terminal).lower()}")
        return
    status, body = factory_post(
        token,
        "CreateDeployment",
        {
            "deployment": {
                "deploySourceUri": source_uri,
                "environment": f"environments/{env}",
                "deployVersion": version,
                "service": f"services/{service}",
            },
            "event": {"started": {}, "actor": actor},
        },
    )
    ensure_ok(status, body, "CreateDeployment")
    name = deployment_name(body)
    write_state(name, False, version, actor)
    print(f"opened deployment {name}")


def cmd_finish() -> None:
    state = load_state()
    name = state.get("name")
    if not isinstance(name, str) or not name:
        print("no deployment was opened; skipping finish")
        return
    actor = require_env("DEPLOY_ACTOR")
    if state.get("terminal") and state.get("actor") == actor:
        print(f"deployment {name} already has a terminal event; skipping finish")
        return
    version = state.get("version")
    env, service = monitor_pair()
    source_uri = require_env("DEPLOY_SOURCE_URI")
    token = exchange_token(api_key())
    if isinstance(version, str) and version:
        _existing, terminal = select_reusable(
            list_deployments(token, source_uri, env, service, version), actor
        )
        if terminal:
            write_state(name, True, version, actor)
            print(f"deployment {name} already completed; skipping finish")
            return
    outcome = require_env("DEPLOY_OUTCOME").lower()
    message = os.environ.get("DEPLOY_FAILURE_MESSAGE", "").strip()
    event = completion_body(outcome, message)
    event["actor"] = actor
    status, body = factory_post(
        token,
        "AppendDeploymentEvent",
        {"name": name, "event": event},
    )
    ensure_ok(status, body, "AppendDeploymentEvent")
    if isinstance(version, str):
        write_state(name, True, version, actor)
    print(f"closed deployment {name} outcome={outcome}")


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[1] not in {"bootstrap", "start", "finish"}:
        raise SystemExit("usage: report_rollouts_deploy.py bootstrap|start|finish")
    command = argv[1]
    if command == "bootstrap":
        cmd_bootstrap()
        return
    if command == "start":
        cmd_start()
        return
    if command == "finish":
        cmd_finish()
        return
    raise SystemExit(f"unhandled command: {command}")


if __name__ == "__main__":
    main(sys.argv)
