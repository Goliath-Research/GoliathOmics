"""
Utility functions for MethylCluster.

This module provides helper functions for common operations.
"""

import numpy as np
from pathlib import Path
from typing import List, Tuple


def validate_sample_paths(sample_paths: List[str], chrom: str, ctx: str) -> List[Path]:
    """
    Validate that all sample paths exist and contain the required HDF5 files.
    
    Args:
        sample_paths: List of sample directory paths
        chrom: Chromosome identifier
        ctx: Context type
    
    Returns:
        List of validated Path objects
    
    Raises:
        FileNotFoundError: If a sample directory or HDF5 file doesn't exist
    """
    validated_paths = []
    missing_files = []
    
    for sample_path in sample_paths:
        sample_dir = Path(sample_path)
        h5_file = sample_dir / f"{chrom}-{ctx}.h5"
        
        if not sample_dir.exists():
            missing_files.append(f"Directory not found: {sample_dir}")
        elif not h5_file.exists():
            missing_files.append(f"HDF5 file not found: {h5_file}")
        else:
            validated_paths.append(sample_dir)
    
    if missing_files:
        error_msg = "Sample validation failed:\n" + "\n".join(missing_files)
        raise FileNotFoundError(error_msg)
    
    return validated_paths


def compute_distance_statistics(distance_matrix: np.ndarray) -> dict:
    """
    Compute statistics for a distance matrix.
    
    Args:
        distance_matrix: Symmetric distance matrix
    
    Returns:
        Dictionary with distance statistics
    """
    # Extract upper triangle (excluding diagonal)
    tri_upper = np.triu_indices_from(distance_matrix, k=1)
    distances = distance_matrix[tri_upper]
    
    return {
        'min': float(np.min(distances)),
        'max': float(np.max(distances)),
        'mean': float(np.mean(distances)),
        'median': float(np.median(distances)),
        'std': float(np.std(distances)),
        'q1': float(np.percentile(distances, 25)),
        'q3': float(np.percentile(distances, 75))
    }


def create_cluster_summary(labels: np.ndarray, sample_paths: List[Path]) -> dict:
    """
    Create a summary of cluster assignments.
    
    Args:
        labels: Cluster labels for each sample
        sample_paths: List of sample paths
    
    Returns:
        Dictionary with cluster summary
    """
    unique_labels = set(labels)
    n_clusters = len(unique_labels - {-1})
    n_noise = int(np.sum(labels == -1))
    
    cluster_summary = {
        'n_clusters': n_clusters,
        'n_noise': n_noise,
        'n_samples': len(labels),
        'clusters': {}
    }
    
    for label in sorted(unique_labels):
        indices = np.where(labels == label)[0]
        cluster_name = 'noise' if label == -1 else f'cluster_{label}'
        cluster_summary['clusters'][cluster_name] = {
            'size': len(indices),
            'samples': [str(sample_paths[i]) for i in indices]
        }
    
    return cluster_summary


__all__ = [
    'validate_sample_paths',
    'compute_distance_statistics',
    'create_cluster_summary'
]

