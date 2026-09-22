"""
Queue subcommands for distributed per-comparison enricher tasks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .enricher_queue import (
    execute_enricher_task,
    export_enricher_queue,
    plan_enricher_tasks,
)
from .ensure_complete import verify_project_complete


def _add_project_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--project",
        "-p",
        type=Path,
        required=True,
        help="Path to production project.json",
    )
    p.add_argument(
        "--step-override",
        type=Path,
        default=None,
        help="Optional JSON overrides for enricher step config",
    )


def build_queue_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MethylEnricher queue — distributed per-comparison enrichment"
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_plan = sub.add_parser("plan-tasks", help="Write queue task JSON files")
    _add_project_arg(p_plan)
    p_plan.add_argument("--overwrite", action="store_true", help="Replace existing task JSON")

    p_export = sub.add_parser("export-queue", help="Write queue_manifest.jsonl and commands.sh")
    _add_project_arg(p_export)

    p_run = sub.add_parser("run-task", help="Run one comparison enrichment task")
    p_run.add_argument("--task", type=Path, required=True, help="Path to task JSON")
    p_run.add_argument("--force", action="store_true", help="Re-run even if completed")

    p_verify = sub.add_parser("verify-complete", help="Verify all comparisons have complete enrichment")
    _add_project_arg(p_verify)
    p_verify.add_argument(
        "--comparison",
        type=str,
        default=None,
        help="Verify only this comparison label",
    )

    return parser


QUEUE_SUBCOMMANDS = ("plan-tasks", "export-queue", "run-task", "verify-complete")


def main_queue(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_queue_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "plan-tasks":
        plan = plan_enricher_tasks(
            args.project,
            step_override_path=args.step_override,
            overwrite=bool(args.overwrite),
        )
        print(f"Planned {plan['n_tasks']} enricher task(s) under production/enricher/queue/")
        return 0

    if args.subcommand == "export-queue":
        summary = export_enricher_queue(args.project)
        print(f"Exported queue manifest: {summary['manifest']}")
        print(f"Commands script: {summary['commands_sh']}")
        return 0

    if args.subcommand == "run-task":
        return execute_enricher_task(args.task, force=bool(args.force))

    if args.subcommand == "verify-complete":
        ok = verify_project_complete(args.project, comparison=args.comparison)
        if ok:
            print("[OK] All comparisons have complete enricher outputs.")
            return 0
        print("[ERROR] Enricher outputs incomplete.", file=sys.stderr)
        return 1

    return 1
