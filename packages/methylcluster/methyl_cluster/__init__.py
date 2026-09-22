"""
MethylCluster: HDBSCAN clustering for methylation samples.

This package provides GPU-accelerated clustering of methylation samples
using HDBSCAN with precomputed distance matrices from MethylUtils metrics.
"""

from .config import MethylClusterConfig, ClusterMetric
from .cluster import MethylCluster
from .distance_matrix import DistanceMatrixComputer
from .visualization import ClusterVisualizer

__version__ = '1.0.0'

__all__ = [
    'MethylCluster',
    'MethylClusterConfig',
    'ClusterMetric',
    'DistanceMatrixComputer',
    'ClusterVisualizer',
]

