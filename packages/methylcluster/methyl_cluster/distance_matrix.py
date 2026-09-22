"""
Distance matrix computation for methylation sample clustering.

This module handles the computation of pairwise distance matrices between
methylation samples using GPU-accelerated metrics from MethylUtils, with
support for caching to improve performance.
"""

import numpy as np
import hashlib
import json
from pathlib import Path
from typing import List, Optional, Tuple
import logging

# Import MethylUtils components
try:
    from methyl_utils.core.methyl_frame import MethylSample
    from methyl_utils.metrics_factory import get_metric_factory
except ImportError:
    # Fallback for development
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'methylutils'))
    from methyl_utils.core.methyl_frame import MethylSample
    from methyl_utils.metrics_factory import get_metric_factory

logger = logging.getLogger(__name__)


class DistanceMatrixComputer:
    """
    Computes pairwise distance matrices between methylation samples.
    
    This class handles:
    - Distance computation using MethylUtils GPU-accelerated metrics
    - Caching of computed distance matrices
    - Beta parameter extraction from MethylSample instances
    """
    
    def __init__(
        self,
        metric: str,
        chrom: str,
        ctx: str,
        use_gpu: bool = True,
        cache_dir: Optional[Path] = None
    ):
        """
        Initialize the distance matrix computer.
        
        Args:
            metric: Distance metric name (e.g., 'jensen_shannon', 'hellinger')
            chrom: Chromosome identifier (e.g., '1', 'X')
            ctx: Context type (e.g., 'CG', 'CHG', 'CHH')
            use_gpu: Whether to use GPU acceleration
            cache_dir: Directory for caching distance matrices (output directory)
        """
        self.metric_factory = get_metric_factory()
        self.metric = metric
        self.chrom = chrom
        self.ctx = ctx
        self.use_gpu = use_gpu
        self.cache_dir = Path(cache_dir) if cache_dir else None
        
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Distance matrix caching enabled: {self.cache_dir}")
    
    def compute_pairwise_distances(
        self,
        samples: List[MethylSample],
        sample_paths: List[Path]
    ) -> np.ndarray:
        """
        Compute pairwise distance matrix using Beta parameters.
        
        Each sample pair is aligned to their common positions independently,
        maximizing the number of positions used for distance calculation.
        
        Args:
            samples: List of MethylSample instances (unaligned)
            sample_paths: List of sample directory paths (for cache key)
        
        Returns:
            Symmetric distance matrix of shape (n_samples, n_samples)
        """
        logger.info(f"Computing pairwise distances for {len(samples)} samples using {self.metric}")
        
        # Check for cached matrix
        cache_path = self._get_cache_path(sample_paths)
        if cache_path and cache_path.exists():
            logger.info(f"Found cached distance matrix at {cache_path}")
            cached_matrix = self._load_cached_matrix(cache_path, sample_paths)
            if cached_matrix is not None:
                logger.info(f"Using cached distance matrix")
                return cached_matrix
            else:
                logger.info(f"Cache invalid, recomputing distances")
        
        n_samples = len(samples)
        distance_matrix = np.zeros((n_samples, n_samples), dtype=np.float64)
        
        # Compute pairwise distances (upper triangle only, since matrix is symmetric)
        logger.info("Computing pairwise distances with individual sample-pair alignment...")
        total_pairs = (n_samples * (n_samples - 1)) // 2
        computed = 0
        common_positions_stats = []
        
        for i in range(n_samples):
            for j in range(i + 1, n_samples):
                # Align this specific pair to their common positions
                a1, b1, a2, b2, n_common = self._align_sample_pair(samples[i], samples[j])
                common_positions_stats.append(n_common)
                
                # Compute distance using MethylUtils metric factory
                dist = self.metric_factory.compute_distance(
                    self.metric, a1, b1, a2, b2, use_gpu=self.use_gpu
                )
                
                # Average distance across all pairwise common positions
                avg_dist = float(np.mean(dist))
                
                # Check for NaN or Inf values
                if not np.isfinite(avg_dist):
                    logger.warning(f"Non-finite distance between samples {i} and {j}: {avg_dist}. "
                                  f"Using median distance as fallback.")
                    # Use median of finite distances as fallback
                    finite_dist = dist[np.isfinite(dist)]
                    if len(finite_dist) > 0:
                        avg_dist = float(np.median(finite_dist))
                    else:
                        # If all distances are non-finite, use a large value
                        avg_dist = 1.0
                        logger.warning(f"All distances non-finite for pair ({i}, {j}), using 1.0")
                
                distance_matrix[i, j] = avg_dist
                distance_matrix[j, i] = avg_dist
                
                computed += 1
                if computed % 100 == 0:
                    logger.info(f"Computed {computed}/{total_pairs} pairwise distances")
        
        # Log statistics about common positions
        if common_positions_stats:
            logger.info(f"Common positions per pair: min={min(common_positions_stats)}, "
                       f"max={max(common_positions_stats)}, mean={np.mean(common_positions_stats):.0f}")
        
        logger.info(f"Distance matrix computation complete: shape {distance_matrix.shape}")
        
        # Final validation: check for any remaining non-finite values
        n_nan = np.sum(np.isnan(distance_matrix))
        n_inf = np.sum(np.isinf(distance_matrix))
        if n_nan > 0 or n_inf > 0:
            logger.error(f"Distance matrix contains {n_nan} NaN and {n_inf} Inf values!")
            logger.error("This indicates numerical issues with the distance metric.")
            raise ValueError(f"Distance matrix contains non-finite values: {n_nan} NaN, {n_inf} Inf")
        
        # Cache the matrix
        if cache_path:
            self._save_cached_matrix(distance_matrix, cache_path, sample_paths)
        
        return distance_matrix
    
    def _align_sample_pair(
        self,
        sample1: MethylSample,
        sample2: MethylSample
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        """
        Align two samples to their common positions and compute Beta parameters.
        
        This method finds positions common to both samples and extracts the
        aligned mC/uC values, then computes Beta distribution parameters.
        
        Args:
            sample1: First MethylSample instance
            sample2: Second MethylSample instance
        
        Returns:
            Tuple of (alpha1, beta1, alpha2, beta2, n_common_positions)
            where alpha and beta are Beta distribution parameters
        """
        # Find common positions between this pair (using numpy intersection)
        common_pos = np.intersect1d(sample1.pos, sample2.pos, assume_unique=True)
        
        if len(common_pos) == 0:
            logger.warning("No common positions between sample pair, using pseudocounts only")
            # Return minimal arrays with pseudocounts
            alpha1 = np.array([1.0], dtype=np.float32)
            beta1 = np.array([1.0], dtype=np.float32)
            alpha2 = np.array([1.0], dtype=np.float32)
            beta2 = np.array([1.0], dtype=np.float32)
            return alpha1, beta1, alpha2, beta2, 0
        
        # Fast index lookup with searchsorted (O(log n) per query)
        idx1 = np.searchsorted(sample1.pos, common_pos)
        idx2 = np.searchsorted(sample2.pos, common_pos)
        
        # Extract aligned mC/uC arrays
        aligned_mC1 = sample1.mC[idx1]
        aligned_uC1 = sample1.uC[idx1]
        aligned_mC2 = sample2.mC[idx2]
        aligned_uC2 = sample2.uC[idx2]
        
        # Compute Beta parameters with pseudocounts for numerical stability
        alpha1 = aligned_mC1.astype(np.float32) + 1.0
        beta1 = aligned_uC1.astype(np.float32) + 1.0
        alpha2 = aligned_mC2.astype(np.float32) + 1.0
        beta2 = aligned_uC2.astype(np.float32) + 1.0
        
        return alpha1, beta1, alpha2, beta2, len(common_pos)
    
    def _get_cache_path(self, sample_paths: List[Path]) -> Optional[Path]:
        """
        Generate cache file path using descriptive naming scheme.
        
        The filename format is: distance-{metric}-{chrom}-{ctx}.npz
        This makes it easy to identify which distance matrix is cached.
        
        Args:
            sample_paths: List of sample paths (unused, kept for compatibility)
        
        Returns:
            Path to cache file or None if caching is disabled
        """
        if not self.cache_dir:
            return None
        
        # Use descriptive filename: distance-{metric}-{chrom}-{ctx}.npz
        cache_filename = f"distance-{self.metric}-{self.chrom}-{self.ctx}.npz"
        return self.cache_dir / cache_filename
    
    def _save_cached_matrix(
        self,
        distance_matrix: np.ndarray,
        cache_path: Path,
        sample_paths: List[Path]
    ) -> None:
        """
        Save distance matrix to cache file.
        
        Args:
            distance_matrix: Distance matrix to cache
            cache_path: Path to cache file
            sample_paths: List of sample paths (for verification)
        """
        logger.info(f"Saving distance matrix to cache: {cache_path}")
        np.savez_compressed(
            cache_path,
            distance_matrix=distance_matrix,
            sample_paths=np.array([str(p) for p in sample_paths]),
            metric=self.metric
        )
    
    def _load_cached_matrix(self, cache_path: Path, sample_paths: List[Path]) -> Optional[np.ndarray]:
        """
        Load distance matrix from cache file with validation.
        
        Args:
            cache_path: Path to cache file
            sample_paths: Current sample paths for validation
        
        Returns:
            Cached distance matrix if valid, None if invalid
        """
        try:
            data = np.load(cache_path)
            
            # Validate that cached metric matches current metric
            if 'metric' in data:
                cached_metric = str(data['metric'])
                if cached_metric != self.metric:
                    logger.warning(f"Cached metric '{cached_metric}' != current metric '{self.metric}'. Ignoring cache.")
                    return None
            
            # Validate matrix dimensions match number of samples
            dist_matrix = data['distance_matrix']
            if dist_matrix.shape[0] != len(sample_paths):
                logger.warning(f"Cached matrix size {dist_matrix.shape[0]} != current samples {len(sample_paths)}. Ignoring cache.")
                return None
            
            # Validate sample paths match (if available)
            if 'sample_paths' in data:
                cached_paths = set(str(p) for p in data['sample_paths'])
                current_paths = set(str(p) for p in sample_paths)
                if cached_paths != current_paths:
                    logger.warning(f"Cached sample paths don't match current samples. Ignoring cache.")
                    return None
            
            return dist_matrix
            
        except Exception as e:
            logger.warning(f"Error loading cache: {e}. Ignoring cache.")
            return None


__all__ = ['DistanceMatrixComputer']

