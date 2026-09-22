"""CLI entry point: python -m methyl_worker or methyl-worker."""

from __future__ import annotations

import argparse
import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Optional

from .capabilities import (
    OMNIBUS_WILDCARD,
    assert_node_can_serve_capability,
    resolve_worker_capabilities,
)
from .client import WorkflowRestClient
from .runner import WorkerRunner

DEFAULT_TOKEN_FILE = Path("/etc/methyl/worker-token")
_SUBCOMMANDS = frozenset({"poll", "enroll", "plan-iterations"})


def _default_api_base() -> str:
    """Match register_worker / provision: WORKER_API_BASE then METHYL_API_BASE."""
    return (
        os.environ.get("WORKER_API_BASE")
        or os.environ.get("METHYL_API_BASE")
        or "http://localhost:8080/v1"
    )


def main(argv: list[str] | None = None) -> int:
    # Fleet hosts share /work over NFS as different numeric uids (all named
    # ``ubuntu``). Default umask 022 + docker --user leaves mode-0600 GAFs that
    # other sisters cannot read. Soften so new files are group/other-readable.
    try:
        os.umask(0o002)
    except OSError:
        pass

    argv_list = list(sys.argv[1:] if argv is None else argv)
    # Legacy: flags without a subcommand mean poll (e.g. `methyl-worker --once`).
    # Detect before parse_args so poll-only flags are not rejected as unknown.
    if not argv_list or (
        argv_list[0] not in _SUBCOMMANDS and argv_list[0] not in ("-h", "--help")
    ):
        argv_list = ["poll", *argv_list]

    parser = argparse.ArgumentParser(
        description="MethylPipeline REST workflow worker (poll middle-tier API)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    poll = sub.add_parser("poll", help="Poll gateway for READY tasks (default)")
    _add_poll_args(poll)

    enroll = sub.add_parser(
        "enroll",
        help="Enroll via POST /workers/enroll (portal IP allowlist; no SQL)",
    )
    enroll.add_argument(
        "--api-base",
        default=_default_api_base(),
        help="Gateway base URL (WORKER_API_BASE, then METHYL_API_BASE)",
    )
    enroll.add_argument(
        "--cluster",
        "--cluster-key",
        dest="cluster_key",
        default=os.environ.get("CLUSTER_KEY", ""),
        help="Cluster key (or CLUSTER_KEY)",
    )
    enroll.add_argument(
        "--key",
        "--external-worker-key",
        dest="external_worker_key",
        default=os.environ.get("WORKER_KEY", ""),
        help="External worker key matching portal enrollment (or WORKER_KEY)",
    )
    enroll.add_argument(
        "--capabilities-json",
        default="",
        help=(
            "JSON array of capabilities. Default: probe this NVIDIA GPU host. "
            "Pass '[\"*\"]' only for explicit omnibus. Never enroll with []."
        ),
    )
    enroll.add_argument(
        "--token-file",
        default=os.environ.get("METHYL_WORKER_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)),
        help=f"Write credentials here (default: {DEFAULT_TOKEN_FILE})",
    )
    enroll.add_argument(
        "--env-file",
        default="",
        help=(
            "Optional *per-VM* env file to append WORKER_ID=/WORKER_TOKEN=. "
            "Do not use shared /work/goliath/env/worker.env — that overrides "
            "other nodes' credentials when systemd loads it after the token file."
        ),
    )

    plan = sub.add_parser("plan-iterations", help="Monte Carlo planner CLI")
    plan.add_argument(
        "--plan-input",
        help="JSON file for plan-iterations (default: stdin or PROJECT_PATH)",
    )
    plan.add_argument(
        "--api-base",
        default=_default_api_base(),
    )

    args = parser.parse_args(argv_list)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "plan-iterations":
        return _run_plan_iterations_cli(args)
    if args.command == "enroll":
        return _run_enroll_cli(args)
    return _run_poll_cli(args)


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _add_poll_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--worker-id", type=int, default=_env_int("WORKER_ID", 0))
    parser.add_argument("--worker-token", default=os.environ.get("WORKER_TOKEN", ""))
    parser.add_argument("--capability", default=os.environ.get("WORKER_CAPABILITY"))
    parser.add_argument(
        "--api-base",
        default=_default_api_base(),
        help="Middle-tier base URL (WORKER_API_BASE, then METHYL_API_BASE)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("WORKER_POLL_SECONDS", "5")),
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "60")),
    )
    parser.add_argument("--once", action="store_true", help="Poll once then exit")
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help="Call POST /workers/authenticate before polling",
    )


def _run_poll_cli(args: argparse.Namespace) -> int:
    if args.worker_id <= 0 or not args.worker_token:
        print(
            "Set --worker-id and --worker-token (or WORKER_ID / WORKER_TOKEN env vars)",
            file=sys.stderr,
        )
        return 2

    if args.capability:
        assert_node_can_serve_capability(args.capability)

    client = WorkflowRestClient(args.api_base)
    if args.authenticate:
        client.authenticate(args.worker_id, args.worker_token)

    if args.once:
        poll = client.request_task(args.worker_id, args.worker_token, args.capability)
        print(
            json.dumps(
                {
                    "has_task": poll.claim is not None,
                    "claim": _claim_dict(poll.claim),
                    "desired_state": poll.control.desired_state,
                    "command": poll.control.command,
                },
                indent=2,
            )
        )
        return 0

    runner = WorkerRunner(
        client,
        args.worker_id,
        args.worker_token,
        args.capability,
        poll_seconds=args.poll_seconds,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    runner.run_forever()
    return 0


def _run_enroll_cli(args: argparse.Namespace) -> int:
    if not args.cluster_key or not args.external_worker_key:
        print("Set --cluster and --key (or CLUSTER_KEY / WORKER_KEY)", file=sys.stderr)
        return 2

    if args.capabilities_json.strip():
        capabilities: list[Any] = json.loads(args.capabilities_json)
        if not isinstance(capabilities, list) or not capabilities:
            print(
                "--capabilities-json must be a non-empty JSON array "
                "(or omit it to probe NVIDIA capabilities on this host)",
                file=sys.stderr,
            )
            return 2
    else:
        # GH200 / NVIDIA cluster: probe installed CLIs + GPU; never enroll with []
        # or silent ["*"] (resolve no longer falls back to omnibus).
        capabilities = resolve_worker_capabilities()
        if not capabilities:
            print(
                "resolve_worker_capabilities() returned empty; "
                "refusing enroll (no AMD/ROCm fallback on this fleet)",
                file=sys.stderr,
            )
            return 2
        if capabilities == [OMNIBUS_WILDCARD]:
            print(
                "refusing enroll with probed omnibus ['*']; "
                "pass --capabilities-json '[\"*\"]' only when intentional",
                file=sys.stderr,
            )
            return 2

    client = WorkflowRestClient(args.api_base)
    try:
        result = client.enroll(
            args.cluster_key,
            args.external_worker_key,
            capabilities=capabilities,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    worker_id = int(result["worker_id"])
    token = str(result["worker_token"])
    token_path = Path(args.token_file)
    _write_token_file(token_path, worker_id, token)
    if args.env_file:
        _append_worker_env(Path(args.env_file), worker_id, token)
    print(f"Enrolled worker_id={worker_id}")
    print(f"  Wrote credentials to {token_path}")
    print(f"  WORKER_ID={worker_id}")
    return 0


def _write_token_file(path: Path, worker_id: int, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"WORKER_ID={worker_id}\nWORKER_TOKEN={token}\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def _append_worker_env(path: Path, worker_id: int, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [
        line
        for line in existing.splitlines()
        if not line.startswith("WORKER_ID=") and not line.startswith("WORKER_TOKEN=")
    ]
    lines.append(f"WORKER_ID={worker_id}")
    lines.append(f"WORKER_TOKEN={token}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _claim_dict(claim) -> dict | None:
    if claim is None:
        return None
    return {
        "node_execution_id": claim.node_execution_id,
        "workflow_instance_id": claim.workflow_instance_id,
        "action_name": claim.action_name,
        "capability": claim.capability,
        "node_key": claim.node_key,
        "input_json": claim.input_json,
    }


def _run_plan_iterations_cli(args: argparse.Namespace) -> int:
    from methyl_validation.workflow_planner import plan_validation_context

    if args.plan_input:
        payload = json.loads(Path(args.plan_input).read_text(encoding="utf-8"))
    else:
        payload = {"projectPath": os.environ.get("PROJECT_PATH", "")}
        if not payload["projectPath"]:
            print("Set PROJECT_PATH or pass --plan-input", file=sys.stderr)
            return 2
    context = plan_validation_context(payload)
    print(json.dumps(context.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
