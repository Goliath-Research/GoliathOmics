"""Command line interface for MethylDetector."""

import logging
import sys
from pathlib import Path
from typing import Optional, List, Union

import click
from methyl_utils.action_config_resolver import resolve_for_project

# Handle imports for both direct execution and module execution
try:
    from ..core.methyldetector import MethylDetector
    from ..utils.core import load_config_from_json, setup_logging
    from ..utils.project_resolver import resolve_detector_config, resolve_detector_config_per_cancer_group
except ImportError:
    # When running directly, add parent directory to path
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from methyl_detector.core.methyldetector import MethylDetector
    from methyl_detector.utils.core import load_config_from_json, setup_logging
    from methyl_detector.utils.project_resolver import resolve_detector_config, resolve_detector_config_per_cancer_group

@click.command()
@click.argument(
    'config',
    type=click.Path(path_type=Path),
    required=False,
    default=None,
)
@click.option(
    '--project', '-p',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help='Path to pipeline project config JSON; derived paths and groups used for detector config',
)
@click.option(
    '--step-override',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help='Optional JSON with detector overrides (merged over project-derived config)',
)
@click.option(
    '--resolved-config',
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help='Baked detection actionConfig slice from workflow task input (worker path).',
)
@click.option(
    '--verbose', '-v',
    is_flag=True,
    default=False,
    help='Enable verbose logging'
)
@click.option(
    '--log-file',
    type=click.Path(path_type=Path),
    default=None,
    help='Path to log file for detailed logging (summary/errors still shown on screen)'
)
@click.option(
    '--output-base',
    type=click.Path(path_type=Path),
    default=None,
    help='Override project output_base so all paths (centroids, detection, etc.) are under this directory. '
         'Use when the project JSON has a different machine path; no need for --centroid1-dir/--centroid2-dir.',
)
@click.option(
    '--centroid1-dir',
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help='Override centroid1 directory (optional; usually unnecessary if --output-base is set). Requires --project.',
)
@click.option(
    '--centroid2-dir',
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help='Override centroid2 directory (optional; usually unnecessary if --output-base is set). Requires --project.',
)
@click.option(
    '--per-cancer-group',
    is_flag=True,
    default=False,
    help='With --project: run one detection per configured comparison. '
         'Outputs to detections/<control_group>/<disease_group> for each comparison.'
)
@click.option(
    '--multi-class-model',
    is_flag=True,
    default=False,
    help='Build a native multiclass classifier from per-comparison detection DMP CSVs and class centroids. '
         'If --per-cancer-group is also set, run detection first then merge and build. '
         'If only --multi-class-model: require per-comparison detection outputs to already exist.'
)
@click.option(
    '--learned-multiclass-head',
    is_flag=True,
    default=False,
    help='With --multi-class-model: fit a multinomial logistic layer on native histogram pre-softmax scores '
         'using training cohorts from the project (same sample lists as centroid training).',
)
@click.option(
    '--group',
    type=str,
    default=None,
    help='With --project: run detection for a single comparison only (e.g. --group pca1 for healthy vs pca1). '
         'Overrides running all comparisons.'
)
@click.version_option(version='0.3.0')
def main(
    config: Optional[Path],
    project: Optional[Path],
    step_override: Optional[Path],
    resolved_config: Optional[Path],
    verbose: bool,
    log_file: Optional[Path],
    output_base: Optional[Path],
    centroid1_dir: Optional[Path],
    centroid2_dir: Optional[Path],
    per_cancer_group: bool,
    multi_class_model: bool,
    learned_multiclass_head: bool,
    group: Optional[str],
) -> None:
    """
    MethylDetector - Genomics sample classification using enhanced centroid-based approach.

    Use either CONFIG (path to detector JSON) or --project (pipeline project config).
    With --project, paths follow the canonical project layout under {output_base}/{project_name}.
    Use --per-cancer-group with --project to run one detection per configured comparison,
    writing to detections/<control_group>/<disease_group>.
    Use --multi-class-model to build a native multiclass model from existing per-comparison DMPs.
    """
    if (config is None) == (project is None):
        raise click.UsageError("Provide either CONFIG or --project (not both, not neither).")
    # When project has multiple comparisons, always use per-comparison output (detections/control/disease) to avoid overwriting
    if project is not None:
        from methyl_utils import load_project
        _proj = load_project(project, output_base_override=str(output_base) if output_base else None)
        _multi = getattr(_proj, "uses_control_disease", lambda: False)() and len(_proj.get_comparisons()) > 1
        if _multi and not (per_cancer_group or multi_class_model or group):
            per_cancer_group = True  # run each comparison to detections/healthy/pca1, etc.
        if group is not None:
            per_cancer_group = True  # --group runs one comparison via the per-group path
    if project is not None and (per_cancer_group or multi_class_model):
        from methyl_utils import load_project
        from ..utils.multiclass_export_config import multiclass_build_overrides_from_detection_step
        from ..utils.multiclass_merge import (
            check_detection_dirs_have_dmps,
            merge_dmp_csvs_from_detection_dirs,
        )

        configs_and_labels = resolve_detector_config_per_cancer_group(
            project, step_override, output_base_override=output_base
        )
        if not configs_and_labels:
            raise click.UsageError(
                "Project has fewer than 2 groups; --per-cancer-group/--multi-class-model/--group require at least one control and one disease group."
            )
        if group is not None:
            group_label = group.strip()
            configs_and_labels = [(c, lbl) for c, lbl in configs_and_labels if lbl == group_label]
            if not configs_and_labels:
                from methyl_utils import load_project
                _proj = load_project(project, output_base_override=str(output_base) if output_base else None)
                valid = [spec.disease_group for spec in _proj.get_comparisons()]
                raise click.UsageError(
                    f"--group '{group}' not found. Valid disease group labels: {valid}"
                )
        # Configure logging once
        if log_file:
            setup_logging(
                verbose=verbose,
                log_file=log_file,
                console_level='INFO',
                file_level='DEBUG' if verbose else 'INFO',
            )
        else:
            setup_logging(verbose=verbose)
        logger = logging.getLogger(__name__)

        if per_cancer_group:
            logger.info(f"Running detection for {len(configs_and_labels)} disease group(s) (control vs each)")
            for loaded_config, label in configs_and_labels:
                logger.info(f"Detection: control vs {label} -> {loaded_config.output_dir}")
                detector = MethylDetector(loaded_config)
                detector.run()
            logger.info(f"\n{'='*80}\nPer-cancer-group detection complete: {len(configs_and_labels)} group(s)")
            for loaded_config, label in configs_and_labels:
                logger.info(f"  {label}: {loaded_config.output_dir}")

        if multi_class_model:
            missing = check_detection_dirs_have_dmps(configs_and_labels)
            if missing:
                click.echo(
                    "Missing detection results for one or more classes. Run with --per-cancer-group first, or ensure each detection dir contains dmps-*.csv:",
                    err=True,
                )
                for out_dir, label in missing:
                    click.echo(f"  {label}: {out_dir}", err=True)
                sys.exit(1)
            proj = load_project(
                project,
                output_base_override=str(output_base) if output_base else None,
            )
            paths = proj.get_derived_paths()
            detection_dir = Path(paths.detection_dir)
            merged_path = detection_dir / "dmps-merged-multiclass.csv"
            logger.info(f"Merging DMPs from {len(configs_and_labels)} detection dirs -> {merged_path}")
            merge_dmp_csvs_from_detection_dirs(
                [Path(c.output_dir) for c, _ in configs_and_labels],
                merged_path,
                weights_column="effect_size",
                detection_labels=[label for _, label in configs_and_labels],
            )
            try:
                from methyl_classifier.project_resolver import build_multiclass_config_from_project
                from methyl_classifier.utils.multiclass_builder import build_multiclass_model
            except ImportError as e:
                click.echo(
                    "Building the native multiclass model requires methylclassifier. Install it and run:\n"
                    f"  python -c \"from methyl_classifier.project_resolver import build_multiclass_config_from_project; "
                    f"from methyl_classifier.utils.multiclass_builder import build_multiclass_model; "
                    f"import json; cfg = build_multiclass_config_from_project('{project}', dmps_csv='{merged_path}'); "
                    f"build_multiclass_model(cfg)\"",
                    err=True,
                )
                sys.exit(1)
            cfg = build_multiclass_config_from_project(
                str(project),
                dmps_csv=str(merged_path),
                output_model=str(Path(paths.classifier_dir) / "multiclass-classifier.pkl"),
                weights_column="weight",
                output_base_override=str(output_base) if output_base else None,
            )
            det_step = resolve_for_project("detection", proj)
            cfg.update(multiclass_build_overrides_from_detection_step(det_step))
            if learned_multiclass_head:
                cfg["train_learned_multiclass"] = True
            out_pkl = build_multiclass_model(cfg)
            logger.info(f"Multiclass model saved to {out_pkl}")
            click.echo(f"Multiclass model saved to {out_pkl}")

        return
    if project is not None:
        loaded_config = resolve_detector_config(
            project,
            step_override,
            output_base_override=output_base,
            centroid1_dir_override=centroid1_dir,
            centroid2_dir_override=centroid2_dir,
            resolved_config_path=resolved_config,
        )
    else:
        if not config.exists():
            raise click.BadParameter(f"Config file not found: {config}", param_hint="CONFIG")
        loaded_config = load_config_from_json(config)

    # Configure logging with dual handlers if log_file specified
    if log_file:
        # All logs go to file, but errors and info (summary) still appear on console
        setup_logging(
            verbose=verbose,
            log_file=log_file,
            console_level='INFO',  # Show INFO (summary) and above on console
            file_level='DEBUG' if verbose else 'INFO'  # All logs to file
        )
        logger = logging.getLogger(__name__)
        logger.info(f"Logging to file: {log_file}")
    else:
        # Standard logging to console only
        setup_logging(verbose=verbose)
        logger = logging.getLogger(__name__)
    
    try:
        logger.debug("Using loaded configuration (from CONFIG or --project)")
        detector = MethylDetector(loaded_config)
        results = detector.run()

        # Handle both single result and list of results (multi-chromosome mode)
        if isinstance(results, list):
            # Multi-chromosome mode: summarize all results
            logger.info(f"\n{'='*80}")
            logger.info(f"MethylDetector Multi-Chromosome Analysis Results")
            logger.info(f"{'='*80}")
            logger.info(f"\n📊 Processed {len(results)} chromosomes")
            
            total_statistical = sum(r.total_statistical_dmps for r in results)
            total_biological = sum(r.total_biological_dmps for r in results)
            ba_values = [r.balanced_accuracy for r in results if getattr(r, 'balanced_accuracy', None) is not None]
            avg_ba = (sum(ba_values) / len(ba_values)) if ba_values else None

            summary_lines = [
                "\n" + "="*80,
                "MethylDetector Multi-Chromosome Analysis Summary",
                "="*80,
                f"\n📊 Overall Summary:",
                f"  Chromosomes Processed: {len(results)}",
                f"  Total Statistical DMPs: {total_statistical:,}",
                f"  Total Biological DMPs: {total_biological:,}",
                f"  Average Retention Rate: {sum(r.biological_retention_rate for r in results) / len(results):.1%}"
            ]
            if avg_ba is not None:
                summary_lines.append(f"  Average Balanced Accuracy: {avg_ba:.4f}")
            
            summary_lines.append("\n🔬 Per-Chromosome Results:")
            chromosomes = loaded_config.chromosome if isinstance(loaded_config.chromosome, list) else [loaded_config.chromosome]

            for i, result in enumerate(results, 1):
                # Use the chromosome from the config list (results are in same order as config)
                chrom = chromosomes[i-1] if i <= len(chromosomes) else f'Chromosome {i}'

                dmp_count = len(result.biologically_significant_dmps_df) if result.biologically_significant_dmps_df is not None else 0
                lines = [
                    f"  [{i}] {chrom}:",
                    f"    Statistical DMPs: {result.total_statistical_dmps:,}",
                    f"    Biological DMPs: {result.total_biological_dmps:,}",
                    f"    Retention Rate: {result.biological_retention_rate:.1%}",
                    f"    Final DMPs: {dmp_count:,}"
                ]
                if getattr(result, 'balanced_accuracy', None) is not None:
                    lines.append(f"    Balanced Accuracy: {result.balanced_accuracy:.4f}")
                summary_lines.extend(lines)
            
            summary_lines.append("\n✅ Multi-chromosome analysis complete!")
            
            # Print summary to screen
            for line in summary_lines:
                logger.info(line)
                if log_file:
                    click.echo(line)
        else:
            # Single chromosome mode: original behavior
            result = results
            # Debug: check result structure
            logger.debug(f"Result type: {type(result)}")
            logger.debug(f"Result config_summary type: {type(result.config_summary)}")
            logger.debug(f"Result config_summary: {result.config_summary}")
            
            # Summary output - always shown on screen, also logged to file if log_file specified
            summary_lines = [
                "\n" + "="*60,
                "MethylDetector Analysis Results",
                "="*60,
                "\n📊 Summary:",
                f"  Statistical DMPs: {result.total_statistical_dmps:,}",
                f"  Biological DMPs: {result.total_biological_dmps:,}",
                f"  Retention Rate: {result.biological_retention_rate:.1%}",
                f"  Analysis Time: {result.timestamp}"
            ]
            
            if result.comparison_stats:
                summary_lines.append("\n🔬 Comparison Details:")
                for stats in result.comparison_stats:
                    summary_lines.extend([
                        f"  {stats.comparison_name}:",
                        f"    Positions: {stats.total_positions:,}",
                        f"    Statistical DMPs: {stats.statistical_dmps:,}",
                        f"    Biological DMPs: {stats.biological_dmps:,}",
                        f"    Processing Time: {stats.processing_time_seconds:.2f}s",
                        f"    GPU Used: {'Yes' if stats.gpu_used else 'No'}"
                    ])

            dmp_count = len(result.biologically_significant_dmps_df) if result.biologically_significant_dmps_df is not None else 0
            summary_lines.append(f"\n✅ Analysis complete! Found {dmp_count} DMPs")
            
            # Print summary to screen (and log if log_file specified)
            for line in summary_lines:
                logger.info(line)
                # Also use click.echo to ensure it appears on screen even with file logging
                if log_file:
                    click.echo(line)

    except Exception as e:
        error_msg = f"Analysis failed: {e}"
        logger.error(error_msg)
        import traceback
        traceback_str = traceback.format_exc()
        logger.error(f"Full traceback:\n{traceback_str}")
        # Error always appears on screen
        click.echo(f"\n❌ Error: {e}", err=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
