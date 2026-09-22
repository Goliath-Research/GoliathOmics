"""
Command-line interface for MethylCentroid.

This module provides a clean, modular CLI that demonstrates
the refactored architecture following SOLID principles.
"""

import argparse
import sys
from pathlib import Path
from typing import List

from .config import MethylCentroidConfig, BatchProcessingConfig, ProcessingConfig, CentroidResults
from .core import MethylCentroid
from .project_resolver import (
    group_token_requests_all_groups,
    resolve_centroid_batch_config,
    run_centroids_for_all_groups,
    run_centroid_for_one_group,
)
from methyl_utils import cleanup_gpu_memory
from methyl_utils.logging_utils import setup_logging


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with clean, organized options."""
    parser = argparse.ArgumentParser(
        description="MethylCentroid - Advanced Methylation Centroid Calculation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single chromosome/context
  python -m methyl_centroid.cli -c 1 -x CG -s samples.csv -o ./output

  # Use configuration file
  python -m methyl_centroid.cli --config config.json

  # Batch processing
  python -m methyl_centroid.cli --batch-config batch_config.json
        """
    )

    # Input configuration
    config_group = parser.add_argument_group('Configuration')
    config_group.add_argument(
        '--config', '-c',
        type=Path,
        help='Path to JSON configuration file'
    )
    config_group.add_argument(
        '--batch-config',
        type=Path,
        help='Path to batch processing configuration file'
    )
    config_group.add_argument(
        '--project', '-p',
        type=Path,
        metavar='JSON',
        help='Path to pipeline project config; use with --group to build centroid for one cohort'
    )
    config_group.add_argument(
        '--group',
        help='Which cohort to build: group1, group2, 0-based index, or project group label (e.g. all, PCa). '
        'Use --group all to run every cohort only when no group is labeled all; requires --project'
    )
    config_group.add_argument(
        '--step-override',
        type=Path,
        metavar='JSON',
        help='Optional JSON overrides for centroid step (when using --project)'
    )
    from methyl_utils.cli_resolved_config import add_resolved_config_argument

    add_resolved_config_argument(config_group, help_suffix="(centroid actionConfig slice)")

    # Individual parameters (when not using config file)
    params_group = parser.add_argument_group('Individual Parameters')
    params_group.add_argument(
        '--chromosome', '-C',
        help='Chromosome identifier (e.g., "1", "X")'
    )
    params_group.add_argument(
        '--context', '-x',
        choices=['CG', 'CHG', 'CHH'],
        help='Context type'
    )
    params_group.add_argument(
        '--samples', '-s',
        type=Path,
        help='Path to CSV file containing sample directory paths'
    )
    params_group.add_argument(
        '--output-dir', '-o',
        type=Path,
        help='Output directory for results'
    )
    
    # Metadata parameters
    metadata_group = parser.add_argument_group('Metadata')
    metadata_group.add_argument(
        '--laboratory',
        help='Laboratory or institution name'
    )
    metadata_group.add_argument(
        '--disease',
        help='Disease or condition being studied'
    )
    metadata_group.add_argument(
        '--group-label',
        dest='group',
        help='Sample group identifier (e.g., "cancer", "control") when not using --project'
    )
    metadata_group.add_argument(
        '--batch',
        help='Batch identifier for sample processing'
    )

    # Processing options
    processing_group = parser.add_argument_group('Processing Options')
    processing_group.add_argument(
        '--min-coverage',
        type=int,
        default=4,
        help='Minimum coverage threshold (default: 4)'
    )
    processing_group.add_argument(
        '--binned-stats-bins',
        type=int,
        default=20,
        help='Required number of ECDF histogram bins per position (default: 20, must be >= 1)'
    )
    gpu_group = processing_group.add_mutually_exclusive_group()
    gpu_group.add_argument(
        '--use-gpu',
        dest='use_gpu',
        action='store_true',
        help='Enable GPU acceleration (default when available)'
    )
    gpu_group.add_argument(
        '--no-gpu',
        dest='use_gpu',
        action='store_false',
        help='Disable GPU acceleration and force CPU'
    )
    parser.set_defaults(use_gpu=None)

    # Output options
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    output_group.add_argument(
        '--save-intermediate',
        action='store_true',
        help='Save intermediate results and debug information'
    )
    output_group.add_argument(
        '--disable-validation',
        action='store_true',
        help='Disable centroid validation (faster but less safe)'
    )
    output_group.add_argument(
        '--disable-visualization',
        action='store_true',
        help='Disable result visualization'
    )

    output_group.add_argument(
        '--residualize-coef-dir',
        type=Path,
        default=None,
        help='Optional residualize_fit coefficient directory. When omitted, centroids use raw betas.',
    )

    return parser


def read_samples_from_csv(csv_file: Path) -> List[str]:
    """
    Read sample paths from CSV file.

    Args:
        csv_file: Path to CSV file containing sample paths

    Returns:
        List of sample directory paths
    """
    try:
        with open(csv_file, 'r') as f:
            samples = [line.strip() for line in f if line.strip()]

        if not samples:
            raise ValueError(f"No samples found in {csv_file}")

        return samples

    except FileNotFoundError:
        raise FileNotFoundError(f"Sample file not found: {csv_file}")
    except Exception as e:
        raise RuntimeError(f"Error reading sample file {csv_file}: {e}")


def create_config_from_args(args: argparse.Namespace) -> MethylCentroidConfig:
    """
    Create configuration from command line arguments.

    Args:
        args: Parsed command line arguments

    Returns:
        MethylCentroidConfig instance
    """
    if not all([args.chromosome, args.context, args.samples, args.output_dir]):
        raise ValueError("When not using --config, all of --chromosome, --context, --samples, and --output-dir are required")

    # Check that all metadata fields are provided
    if not all([args.laboratory, args.disease, args.group, args.batch]):
        raise ValueError("When not using --config, all metadata fields (--laboratory, --disease, --group, --batch) are required")

    # Read samples from CSV
    sample_paths = read_samples_from_csv(args.samples)

    return MethylCentroidConfig(
        laboratory=args.laboratory,
        disease=args.disease,
        group=args.group,
        batch=args.batch,
        chrom=args.chromosome,
        ctx=args.context,
        output_dir=str(args.output_dir),
        add_samples=sample_paths,
        min_coverage=args.min_coverage,
        binned_stats_bins=args.binned_stats_bins,
        use_gpu=True if args.use_gpu is None else bool(args.use_gpu),
        residualize_coef_dir=(
            str(args.residualize_coef_dir) if getattr(args, "residualize_coef_dir", None) else None
        ),
    )


def create_processing_config(args: argparse.Namespace) -> ProcessingConfig:
    """
    Create processing configuration from arguments.

    Args:
        args: Parsed command line arguments

    Returns:
        ProcessingConfig instance
    """
    return ProcessingConfig(
        enable_profiling=True,  # Always enable profiling for CLI
        save_intermediate=args.save_intermediate,
        verbose_logging=args.verbose,
        enable_validation=not args.disable_validation,
        enable_visualization=not args.disable_visualization
    )


def run_single_processing(
    config: MethylCentroidConfig,
    processing_config: ProcessingConfig
) -> CentroidResults:
    """
    Run MethylCentroid for a single chromosome/context.

    Args:
        config: MethylCentroid configuration
        processing_config: Processing configuration

    Returns:
        CentroidResults containing information about centroid creation
    """
    print(f"🚀 Starting MethylCentroid processing for {config.chrom}-{config.ctx}")
    print(f"📁 Output directory: {config.output_dir}")
    n_add = len(config.add_samples or [])
    n_rem = len(config.remove_samples or [])
    print(f"📊 Cohort deltas: add_samples={n_add}, remove_samples={n_rem}")
    print(f"🎯 Minimum coverage: {config.min_coverage}")
    print()

    try:
        # Create and run MethylCentroid
        mc = MethylCentroid.from_config(config)
        results = mc.build_centroid()

        # Print results
        print("\n✅ Processing completed successfully!")
        print(f"💾 Final centroid: {results.final_centroid_path}")

        try:
            from methyl_domain.action_result import atomic_write_json, manifest_path_for

            out_dir = Path(str(config.output_dir))
            run_key_parts = [str(config.chrom), str(config.ctx), str(config.group)]
            run_key = "_".join(p for p in run_key_parts if p) or "default"
            manifest = manifest_path_for(out_dir, "pipeline.centroid", run_key)
            n_positions = None
            h5_path = Path(results.final_centroid_path)
            if h5_path.is_file():
                try:
                    import h5py

                    with h5py.File(h5_path, "r") as f:
                        if "positions" in f:
                            n_positions = int(f["positions"].shape[0])
                except Exception:
                    pass
            atomic_write_json(
                manifest,
                {
                    "schema_version": "1.0",
                    "status": "ok",
                    "action_name": "pipeline.centroid",
                    "group": str(config.group),
                    "chromosome": str(config.chrom),
                    "context": str(config.ctx),
                    "output_dir": str(out_dir),
                    "centroid_h5_path": str(h5_path),
                    "n_samples": results.total_samples_processed,
                    "n_positions": n_positions,
                    "result_code": 0,
                },
            )
        except Exception:
            import logging

            logging.getLogger(__name__).debug(
                "Could not write centroid action manifest", exc_info=True
            )

        return results

    except Exception as e:
        print(f"❌ Error during processing: {e}", file=sys.stderr)
        if processing_config.verbose_logging:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup_gpu_memory()


def run_batch_processing(batch_config: BatchProcessingConfig) -> None:
    """
    Run batch processing for multiple chromosome/context combinations.

    Args:
        batch_config: Batch processing configuration
    """
    print("🚀 Starting batch MethylCentroid processing")
    print(f"📊 Combinations to process: {len(batch_config.chromosomes)} chromosomes × {len(batch_config.contexts)} contexts")
    print(f"📁 Base output directory: {batch_config.base_config.output_dir}")
    print()

    total_combinations = len(batch_config.chromosomes) * len(batch_config.contexts)
    processed_combinations = 0
    total_samples = 0

    for chrom in batch_config.chromosomes:
        for ctx in batch_config.contexts:
            processed_combinations += 1
            print(f"\n{'='*60}")
            print(f"Processing {processed_combinations}/{total_combinations}: {chrom}-{ctx}")
            print('='*60)

            try:
                # Create config for this combination
                base_data = batch_config.base_config.model_dump()
                # Remove chrom and ctx from base_data to avoid conflicts
                base_data.pop('chrom', None)
                base_data.pop('ctx', None)

                context_overrides = batch_config.context_overrides.get(ctx, {})
                if context_overrides:
                    context_overrides = dict(context_overrides)
                    context_overrides.pop('chrom', None)
                    context_overrides.pop('ctx', None)

                combination_data = {
                    **base_data,
                    **context_overrides,
                    "chrom": chrom,
                    "ctx": ctx,
                }

                combination_config = MethylCentroidConfig(**combination_data)

                # Create default processing config
                processing_config = ProcessingConfig()

                # Run processing and get results
                results = run_single_processing(combination_config, processing_config)
                total_samples += results.total_samples_processed

                # Release GPU memory after each chromosome/context so the next combination
                # starts with a clean slate and avoids OOM on the first sample.
                cleanup_gpu_memory()

            except Exception as e:
                error_msg = f"Failed to process {chrom}-{ctx}: {e}"
                print(f"❌ {error_msg}", file=sys.stderr)

                if batch_config.continue_on_error:
                    print("Continuing with next combination...")
                    cleanup_gpu_memory()
                    continue
                else:
                    print("Stopping batch processing due to error.")
                    sys.exit(1)

    print(f"\n{'='*60}")
    print("🎉 Batch processing completed!")
    print(f"📊 Total combinations processed: {processed_combinations}")
    print(f"📈 Total samples processed: {total_samples}")
    print('='*60)


def main() -> None:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    try:
        if args.project is not None:
            if args.group is None:
                raise ValueError("--project requires --group (group1, group2, all, or 0-based index)")
            if not args.project.exists():
                raise FileNotFoundError(f"Project config not found: {args.project}")
            group_arg = args.group.strip()
            if group_arg.isdigit():
                group_arg = int(group_arg)
            if group_token_requests_all_groups(args.project, group_arg):
                run_centroids_for_all_groups(
                    args.project,
                    args.step_override,
                    resolved_config_path=getattr(args, "resolved_config", None),
                    residualize_coef_dir=getattr(args, "residualize_coef_dir", None),
                )
            else:
                run_centroid_for_one_group(
                    args.project,
                    group_arg,
                    args.step_override,
                    output_dir=args.output_dir,
                    chromosome=args.chromosome,
                    context=args.context,
                    resolved_config_path=getattr(args, "resolved_config", None),
                    residualize_coef_dir=getattr(args, "residualize_coef_dir", None),
                )

        elif args.batch_config:
            # Batch processing mode
            if not args.batch_config.exists():
                raise FileNotFoundError(f"Batch configuration file not found: {args.batch_config}")

            batch_config = BatchProcessingConfig.from_file(args.batch_config)
            if args.use_gpu is not None:
                batch_config.base_config.use_gpu = bool(args.use_gpu)
            run_batch_processing(batch_config)

        elif args.config:
            # Single processing with config file
            if not args.config.exists():
                raise FileNotFoundError(f"Configuration file not found: {args.config}")

            config = MethylCentroidConfig.from_file(args.config)
            if args.use_gpu is not None:
                config.use_gpu = bool(args.use_gpu)
            processing_config = create_processing_config(args)
            run_single_processing(config, processing_config)

        else:
            # Single processing with command line arguments
            config = create_config_from_args(args)
            processing_config = create_processing_config(args)
            run_single_processing(config, processing_config)

    except KeyboardInterrupt:
        print("\n⚠️  Processing interrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"❌ Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
