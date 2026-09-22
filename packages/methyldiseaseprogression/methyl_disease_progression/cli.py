"""
CLI for methyl-disease-progression.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .progression import run_progression_report


def _parse_labels(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    tokens = [x.strip() for x in raw.split(",") if x.strip()]
    return tokens or None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Synthesize cross-stage disease progression tables from per-comparison "
            "MethylMapper and MethylEnricher outputs."
        )
    )
    parser.add_argument("--project", type=Path, required=True, help="Path to project JSON.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory (default: <project_root>/progression).",
    )
    parser.add_argument(
        "--ordered-comparison-labels",
        type=str,
        default=None,
        help="Optional comma-separated explicit stage order using comparison_label and/or disease_group.",
    )
    parser.add_argument(
        "--strict-missing",
        action="store_true",
        help="Fail when any expected stage input file is missing.",
    )
    parser.add_argument(
        "--report-md",
        action="store_true",
        help="Write an additional markdown summary report.md.",
    )
    gsm = parser.add_mutually_exclusive_group()
    gsm.add_argument(
        "--gene-set-metrics",
        action="store_true",
        help="Enable gene set overlap metrics (overrides progression.gene_set_metrics.enabled).",
    )
    gsm.add_argument(
        "--no-gene-set-metrics",
        action="store_true",
        help="Disable gene set overlap metrics (overrides progression config).",
    )
    parser.add_argument(
        "--gene-sets-path",
        type=Path,
        default=None,
        help="JSON file mapping category_id -> [gene symbols]; overrides progression.gene_sets_path.",
    )
    parser.add_argument(
        "--gene-set-profile",
        type=Path,
        default=None,
        help="Alias for --gene-sets-path (JSON profile path).",
    )
    parser.add_argument(
        "--disease-profile",
        type=str,
        default=None,
        metavar="KEY",
        help="Bundled profile name (e.g. prostate_cancer); overrides progression.disease_profile / disease_context.",
    )
    from methyl_utils.cli_resolved_config import add_resolved_config_argument

    add_resolved_config_argument(parser, help_suffix="(progression actionConfig slice)")
    args = parser.parse_args()

    if args.no_gene_set_metrics:
        gsm_enabled: Optional[bool] = False
    elif args.gene_set_metrics:
        gsm_enabled = True
    else:
        gsm_enabled = None

    resolved_progression_config = None
    if getattr(args, "resolved_config", None):
        from methyl_utils.cli_resolved_config import resolve_cli_step_config

        resolved_progression_config = resolve_cli_step_config(
            "progression",
            None,
            resolved_config_path=args.resolved_config,
        )

    try:
        summary = run_progression_report(
            project_path=args.project,
            output_dir=args.output_dir,
            ordered_comparison_labels=_parse_labels(args.ordered_comparison_labels),
            strict_missing=bool(args.strict_missing),
            report_md=bool(args.report_md),
            gene_set_metrics_enabled=gsm_enabled,
            gene_sets_path=args.gene_sets_path,
            gene_set_profile=args.gene_set_profile,
            disease_profile=args.disease_profile,
            resolved_progression_config=resolved_progression_config,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Disease progression report complete.")
    print(f"Output directory: {summary.get('output_dir')}")
    print(f"Summary JSON: {summary.get('summary_json')}")


if __name__ == "__main__":
    main()

