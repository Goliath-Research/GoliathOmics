"""CLI entry for ``methyl-omega-cluster`` (research analysis)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from methyl_deconv.analysis.omega_cluster import OmegaClusterConfig, run_omega_cluster_analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cluster train-healthy CellDeconv Ω (all 6 cell types), assign strata, "
            "and compare baseline vs matched-stratum vs all-pairs two-group models."
        )
    )
    parser.add_argument(
        "--cell-fractions",
        required=True,
        help="Path to cell_fractions.csv (requires sample_id, group, CD8T..Neu)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for summary JSON, assignments CSV, and PCA plot",
    )
    parser.add_argument("--healthy-group", default="all")
    parser.add_argument("--disease-group", default="PCa")
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=13)
    parser.add_argument("--k-min", type=int, default=2)
    parser.add_argument("--k-max", type=int, default=6)
    parser.add_argument("--min-train-per-class", type=int, default=8)
    parser.add_argument("--no-clr", action="store_true")
    parser.add_argument("--no-lda-sensitivity", action="store_true")
    args = parser.parse_args(argv)

    cfg = OmegaClusterConfig(
        healthy_group=str(args.healthy_group),
        disease_group=str(args.disease_group),
        test_size=float(args.test_size),
        random_state=int(args.random_state),
        k_min=int(args.k_min),
        k_max=int(args.k_max),
        min_train_per_class=int(args.min_train_per_class),
        use_clr=not args.no_clr,
        lda_residual_sensitivity=not args.no_lda_sensitivity,
    )
    summary = run_omega_cluster_analysis(
        args.cell_fractions,
        args.output_dir,
        cfg=cfg,
    )
    print(
        json.dumps(
            {
                "recommendation": summary["recommendation"],
                "results": summary["results"],
            },
            indent=2,
        )
    )
    print(f"[INFO] Wrote summary to {summary['artifacts']['summary_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
