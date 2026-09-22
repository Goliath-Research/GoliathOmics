#!/usr/bin/env python3
"""
Admin-tier study lifecycle CLI (domain-aware).

Compiles DomainPrograms, plans instance context, bakes resolvedConfig scope vars,
and creates/starts workflow instances via the backend-agnostic DB layer
(``rest.db_client`` → MSSQL or PostgreSQL).

Not part of the worker-only REST gateway. Production portal queues
``cfg.study_start_request``; this CLI's ``drain-requests`` command bakes
``resolvedConfig`` from materialized ``/work`` and calls
``portal.sp_create_and_start_instance``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from admin._paths import ensure_import_paths


def _load_body(path: Optional[str]) -> Dict[str, Any]:
    if path is None or path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    return json.loads(raw) if raw.strip() else {}


def _open_db():
    from rest.connection import resolve_connection_config
    from rest.db import open_gateway_db

    return open_gateway_db(resolve_connection_config())


def cmd_compile(args: argparse.Namespace) -> int:
    from ops.study_lifecycle import compile_program_spec

    program = Path(args.program).expanduser().resolve()
    spec = compile_program_spec(program, project_path=args.project_path)
    text = json.dumps(spec, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def cmd_validation_start(args: argparse.Namespace) -> int:
    from rest.db_client import (
        create_workflow_definition,
        create_workflow_instance,
        start_workflow_instance,
    )
    from ops.study_lifecycle import start_study_validation

    body = _load_body(args.body_file)
    if args.project_path:
        body["projectPath"] = args.project_path
    if args.program_path:
        body["program_path"] = args.program_path
    if args.workflow_version_id is not None:
        body["workflow_version_id"] = args.workflow_version_id

    db = _open_db()
    try:
        payload = start_study_validation(
            db,
            body,
            create_workflow_definition=create_workflow_definition,
            create_workflow_instance=create_workflow_instance,
            start_workflow_instance=start_workflow_instance,
        )
    finally:
        db.close()

    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_sample_prep_start(args: argparse.Namespace) -> int:
    from rest.db_client import (
        create_workflow_definition,
        create_workflow_instance,
        start_workflow_instance,
    )
    from ops.sample_lifecycle import start_sample_prep

    body = _load_body(args.body_file)
    if args.project_path:
        body["projectPath"] = args.project_path
    if args.workflow_version_id is not None:
        body["workflow_version_id"] = args.workflow_version_id

    db = _open_db()
    try:
        payload = start_sample_prep(
            db,
            body,
            create_workflow_definition=create_workflow_definition,
            create_workflow_instance=create_workflow_instance,
            start_workflow_instance=start_workflow_instance,
        )
    finally:
        db.close()

    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_hyperparam_grid_start(args: argparse.Namespace) -> int:
    from rest.db_client import (
        create_workflow_definition,
        create_workflow_instance,
        start_workflow_instance,
    )
    from ops.hyperparam_grid import DbTrialLedger, expand_and_start_grid

    body = _load_body(args.body_file)
    if args.project_path:
        body["project_path"] = args.project_path

    db = _open_db()
    try:
        ledger = None if args.no_ledger else DbTrialLedger(
            db, study_row_id=args.study_row_id, created_by=args.created_by
        )
        result = expand_and_start_grid(
            db,
            body,
            create_workflow_definition=create_workflow_definition,
            create_workflow_instance=create_workflow_instance,
            start_workflow_instance=start_workflow_instance,
            ledger=ledger,
        )
    finally:
        db.close()

    json.dump(result.to_json(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_scenario_start(args: argparse.Namespace) -> int:
    from rest.db_client import (
        create_workflow_definition,
        create_workflow_instance,
        start_workflow_instance,
    )
    from ops.hyperparam_grid import DbTrialLedger, start_scenario_trial

    body = _load_body(args.body_file)
    if args.project_path:
        body["project_path"] = args.project_path

    db = _open_db()
    try:
        ledger = None if args.no_ledger else DbTrialLedger(
            db, study_row_id=args.study_row_id, created_by=args.created_by
        )
        result = start_scenario_trial(
            db,
            body,
            create_workflow_definition=create_workflow_definition,
            create_workflow_instance=create_workflow_instance,
            start_workflow_instance=start_workflow_instance,
            ledger=ledger,
        )
    finally:
        db.close()

    json.dump(result.to_json(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_hyperparam_grid_score(args: argparse.Namespace) -> int:
    from ops.hyperparam_grid import score_grid, winner_overlay

    trial_mc_runs: Dict[str, str] = {}
    if args.trial_mc_json:
        trial_mc_runs = json.loads(Path(args.trial_mc_json).read_text(encoding="utf-8"))
    weights: Dict[str, Any] = {}
    if args.weights_json:
        weights = json.loads(Path(args.weights_json).read_text(encoding="utf-8"))

    db = _open_db()
    try:
        summary = score_grid(db, int(args.search_id), weights, trial_mc_runs)
        summary["winner"] = winner_overlay(db, int(args.search_id))
    finally:
        db.close()

    if args.winner_out and summary.get("winner"):
        Path(args.winner_out).write_text(
            json.dumps(summary["winner"], indent=2) + "\n", encoding="utf-8"
        )

    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_plan_iterations(args: argparse.Namespace) -> int:
    ensure_import_paths()
    from methyl_validation.workflow_planner import plan_validation_context
    from rest.db_client import apply_validation_plan
    from workflow_context import finalize_instance_context

    body = _load_body(args.body_file)
    planned = plan_validation_context(body)
    context = finalize_instance_context(planned.model_dump(mode="json"))

    if args.workflow_instance_id is not None:
        db = _open_db()
        try:
            apply_validation_plan(
                db,
                int(args.workflow_instance_id),
                context,
                persist_extension=not args.no_persist_extension,
            )
        finally:
            db.close()

    json.dump(
        {
            "context_json": context,
            "n_iterations": len(context.get("iterations", [])),
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


def cmd_drain_requests(args: argparse.Namespace) -> int:
    from ops.study_start_queue import drain_requests

    db = _open_db()
    try:
        results = drain_requests(
            db,
            claimed_by=args.claimed_by,
            lease_seconds=args.lease_seconds,
            limit=args.limit,
        )
    finally:
        db.close()
    json.dump(results, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if all("error" not in r for r in results) else 1


def main(argv: Optional[list[str]] = None) -> int:
    ensure_import_paths()

    parser = argparse.ArgumentParser(
        description="MethylPipeline admin study lifecycle (compile, plan, start instances)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_compile = sub.add_parser("compile", help="Compile a DomainProgram to WorkflowDefinitionSpec JSON")
    p_compile.add_argument("program", help="Path to *.program.json")
    p_compile.add_argument("--project-path", dest="project_path", default=None)
    p_compile.add_argument("-o", "--output", default=None, help="Write spec JSON to file")
    p_compile.set_defaults(func=cmd_compile)

    p_val = sub.add_parser("validation-start", help="Plan and start a study validation workflow")
    p_val.add_argument(
        "body_file",
        nargs="?",
        default="-",
        help="JSON request body (default: stdin)",
    )
    p_val.add_argument("--project-path", dest="project_path", default=None)
    p_val.add_argument("--program-path", dest="program_path", default=None)
    p_val.add_argument("--workflow-version-id", type=int, default=None)
    p_val.set_defaults(func=cmd_validation_start)

    p_prep = sub.add_parser("sample-prep-start", help="Plan and start SamplePrepPipeline")
    p_prep.add_argument(
        "body_file",
        nargs="?",
        default="-",
        help="JSON request body (default: stdin)",
    )
    p_prep.add_argument("--project-path", dest="project_path", default=None)
    p_prep.add_argument("--workflow-version-id", type=int, default=None)
    p_prep.set_defaults(func=cmd_sample_prep_start)

    p_grid = sub.add_parser(
        "hyperparam-grid-start",
        help="Expand a hyperparameter grid into N validation instances (multi-instance HPO)",
    )
    p_grid.add_argument(
        "body_file",
        nargs="?",
        default="-",
        help="HyperparamSearchRequest JSON (default: stdin)",
    )
    p_grid.add_argument("--project-path", dest="project_path", default=None)
    p_grid.add_argument("--study-row-id", type=int, default=None, help="cfg.study id for the ledger")
    p_grid.add_argument("--created-by", default=None)
    p_grid.add_argument(
        "--no-ledger",
        action="store_true",
        help="Start instances without writing the cfg.hyperparameter_search_* ledger",
    )
    p_grid.set_defaults(func=cmd_hyperparam_grid_start)

    p_scenario = sub.add_parser(
        "scenario-start",
        help=(
            "Start one validation instance from an actionConfig overlay "
            "(assumption check / stability scenario; no Cartesian grid)"
        ),
    )
    p_scenario.add_argument(
        "body_file",
        nargs="?",
        default="-",
        help="HyperparamScenarioRequest JSON (default: stdin)",
    )
    p_scenario.add_argument("--project-path", dest="project_path", default=None)
    p_scenario.add_argument("--study-row-id", type=int, default=None)
    p_scenario.add_argument("--created-by", default=None)
    p_scenario.add_argument(
        "--no-ledger",
        action="store_true",
        help="Start the instance without writing a cfg single-trial search row",
    )
    p_scenario.set_defaults(func=cmd_scenario_start)

    p_score = sub.add_parser(
        "hyperparam-grid-score",
        help="Score completed grid trials via objective J and persist to the cfg ledger",
    )
    p_score.add_argument("--search-id", required=True, help="cfg.hyperparameter_search_run id")
    p_score.add_argument(
        "--trial-mc-json",
        default=None,
        help="JSON map of trial index -> monte_carlo_runs directory",
    )
    p_score.add_argument("--weights-json", default=None, help="ObjectiveWeights JSON")
    p_score.add_argument(
        "--winner-out",
        default=None,
        help="Write the winning trial's actionConfig overlay to this path (operator-gated).",
    )
    p_score.set_defaults(func=cmd_hyperparam_grid_score)

    p_plan = sub.add_parser("plan-iterations", help="Plan validation iterations context JSON")
    p_plan.add_argument(
        "body_file",
        nargs="?",
        default="-",
        help="JSON planner payload (default: stdin)",
    )
    p_plan.add_argument("--workflow-instance-id", type=int, default=None)
    p_plan.add_argument("--no-persist-extension", action="store_true")
    p_plan.set_defaults(func=cmd_plan_iterations)

    p_drain = sub.add_parser(
        "drain-requests",
        help=(
            "Claim queued cfg.study_start_request rows, bake resolvedConfig "
            "from materialized /work, then create/start/link via SQL"
        ),
    )
    p_drain.add_argument("--claimed-by", default=None)
    p_drain.add_argument("--lease-seconds", type=int, default=600)
    p_drain.add_argument("--limit", type=int, default=1, help="Max requests this pass")
    p_drain.set_defaults(func=cmd_drain_requests)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
