"""CLI for MethylDetectorExplorer: staged statistical and biological analysis."""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

try:
    from ..explorer import MethylDetectorExplorer
    from ..utils.core import setup_logging
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from methyl_detector.explorer import MethylDetectorExplorer
    from methyl_detector.utils.core import setup_logging

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--centroid1-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=False,
    help="Directory containing centroid H5 files (e.g. {chrom}-{context}.h5).",
)
@click.option(
    "--centroid2-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=False,
    help="Directory containing second centroid H5 files.",
)
@click.option(
    "--centroid1",
    type=click.Path(path_type=Path, exists=True),
    required=False,
    help="Path to first centroid H5 file (alternative to centroid1-dir + chromosome + context).",
)
@click.option(
    "--centroid2",
    type=click.Path(path_type=Path, exists=True),
    required=False,
    help="Path to second centroid H5 file.",
)
@click.option(
    "--chromosome",
    default="1",
    help="Chromosome identifier (e.g. 1, 2, X). Used with centroid1-dir/centroid2-dir and --context.",
)
@click.option(
    "--context",
    default="CG",
    type=click.Choice(["CG", "CHG", "CHH"]),
    help="Methylation context. Used with centroid1-dir/centroid2-dir and --chromosome.",
)
@click.option(
    "--alpha",
    type=float,
    default=0.05,
    help="FDR threshold applied after the histogram-derived Mann-Whitney U test.",
)
@click.option(
    "--min-coverage",
    type=int,
    default=4,
    help="Minimum coverage (min_coverage) for load_and_align.",
)
@click.option(
    "--min-samples-abs",
    type=int,
    default=1,
    help="Absolute minimum number of samples (N) per position in each centroid.",
)
@click.option(
    "--min-samples-pct",
    type=float,
    default=0.05,
    help="Minimum fraction of that centroid's cohort per position (default 5%%). Effective min N = max(min-samples-abs, ceil(min-samples-pct * cohort_size)) per centroid.",
)
@click.option(
    "--delta-mean-reduction",
    type=float,
    default=None,
    help="Optional aligned pre-statistical reduction threshold: keep only positions with |delta_mean| above this value before running the Mann-Whitney test and continuous overlap/effect_size stages.",
)
@click.option(
    "--min-delta-mean",
    type=float,
    default=None,
    help="Biological filter threshold for |delta_mean| after overlap/effect_size are computed.",
)
@click.option(
    "--max-overlap",
    type=float,
    default=None,
    help="Biological filter threshold for overlap (keep positions with overlap <= max-overlap).",
)
@click.option(
    "--min-effect-size",
    type=float,
    default=None,
    help="Biological filter threshold for effect_size (keep positions with effect_size >= min-effect-size).",
)
@click.option(
    "--optimize-lambda-var",
    is_flag=True,
    default=False,
    help="Optimize lambda_var to maximize Spearman correlation between effect_size and (1 - q_value) on the reduced set.",
)
@click.option(
    "--lambda-var",
    type=float,
    default=2.0,
    help="Variance penalty strength used in the final effect_size formula.",
)
@click.option(
    "--lambda-var-min",
    type=float,
    default=0.0,
    help="Minimum lambda_var considered when --optimize-lambda-var is used.",
)
@click.option(
    "--lambda-var-max",
    type=float,
    default=6.0,
    help="Maximum lambda_var considered when --optimize-lambda-var is used.",
)
@click.option(
    "--lambda-var-step",
    type=float,
    default=0.25,
    help="Step size for lambda_var search when --optimize-lambda-var is used.",
)
@click.option(
    "--ecdf-overlap-grid-size",
    type=int,
    default=512,
    help="Number of grid points used for continuous ECDF overlap integration.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Output directory for report JSON and optional CSV.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file path for report (JSON). Overrides --output-dir for report location.",
)
@click.option(
    "--csv",
    is_flag=True,
    default=False,
    help="Write result table to CSV in output-dir (or current dir if no output-dir).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
def main(
    centroid1_dir: Optional[Path],
    centroid2_dir: Optional[Path],
    centroid1: Optional[Path],
    centroid2: Optional[Path],
    chromosome: str,
    context: str,
    alpha: float,
    min_coverage: int,
    min_samples_abs: int,
    min_samples_pct: float,
    delta_mean_reduction: Optional[float],
    min_delta_mean: Optional[float],
    max_overlap: Optional[float],
    min_effect_size: Optional[float],
    optimize_lambda_var: bool,
    lambda_var: float,
    lambda_var_min: float,
    lambda_var_max: float,
    lambda_var_step: float,
    ecdf_overlap_grid_size: int,
    output_dir: Optional[Path],
    output: Optional[Path],
    csv: bool,
    verbose: bool,
) -> None:
    """MethylDetectorExplorer: staged significance, delta_mean reduction, and final effect_size."""
    setup_logging(verbose=verbose)

    if centroid1 is not None and centroid2 is not None:
        c1_path = centroid1
        c2_path = centroid2
    elif centroid1_dir is not None and centroid2_dir is not None:
        c1_path = centroid1_dir / f"{chromosome}-{context}.h5"
        c2_path = centroid2_dir / f"{chromosome}-{context}.h5"
        if not c1_path.exists():
            click.echo(f"Error: {c1_path} not found.", err=True)
            sys.exit(1)
        if not c2_path.exists():
            click.echo(f"Error: {c2_path} not found.", err=True)
            sys.exit(1)
    else:
        click.echo(
            "Error: Provide either (--centroid1 and --centroid2) or (--centroid1-dir and --centroid2-dir).",
            err=True,
        )
        sys.exit(1)

    explorer = MethylDetectorExplorer(
        centroid1_path=c1_path,
        centroid2_path=c2_path,
        alpha=alpha,
        min_coverage=min_coverage,
        min_samples_abs=min_samples_abs,
        min_samples_pct=min_samples_pct,
        delta_mean_reduction=delta_mean_reduction,
        min_delta_mean=min_delta_mean,
        max_overlap=max_overlap,
        min_effect_size=min_effect_size,
        lambda_var=lambda_var,
        optimize_lambda_var=optimize_lambda_var,
        lambda_var_min=lambda_var_min,
        lambda_var_max=lambda_var_max,
        lambda_var_step=lambda_var_step,
        ecdf_overlap_grid_size=ecdf_overlap_grid_size,
    )
    try:
        df, report = explorer.run()
    except Exception as e:
        logger.exception("MethylDetectorExplorer failed")
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    out_dir = output_dir or Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = output if output is not None else (out_dir / "methyldetectorexplorer_report.json")
    if report_path.suffix != ".json":
        report_path = Path(str(report_path) + ".json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    click.echo(f"Report written to {report_path}")
    msg = (
        f"Total positions: {report['total_positions']:,}, "
        f"After min-samples filter: {report.get('positions_after_min_samples_filter', report.get('positions_after_min_N_filter', 0)):,}, "
        f"After delta_mean reduction: {report['positions_after_delta_mean_reduction']:,}, "
        f"After statistical filter: {report['positions_after_statistical_filter']:,}, "
        f"lambda_var: {report.get('lambda_var_used', 'n/a')}, "
        f"time: {report['time_total_s']:.2f}s"
    )
    if "effect_size_vs_1_minus_q_correlation" in report:
        msg += f", effect_size vs (1-q) Spearman: {report['effect_size_vs_1_minus_q_correlation']:.4f}"
    if "effect_size_95th_percentile" in report:
        msg += f", effect_size 95th %ile: {report['effect_size_95th_percentile']:.4f}"
    click.echo(msg)

    if csv:
        csv_path = out_dir / "methyldetectorexplorer_results.csv"
        df.to_csv(csv_path, index=False)
        click.echo(f"Results CSV written to {csv_path}")


if __name__ == "__main__":
    main()
