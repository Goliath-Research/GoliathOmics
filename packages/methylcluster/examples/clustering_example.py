"""
Example script demonstrating MethylCluster Python API usage.

This script shows how to:
1. Create a configuration programmatically
2. Load and align samples properly
3. Run clustering on aligned samples
4. Access and analyze results
"""

from pathlib import Path
import numpy as np
from methyl_cluster import MethylCluster, MethylClusterConfig, ClusterMetric
from methyl_utils.core.methyl_frame import MethylSample
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    # Example configuration
    config = MethylClusterConfig(
        samples=[
            "path/to/sample1",
            "path/to/sample2",
            # ... add more sample paths
        ],
        chrom="1",
        ctx="CG",
        metric=ClusterMetric.HELLINGER,
        min_cluster_size=5,
        output_dir="clustering_results"
    )
    
    # Create and run clusterer
    clusterer = MethylCluster(config)
    results = clusterer.run()
    
    logger.info(f"Found {results['n_clusters']} clusters")
    for cluster, samples in results['cluster_assignments'].items():
        logger.info(f"{cluster}: {len(samples)} samples")

if __name__ == "__main__":
    main()

