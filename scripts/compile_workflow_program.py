#!/usr/bin/env python3
"""Compile a DomainProgram JSON file to WorkflowDefinitionSpec JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "workflow_engine" / "domain"))

from compiler import compile_domain_program_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", type=Path, help="DomainProgram JSON path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write compiled WorkflowDefinitionSpec JSON (default: stdout)",
    )
    args = parser.parse_args()

    if not args.program.is_file():
        print(f"Program not found: {args.program}", file=sys.stderr)
        return 1

    result = compile_domain_program_file(args.program, enrich_context=False)
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
