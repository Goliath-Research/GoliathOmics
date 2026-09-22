"""CLI for methyl-cell-deconv."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from methyl_utils import load_project
from methyl_utils.cli_resolved_config import add_resolved_config_argument, resolve_cli_step_config

from .core.runner import run_cell_deconv_for_samples
from .project_resolver import resolve_cell_deconv_step_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Houseman leukocyte cell-type deconvolution (FlowSorted.Blood.EPIC IDOL).",
    )
    parser.add_argument("--project", "-p", type=Path, required=True, help="Pipeline project JSON")
    parser.add_argument("--output-dir", "-o", type=Path, help="Override output directory")
    parser.add_argument("--step-override", type=Path, help="Optional JSON overrides")
    add_resolved_config_argument(parser)
    args = parser.parse_args()

    if not args.project.is_file():
        print(f"Project not found: {args.project}", file=sys.stderr)
        sys.exit(1)

    project = load_project(args.project)
    resolved = resolve_cli_step_config(
        "cell_deconvolution",
        project,
        resolved_config_path=args.resolved_config,
        step_override_path=args.step_override,
    )
    cfg, samples, out_dir = resolve_cell_deconv_step_config(
        args.project,
        args.step_override,
        resolved_config=resolved,
    )
    if args.output_dir is not None:
        out_dir = str(args.output_dir)

    if not samples:
        print("No sample directories in project.", file=sys.stderr)
        sys.exit(1)

    summary = run_cell_deconv_for_samples(samples, Path(out_dir), cfg)
    print(json.dumps(summary, indent=2))
    print(f"[INFO] Wrote cell_fractions.csv for {summary.get('n_samples', 0)} sample(s) to {out_dir}")


if __name__ == "__main__":
    main()
