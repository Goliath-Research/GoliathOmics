#!/usr/bin/env python3
"""
Command-line utility for calculating methylation centroids.
Processes a single chromosome/context combination using samples from a CSV file.
"""

import argparse
import sys
from pathlib import Path
from typing import List

from .methyl_centroid import MethylCentroid


def read_samples_from_csv(csv_file: Path) -> List[str]:
    """
    Read sample IDs from a CSV file.

    Args:
        csv_file: Path to CSV file containing sample IDs (one per line)

    Returns:
        List of sample IDs
    """
    try:
        with open(csv_file, "r") as f:
            samples = [line.strip() for line in f if line.strip()]

        if not samples:
            raise ValueError(f"No samples found in {csv_file}")

        return samples
    except FileNotFoundError:
        raise FileNotFoundError(f"CSV file not found: {csv_file}")
    except Exception as e:
        raise RuntimeError(f"Error reading CSV file {csv_file}: {e}")


def main():
    """Main CLI function."""
    parser = argparse.ArgumentParser(
        description="Calculate methylation centroid for a single chromosome/context",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process chromosome 1, CG context with samples from samples.csv
  python centroid_cli.py -c 1 -x CG -s samples.csv -o ./output

  # Process chromosome X, CHG context
  python centroid_cli.py -c X -x CHG -s samples.csv -o ./output

  # Process using JSON configuration file
  python centroid_cli.py --config config.json

  # Process using JSON configuration with verbose output
  python centroid_cli.py --config config.json --verbose
        """,
    )

    # Required arguments
    parser.add_argument(
        "-c",
        "--chromosome",
        help="Chromosome identifier (e.g., '1', 'X') - required when not using --config",
    )

    parser.add_argument(
        "-x",
        "--context",
        choices=["CG", "CHG", "CHH"],
        help="Context type (CG, CHG, or CHH) - required when not using --config",
    )

    parser.add_argument(
        "-s",
        "--samples",
        type=Path,
        help="Path to CSV file containing sample directory paths (one per line) - required when not using --config",
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory for centroid files - required when not using --config",
    )

    # Optional arguments
    parser.add_argument(
        "--min-coverage",
        type=int,
        default=4,
        help="Minimum coverage for centroid positions (default: 4)",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    # JSON configuration option
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to JSON configuration file (alternative to individual parameters)"
    )

    args = parser.parse_args()

    # Validate arguments
    if args.config is not None:
        # JSON configuration mode
        if not args.config.exists():
            print(f"Error: Configuration file not found: {args.config}", file=sys.stderr)
            sys.exit(1)
        
        # Check that no other parameters are provided when using config
        conflicting_args = [
            args.chromosome, args.context, args.samples, args.output_dir
        ]
        if any(arg is not None for arg in conflicting_args):
            print("Error: When using --config, do not provide chromosome, context, samples, or output-dir parameters", file=sys.stderr)
            sys.exit(1)
    else:
        # Individual parameters mode - check required arguments
        if not args.chromosome:
            print("Error: --chromosome is required when not using --config", file=sys.stderr)
            sys.exit(1)
        if not args.context:
            print("Error: --context is required when not using --config", file=sys.stderr)
            sys.exit(1)
        if not args.samples:
            print("Error: --samples is required when not using --config", file=sys.stderr)
            sys.exit(1)
        if not args.output_dir:
            print("Error: --output-dir is required when not using --config", file=sys.stderr)
            sys.exit(1)
        
        if not args.samples.exists():
            print(f"Error: Sample file not found: {args.samples}", file=sys.stderr)
            sys.exit(1)

        if args.output_dir.exists() and not args.output_dir.is_dir():
            print(
                f"Error: Output path exists but is not a directory: {args.output_dir}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Create output directory if it doesn't exist
        args.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.config is not None:
            # JSON configuration mode
            if args.verbose:
                print(f"Reading configuration from: {args.config}")
            
            # Read and parse JSON configuration
            import json
            with open(args.config, "r") as f:
                config_data = json.load(f)
            
            # Parse chromosomes and contexts (handle comma-separated values)
            chroms = [c.strip() for c in config_data['chrom'].split(',')]
            ctxs = [c.strip() for c in config_data['ctx'].split(',')]
            
            if args.verbose:
                print("Configuration loaded successfully")
                print(f"Chromosomes: {chroms}")
                print(f"Contexts: {ctxs}")
                print(f"Number of samples: {len(config_data.get('add_samples', []))}")
                print(f"Total combinations: {len(chroms) * len(ctxs)}")
            
            # Process each chromosome/context combination
            total_combinations = len(chroms) * len(ctxs)
            current_combination = 0
            total_samples = 0
            
            for chrom in chroms:
                for ctx in ctxs:
                    current_combination += 1
                    print(f"\n{'='*60}")
                    print(f"Processing {current_combination}/{total_combinations}: {chrom}-{ctx}")
                    print(f"{'='*60}")
                    
                    # Create individual config for this combination
                    individual_config = config_data.copy()
                    individual_config['chrom'] = chrom
                    individual_config['ctx'] = ctx
                    
                    # Use the base output directory directly (files will have {chrom}-{ctx} prefix)
                    individual_config['output_dir'] = config_data['output_dir']
                    
                    # Create MethylCentroid instance
                    mc = MethylCentroid.from_json(individual_config, verbose=args.verbose)
                    
                    if args.verbose:
                        print(f"Output directory: {mc.output_dir}")
                        print(f"Number of samples: {len(mc.samples)}")
                    
                    # Execute complete workflow
                    results = mc.build_centroid()
                    total_samples += len(mc.samples)
                    
                    # Print summary for this combination
                    print(f"\n✅ Completed {chrom}-{ctx}:")
                    print(f"   Final centroid: {results.final_centroid_path}")
            
            # Print overall summary
            print(f"\n{'='*60}")
            print("ALL COMBINATIONS COMPLETED SUCCESSFULLY!")
            print(f"{'='*60}")
            print(f"Total combinations processed: {total_combinations}")
            print(f"Total samples processed: {total_samples}")
            print(f"Chromosomes: {', '.join(chroms)}")
            print(f"Contexts: {', '.join(ctxs)}")
            
        else:
            # Individual parameters mode
            # Read samples from CSV
            if args.verbose:
                print(f"Reading samples from: {args.samples}")

            add_samples = read_samples_from_csv(args.samples)

            if args.verbose:
                print(f"Found {len(add_samples)} samples")
                print(f"Processing {args.chromosome}-{args.context}")
                print(f"Output directory: {args.output_dir}")

            # Create MethylCentroid instance with cleaned parameters
            mc = MethylCentroid(
                chrom=args.chromosome,
                ctx=args.context,
                output_dir=args.output_dir,
                add_samples=add_samples,
                remove_samples=[],
                min_coverage=args.min_coverage,
                verbose=args.verbose,
            )
            
            # Execute complete workflow
            results = mc.build_centroid()

            # Print summary
            print("\nProcessing completed successfully!")
            print(f"Chromosome: {args.chromosome}")
            print(f"Context: {args.context}")
            print(f"Total samples processed: {len(add_samples)}")
            print(f"Final centroid: {results.final_centroid_path}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
