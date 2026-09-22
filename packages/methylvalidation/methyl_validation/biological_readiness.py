"""
Chain enricher completeness, disease progression, and stability/freeze readiness.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _run(cmd: List[str]) -> int:
    print(f"[biological-readiness] {' '.join(cmd)}", file=sys.stderr, flush=True)
    return subprocess.run(cmd, check=False).returncode


def run_biological_readiness_chain(
    project_root: Path,
    *,
    production_project_json: Optional[Path] = None,
    skip_grok: bool = True,
) -> Tuple[int, List[str]]:
    """
    Run verify-complete -> progression (strict) -> readiness audit.

    Returns (exit_code, error_messages). exit_code 0 means all steps passed.
    """
    project_root = Path(project_root).resolve()
    errors: List[str] = []

    if production_project_json is None:
        prod = project_root / "monte_carlo_runs" / "production" / "project.json"
    else:
        prod = Path(production_project_json).resolve()

    if not prod.is_file():
        return 2, [f"Production project not found: {prod}"]

    rc = _run(["methyl-enricher", "verify-complete", "--project", str(prod)])
    if rc != 0:
        errors.append(f"methyl-enricher verify-complete failed (exit {rc})")
        return 1, errors

    prog_cmd = [
        "methyl-disease-progression",
        "--project",
        str(prod),
        "--strict-missing",
        "--report-md",
    ]
    rc = _run(prog_cmd)
    if rc != 0:
        errors.append(f"methyl-disease-progression failed (exit {rc})")
        return 1, errors

    ready_cmd = ["methyl-stability-freeze-readiness", str(project_root)]
    if skip_grok:
        ready_cmd.append("--no-grok-review")
    rc = _run(ready_cmd)
    if rc != 0:
        errors.append(f"methyl-stability-freeze-readiness failed (exit {rc})")
        return rc, errors

    return 0, []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Biological confirmation gate: verify enricher completeness, "
            "run progression (strict), then stability/freeze readiness audit."
        )
    )
    parser.add_argument(
        "project_root",
        type=Path,
        help="Project directory containing monte_carlo_runs/ (not a JSON file)",
    )
    parser.add_argument(
        "--production-project",
        type=Path,
        default=None,
        help="Override path to production/project.json",
    )
    parser.add_argument(
        "--grok-review",
        action="store_true",
        help="Enable Grok advisory in readiness (default: off)",
    )
    args = parser.parse_args(argv)

    if args.project_root.is_file():
        print(
            f"Error: expected project directory, got file: {args.project_root}",
            file=sys.stderr,
        )
        return 2

    rc, errors = run_biological_readiness_chain(
        args.project_root,
        production_project_json=args.production_project,
        skip_grok=not bool(args.grok_review),
    )
    for err in errors:
        print(f"[biological-readiness] {err}", file=sys.stderr)
    if rc == 0:
        print("[biological-readiness] All steps passed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
