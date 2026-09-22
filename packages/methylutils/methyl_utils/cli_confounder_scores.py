"""CLI: methyl-confounder-scores — label-free smoking/age/BMI/inflammation scores."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from methyl_utils import load_project
from methyl_utils.cli_resolved_config import add_resolved_config_argument, resolve_cli_step_config
from methyl_utils.confounder_scores import load_panel, run_confounder_scores
from methyl_utils.residualize_config import ConfounderScoresStepConfig
from methyl_utils.residualize_project import resolve_named_step_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute label-free methylation confounder scores (smoking, clocks, BMI, CRP).",
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
        "methylation_confounder_scores",
        project,
        resolved_config_path=args.resolved_config,
        step_override_path=args.step_override,
    )
    cfg, samples, out_dir = resolve_named_step_config(
        "methylation_confounder_scores",
        ConfounderScoresStepConfig,
        args.project,
        args.step_override,
        resolved_config=resolved,
        default_output_subdir="confounder_scores",
    )
    if args.output_dir is not None:
        out_dir = str(args.output_dir)
    panels = {}
    mapping = {
        "smoking_score": cfg.smoking_panel_path,
        "age_score": cfg.age_clock_path,
        "bmi_score": cfg.bmi_panel_path,
        "crp_score": cfg.inflammation_panel_path,
    }
    for name, path in mapping.items():
        if not path:
            continue
        panel = load_panel(path)
        panels[name] = panel
    if not panels:
        print(
            "No confounder panels configured. Set smoking_panel_path / age_clock_path / "
            "bmi_panel_path / inflammation_panel_path in actionConfig.methylation_confounder_scores.",
            file=sys.stderr,
        )
        sys.exit(1)
    if cfg.min_coverage is None or cfg.min_sites_fraction is None:
        print(
            "actionConfig.methylation_confounder_scores.min_coverage and min_sites_fraction are required.",
            file=sys.stderr,
        )
        sys.exit(1)
    contexts = cfg.contexts or ["CG"]
    summary = run_confounder_scores(
        samples,
        Path(out_dir),
        panels=panels,
        contexts=contexts,
        min_coverage=int(cfg.min_coverage),
        min_sites_fraction=float(cfg.min_sites_fraction),
    )
    try:
        from methyl_domain.action_result import atomic_write_json, manifest_path_for

        manifest = manifest_path_for(Path(out_dir), "pipeline.methylation_confounder_scores", "default")
        atomic_write_json(
            manifest,
            {
                "schema_version": "1.0",
                "status": "ok",
                "action_name": "pipeline.methylation_confounder_scores",
                "output_dir": str(Path(out_dir)),
                "output_csv": summary.get("output_csv"),
                "n_samples": summary.get("n_samples"),
                "n_columns": summary.get("n_columns"),
                "result_code": 0,
            },
        )
    except Exception:
        pass
    print(json.dumps(summary, indent=2))
    print(f"[INFO] Wrote confounder_scores.csv for {summary.get('n_samples', 0)} sample(s) to {out_dir}")


if __name__ == "__main__":
    main()
