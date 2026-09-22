"""CLI: methyl-residualize-fit — train-only M-value OLS coefficients."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from methyl_utils import load_project
from methyl_utils.cli_resolved_config import add_resolved_config_argument, resolve_cli_step_config
from methyl_utils.residualize_config import ResidualizeStepConfig
from methyl_utils.residualize_fit import run_residualize_fit
from methyl_utils.residualize_project import resolve_named_step_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit train-only M-value residualization coefficients (no Group label).",
    )
    parser.add_argument("--project", "-p", type=Path, required=True, help="Pipeline project JSON")
    parser.add_argument("--output-dir", "-o", type=Path, help="Coefficient output directory")
    parser.add_argument("--step-override", type=Path, help="Optional JSON overrides")
    add_resolved_config_argument(parser)
    args = parser.parse_args()
    if not args.project.is_file():
        print(f"Project not found: {args.project}", file=sys.stderr)
        sys.exit(1)

    project = load_project(args.project)
    resolved = resolve_cli_step_config(
        "residualize",
        project,
        resolved_config_path=args.resolved_config,
        step_override_path=args.step_override,
    )
    cfg, samples, out_dir = resolve_named_step_config(
        "residualize",
        ResidualizeStepConfig,
        args.project,
        args.step_override,
        resolved_config=resolved,
        default_output_subdir="residualize",
    )
    if args.output_dir is not None:
        out_dir = str(args.output_dir)
    if not samples:
        print("No sample directories in project.", file=sys.stderr)
        sys.exit(1)
    summary = run_residualize_fit(
        samples,
        Path(out_dir),
        cfg,
        project_chromosomes=project.chromosomes,
    )
    try:
        from methyl_domain.action_result import atomic_write_json, manifest_path_for

        manifest = manifest_path_for(Path(out_dir), "pipeline.residualize_fit", "default")
        atomic_write_json(
            manifest,
            {
                "schema_version": "1.0",
                "status": "ok",
                "action_name": "pipeline.residualize_fit",
                "output_dir": str(Path(out_dir)),
                "n_files": summary.get("n_files"),
                "n_train_samples": summary.get("n_train_samples"),
                "manifest_path": str(Path(out_dir) / "residualize_manifest.json"),
                "result_code": 0,
            },
        )
    except Exception:
        pass
    print(json.dumps(summary, indent=2))
    print(f"[INFO] Wrote residualize coefficients for {summary.get('n_train_samples', 0)} sample(s) to {out_dir}")


if __name__ == "__main__":
    main()
