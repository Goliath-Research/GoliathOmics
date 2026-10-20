#!/usr/bin/env python3
"""Compile a DomainProgram JSON file to WorkflowDefinitionSpec JSON.

Uses ``domain.compiler`` from the methyl-gateway wheel (GoliathApp PR #7).
When that import is not available, falls back to
``methyl-workflow-run --compile-only``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKERS = REPO_ROOT / "workers"
if _WORKERS.is_dir() and str(_WORKERS) not in sys.path:
    sys.path.insert(0, str(_WORKERS))
sys.path.insert(0, str(REPO_ROOT / "workflow_engine"))

from ops.platform_compiler import compile_domain_program_file  # noqa: E402


def _compile_with_cli(program: Path, output: Path | None) -> int:
    cli = shutil.which("methyl-workflow-run")
    if cli is None:
        print(
            "Compile requires domain.compiler from the methyl-gateway wheel "
            "(GoliathApp PR #7) or methyl-workflow-run --compile-only on PATH.",
            file=sys.stderr,
        )
        return 1
    repo_engine = str((REPO_ROOT / "workflow_engine").resolve())
    repo_domain = str((REPO_ROOT / "workflow_engine" / "domain").resolve())
    cleaned: list[str] = []
    for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            resolved = str(Path(entry).resolve())
        except OSError:
            continue
        if resolved in {repo_engine, repo_domain}:
            continue
        cleaned.append(entry)
    if _WORKERS.is_dir():
        cleaned.insert(0, str(_WORKERS))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(cleaned)
    cmd = [cli, "--compile-only", "--program", str(program)]
    if output is not None:
        cmd.extend(["--output", str(output)])
    completed = subprocess.run(cmd, env=env, check=False)
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", nargs="?", type=Path, help="DomainProgram JSON path")
    parser.add_argument("--program", dest="program_flag", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write compiled WorkflowDefinitionSpec JSON (default: stdout)",
    )
    args = parser.parse_args()
    program = args.program or args.program_flag
    if program is None:
        parser.error("a DomainProgram path is required")

    if not program.is_file():
        print(f"Program not found: {program}", file=sys.stderr)
        return 1

    try:
        result = compile_domain_program_file(program, enrich_context=False)
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return _compile_with_cli(program, args.output)

    spec = result.workflow.model_dump(mode="json")
    text = json.dumps(spec, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
