"""CLI for methyl-fragmentomics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.runner import run_fragmentomics_for_samples
from .project_resolver import resolve_fragmentomics_step_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="cfDNA fragmentomics from BAM (WPS bins, end motifs).",
    )
    parser.add_argument("--project", "-p", type=Path, required=True, help="Pipeline project JSON")
    parser.add_argument("--step-override", type=Path, help="Optional JSON overrides")
    parser.add_argument("--output-dir", "-o", type=Path, help="Override output directory")
    args = parser.parse_args()

    if not args.project.is_file():
        print(f"Project not found: {args.project}", file=sys.stderr)
        sys.exit(1)

    cfg, sample_dirs, out_dir = resolve_fragmentomics_step_config(
        args.project,
        args.step_override,
    )
    if args.output_dir is not None:
        out_dir = str(args.output_dir)

    if not cfg.enabled:
        print("[INFO] step_config.fragmentomics.enabled is false; nothing to do.")
        return

    if not sample_dirs:
        print("No sample directories in project.", file=sys.stderr)
        sys.exit(1)

    from methyl_utils import load_project

    project = load_project(args.project)
    summary = run_fragmentomics_for_samples(
        sample_dirs,
        Path(out_dir),
        cfg,
        project_chromosomes=project.chromosomes,
    )
    print(f"[INFO] Wrote fragmentomics for {len(summary.get('samples', {}))} sample(s) to {out_dir}")


if __name__ == "__main__":
    main()
