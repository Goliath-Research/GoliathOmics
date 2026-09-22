"""
Command-line interface for MethylCluster.

This module provides a CLI for clustering methylation samples using
configuration files or a pipeline project (--project + --group).
"""

import argparse
import sys
import json
import logging
from pathlib import Path

from .config import MethylClusterConfig
from .cluster import MethylCluster
from .project_resolver import (
    resolve_cluster_config_for_group,
    write_clustering_manifest,
)


def setup_logging(verbose: bool = False) -> None:
    """
    Configure logging for the CLI.
    
    Args:
        verbose: Enable verbose (DEBUG) logging
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cluster methylation samples with the project-aware MethylCluster workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run clustering from a project for one group
  methyl-cluster --project path/to/project.json --group healthy

  # Run clustering with a standalone configuration file
  methyl-cluster --config config.json

  # Run with verbose output
  methyl-cluster --config config.json --verbose

  # Enable soft assignments with temperature
  methyl-cluster --config config.json --soft --temperature 2.5

Configuration file format:
  {
    "samples": ["/path/to/sample1", "/path/to/sample2", ...],
    "chrom": "1",
    "ctx": "CG",
    "metric": "jensen_shannon",
    "min_cluster_size": 5,
    "output_dir": "./results",
    "cache_distance_matrix": true,
    "use_gpu": true,
    "soft_assignment": false,  # New: Enable soft probabilities
    "assignment_temperature": 1.0  # New: Softmax temperature (>=0.1, <=10.0)
  }

Available metrics:
  - jensen_shannon: Jensen-Shannon distance (default)
  - hellinger: Hellinger distance

The default clustering method is `centroid`; HDBSCAN and hierarchical remain optional.
        """
    )
    
    # Config source: either --config or (--project + --group)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to JSON configuration file (use this or --project + --group)"
    )
    parser.add_argument(
        "--project", "-p",
        type=Path,
        default=None,
        help="Path to pipeline project config (requires --group)"
    )
    parser.add_argument(
        "--group", "-g",
        type=str,
        default=None,
        help="Group label for project mode (e.g. healthy); requires --project"
    )

    # Optional arguments
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output (DEBUG level logging)"
    )
    
    # New arguments for overrides
    parser.add_argument(
        "--soft",
        action="store_true",
        help="Enable soft cluster assignments with membership probabilities (overrides config)"
    )
    
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Temperature parameter for softmax in soft assignments (overrides config; default=1.0, range [0.1,10.0])"
    )
    
    args = parser.parse_args()
    
    # Validate mutually exclusive config sources
    if args.config is not None and (args.project is not None or args.group is not None):
        parser.error("Use either --config or (--project and --group), not both")
    if args.config is None and (args.project is None or args.group is None):
        parser.error("Provide either --config or both --project and --group")
    if args.project is not None and not args.project.exists():
        logger_early = logging.getLogger(__name__)
        logger_early.error("Project config not found: %s", args.project)
        sys.exit(1)
    
    # Setup logging
    setup_logging(verbose=args.verbose)
    logger = logging.getLogger(__name__)
    
    try:
        if args.project is not None:
            # Project mode: resolve config from project + group
            logger.info("Resolving config from project %s, group %s", args.project, args.group)
            config, side = resolve_cluster_config_for_group(args.project, args.group)
            group_label_for_manifest = args.group
            from_project = True
        else:
            # Standalone config file
            if not args.config.exists():
                logger.error("Configuration file not found: %s", args.config)
                sys.exit(1)
            logger.info("Loading configuration from %s", args.config)
            with open(args.config, 'r') as f:
                config_data = json.load(f)
            config = MethylClusterConfig(**config_data)
            group_label_for_manifest = None
            from_project = False
        
        # Apply CLI overrides after loading
        if args.soft:
            config.soft_assignment = True
            logger.info("CLI override: soft_assignment = True")
        
        if args.temperature != 1.0:
            config.assignment_temperature = args.temperature
            logger.info("CLI override: assignment_temperature = %s", args.temperature)
        
        # Re-validate after overrides (Pydantic ensures bounds)
        config = MethylClusterConfig.model_validate(config.model_dump())
        
        # Log configuration summary
        logger.info("="*60)
        logger.info("MethylCluster Configuration")
        logger.info("="*60)
        logger.info("Samples: %s", len(config.samples))
        logger.info("Chromosome: %s", config.chrom)
        logger.info("Context: %s", config.ctx)
        logger.info("Metric: %s", config.metric.value)
        logger.info("Min cluster size: %s", config.min_cluster_size)
        logger.info("Output directory: %s", config.output_dir)
        logger.info("GPU acceleration: %s", config.use_gpu)
        logger.info("Cache distance matrix: %s", config.cache_distance_matrix)
        logger.info("Soft assignment: %s", config.soft_assignment)
        logger.info("Assignment temperature: %s", config.assignment_temperature)
        logger.info("="*60)
        
        # Normalize output directory path
        config.output_dir = str(Path(config.output_dir).resolve())
        logger.info("Normalized output directory: %s", config.output_dir)
        
        # Create and run clustering pipeline
        logger.info("Starting clustering pipeline...")
        cluster = MethylCluster(config)
        results = cluster.run()
        
        # When run from project, write manifest and assignments for downstream centroid step
        if from_project and group_label_for_manifest:
            write_clustering_manifest(
                config.output_dir,
                group_label_for_manifest,
                results,
                side,
            )
        
        # Display results
        logger.info("="*60)
        logger.info("Clustering Results")
        logger.info("="*60)
        logger.info("Number of clusters: %s", results['n_clusters'])
        logger.info("Noise samples: %s", results['n_noise'])
        logger.info("Cluster sizes:")
        for cluster_name, size in results['cluster_sizes'].items():
            logger.info("  %s: %s samples", cluster_name, size)
        logger.info("="*60)
        logger.info("Results saved to: %s", config.output_dir)
        logger.info("="*60)
        
        logger.info("Clustering complete!")
        
    except KeyboardInterrupt:
        logger.info("\nClustering interrupted by user")
        sys.exit(130)
    
    except Exception as e:
        logger.exception(f"Clustering failed with error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

