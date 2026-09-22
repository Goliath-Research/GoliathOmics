"""CLI for post-extraction QC guardrails."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from methyl_extraction_qc.core.writer import process_sample_extraction_qc
from methyl_extraction_qc.project_resolver import resolve_extraction_qc_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate MethylExtractor extraction manifest guardrails and write "
            "{sample_id}.extraction_qc.json in the sample directory."
        )
    )
    parser.add_argument("--project", "-p", type=Path, help="Pipeline project JSON")
    parser.add_argument("--sample-dir", type=Path, help="Sample directory containing extraction outputs")
    parser.add_argument("--sample-id", help="Sample identifier (defaults to sample directory basename)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.project is not None:
        if not args.project.is_file():
            print(f"Project file not found: {args.project}", file=sys.stderr)
            sys.exit(1)
        config = resolve_extraction_qc_config(args.project)
        if not config.sample_paths:
            print("No sample paths resolved from project.", file=sys.stderr)
            sys.exit(1)
        for sample_path in config.sample_paths:
            sample_dir = Path(sample_path)
            sample_id = sample_dir.name
            output = process_sample_extraction_qc(sample_dir, sample_id, config=config)
            if args.verbose:
                print(f"Wrote {output}")
        return

    if args.sample_dir is None:
        print("Provide --project or --sample-dir", file=sys.stderr)
        sys.exit(1)

    sample_dir = args.sample_dir
    if not sample_dir.is_dir():
        print(f"Sample directory not found: {sample_dir}", file=sys.stderr)
        sys.exit(1)
    sample_id = args.sample_id or sample_dir.name
    output = process_sample_extraction_qc(sample_dir, sample_id)
    if args.verbose:
        print(f"Wrote {output}")
