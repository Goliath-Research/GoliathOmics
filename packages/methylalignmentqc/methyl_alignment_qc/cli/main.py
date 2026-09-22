"""
CLI for MethylAlignmentQC: parse alignment QC metrics and write one JSON per sample.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

from methyl_alignment_qc.core import process_samples_to_qc_jsons
from methyl_alignment_qc.core.parser import find_metrics_files
from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config


def _resolve_samples_arg(samples_arg: str) -> List[str]:
    """Expand --samples: if it's a file path, read lines or JSON array; else return [samples_arg]."""
    p = Path(samples_arg)
    if p.is_file():
        content = p.read_text().strip()
        if content.startswith("["):
            data = json.loads(content)
            return [str(x).strip() for x in data if str(x).strip()]
        return [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
    return [samples_arg]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse Parabricks alignment QC metrics; write one V2 JSON per sample to output dir.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  methyl-qc --project project.json
  methyl-qc --project project.json --step-override overrides.json
  methyl-qc --samples sample_list.txt --output-dir /out/alignment_qc
  methyl-qc --metrics-root /data/metrics --output-dir /out/alignment_qc
        """,
    )
    parser.add_argument("--project", "-p", type=Path, help="Path to pipeline project JSON")
    parser.add_argument("--step-override", type=Path, help="Optional JSON overrides for alignment_qc step")
    parser.add_argument(
        "--samples",
        action="append",
        default=[],
        metavar="PATH",
        help="Sample directory or file listing sample dirs (one per line or JSON array); requires --output-dir",
    )
    parser.add_argument(
        "--metrics-root",
        type=Path,
        metavar="DIR",
        help="Discover samples under DIR (rglob *deduplicate_metrics.txt); requires --output-dir",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        metavar="DIR",
        help="Output directory; one JSON per sample as {output_dir}/{basename}.json",
    )
    parser.add_argument("--no-validation", action="store_true", help="Skip schema validation of each JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Resolve mode: --project, or --samples, or --metrics-root
    if args.project is not None:
        if args.project.exists():
            config = resolve_alignment_qc_config(args.project, args.step_override)
            if args.no_validation:
                config = config.model_copy(update={"validate_schema": False})
            if args.verbose:
                print(f"Project: {args.project}")
                print(f"Output dir: {config.output_dir}")
                print(f"Samples: {len(config.sample_paths)}")
            if not config.sample_paths:
                print("No sample paths in project.", file=sys.stderr)
                sys.exit(1)
            process_samples_to_qc_jsons(
                config.sample_paths,
                config.output_dir,
                validate_schema=config.validate_schema,
                fragmentomics=config.fragmentomics,
                bisulfite_conversion=config.bisulfite_conversion,
                cycle_screening=config.cycle_screening,
                optional_guardrails=config.optional_guardrails,
                alignment_guardrails=config.alignment_guardrails,
                core_guardrails=config.core_guardrails,
            )
            if args.verbose:
                print(f"Wrote {len(config.sample_paths)} JSON(s) to {config.output_dir}")
            return
        print(f"Project file not found: {args.project}", file=sys.stderr)
        sys.exit(1)

    if args.samples:
        if args.output_dir is None:
            print("--output-dir is required when using --samples", file=sys.stderr)
            sys.exit(1)
        expanded: List[str] = []
        for s in args.samples:
            expanded.extend(_resolve_samples_arg(s))
        if not expanded:
            print("No sample paths from --samples", file=sys.stderr)
            sys.exit(1)
        if args.verbose:
            print(f"Output dir: {args.output_dir}, Samples: {len(expanded)}")
        process_samples_to_qc_jsons(
            expanded,
            str(args.output_dir),
            validate_schema=not args.no_validation,
        )
        if args.verbose:
            print(f"Wrote {len(expanded)} JSON(s) to {args.output_dir}")
        return

    if args.metrics_root is not None:
        if args.output_dir is None:
            print("--output-dir is required when using --metrics-root", file=sys.stderr)
            sys.exit(1)
        metrics_root = Path(args.metrics_root)
        if not metrics_root.is_dir():
            print(f"Metrics root is not a directory: {metrics_root}", file=sys.stderr)
            sys.exit(1)
        from methyl_alignment_qc.core.parser import find_metrics_files

        metrics_files = find_metrics_files(metrics_root)
        if not metrics_files:
            print(f"No metrics files found under {metrics_root}", file=sys.stderr)
            sys.exit(1)
        # One sample dir per metrics file (parent of file); dedupe by path
        sample_dirs = list({str(f.parent) for f in metrics_files})
        if args.verbose:
            print(f"Discovered {len(sample_dirs)} samples under {metrics_root}")
        process_samples_to_qc_jsons(
            sample_dirs,
            str(args.output_dir),
            validate_schema=not args.no_validation,
        )
        if args.verbose:
            print(f"Wrote {len(sample_dirs)} JSON(s) to {args.output_dir}")
        return

    print("Provide one of: --project, --samples, or --metrics-root", file=sys.stderr)
    parser.print_help(sys.stderr)
    sys.exit(1)
