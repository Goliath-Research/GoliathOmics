"""
Sample Manager for MethylCentroid.

This module handles sample loading, caching, and memory management
following SOLID principles with clear separation of concerns.
"""

from pathlib import Path
from typing import List, Union, Optional, Tuple, Dict, Any
import numpy as np
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import psutil
from functools import lru_cache

from methyl_utils import MethylSample, get_memory_usage, get_memory_manager
from ..config import ProcessingConfig


class SmartSampleCache:
    """
    Intelligent sample cache with memory management and LRU eviction.

    This cache automatically manages memory usage and evicts least recently used
    samples when memory limits are approached.
    """

    def __init__(self, memory_manager, max_memory_gb: float = None):
        """
        Initialize the smart sample cache.

        Args:
            memory_manager: MethylUtils memory manager instance
            max_memory_gb: Maximum memory to use for caching (None = auto-detect)
        """
        self.memory_manager = memory_manager
        self.cache: OrderedDict[Path, MethylSample] = OrderedDict()
        self.memory_usage_mb: Dict[Path, float] = {}

        # Set memory limit based on available memory
        if max_memory_gb is None:
            memory_info = memory_manager.get_memory_usage()
            total_memory_gb = memory_info.get('total_gb', 400)  # Default to 400GB
            # Use 70% of available memory for caching
            self.max_memory_gb = total_memory_gb * 0.7
        else:
            self.max_memory_gb = max_memory_gb

        self.total_cached_memory_mb = 0.0

    def get(self, sample_path: Path, loader_func: callable = None) -> Optional[MethylSample]:
        """
        Get a sample from cache, loading it if not present.

        Args:
            sample_path: Path to the sample
            loader_func: Function to load sample if not in cache

        Returns:
            MethylSample instance or None if loading fails
        """
        # Check if in cache
        if sample_path in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(sample_path)
            return self.cache[sample_path]

        # Not in cache, load if loader provided
        if loader_func:
            try:
                sample = loader_func(sample_path)
                if sample:
                    self._add_to_cache(sample_path, sample)
                return sample
            except Exception:
                return None

        return None

    def _add_to_cache(self, sample_path: Path, sample: MethylSample):
        """
        Add sample to cache with memory management.

        Args:
            sample_path: Path to the sample
            sample: MethylSample instance
        """
        # Calculate memory usage
        memory_mb = self._calculate_sample_memory_usage(sample)

        # Check if we need to evict before adding
        while self.total_cached_memory_mb + memory_mb > self.max_memory_gb * 1024:
            if not self._evict_lru():
                break  # Can't evict more

        # Add to cache
        self.cache[sample_path] = sample
        self.memory_usage_mb[sample_path] = memory_mb
        self.total_cached_memory_mb += memory_mb

    def _calculate_sample_memory_usage(self, sample: MethylSample) -> float:
        """Calculate memory usage of a MethylSample in MB."""
        total_bytes = 0

        # Core arrays
        for attr in ['pos', 'mC', 'uC', 'tnc']:
            if hasattr(sample, attr):
                arr = getattr(sample, attr)
                if hasattr(arr, 'nbytes'):
                    total_bytes += arr.nbytes

        # Centroid arrays
        for attr in ['N', 'Sx', 'Sx2']:
            if hasattr(sample, attr):
                arr = getattr(sample, attr)
                if arr is not None and hasattr(arr, 'nbytes'):
                    total_bytes += arr.nbytes

        return total_bytes / (1024 * 1024)  # Convert to MB

    def _evict_lru(self) -> bool:
        """Evict least recently used sample. Returns True if eviction occurred."""
        if not self.cache:
            return False

        # Get LRU item
        lru_path, lru_sample = self.cache.popitem(last=False)  # FIFO order

        # Release sample references and any internal state (avoid memory leak)
        try:
            if hasattr(lru_sample, "close"):
                lru_sample.close()
        except Exception:
            pass

        # Update memory tracking
        if lru_path in self.memory_usage_mb:
            self.total_cached_memory_mb -= self.memory_usage_mb[lru_path]
            del self.memory_usage_mb[lru_path]

        return True

    def clear(self):
        """Clear all cached samples and release their resources."""
        for _path, sample in list(self.cache.items()):
            try:
                if hasattr(sample, "close"):
                    sample.close()
            except Exception:
                pass
        self.cache.clear()
        self.memory_usage_mb.clear()
        self.total_cached_memory_mb = 0.0

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            'cached_samples': len(self.cache),
            'memory_usage_mb': self.total_cached_memory_mb,
            'memory_limit_gb': self.max_memory_gb,
            'memory_usage_percent': (self.total_cached_memory_mb / (self.max_memory_gb * 1024)) * 100
        }


class SampleManager:
    """
    Manages sample loading, caching, and memory-aware parallel processing.

    This class follows the Single Responsibility Principle by focusing solely
    on sample management operations.
    """

    def __init__(self, processing_config: ProcessingConfig, chrom: str, ctx: str):
        """
        Initialize the sample manager.

        Args:
            processing_config: Processing configuration
            chrom: Chromosome identifier
            ctx: Context identifier
        """
        self.processing_config = processing_config
        self.chrom = chrom
        self.ctx = ctx

        # Initialize memory manager
        self.memory_manager = get_memory_manager()
        self.max_cache_size = self.memory_manager.get_recommended_cache_size()
        self._aligned_cache = {}

        # Initialize cache if enabled
        if processing_config.enable_caching:
            self.sample_cache = SmartSampleCache(self.memory_manager)
        else:
            self.sample_cache = None

        # Track file paths for samples
        self.sample_paths = []

    def prepare_sample_paths(self, samples: List[str], add_samples: List[str] = None) -> List[Path]:
        """
        Prepare sample file paths for a chromosome/context combination.

        Args:
            samples: Base sample directory paths
            add_samples: Additional sample directory paths

        Returns:
            List of sample file paths
        """
        sample_paths = []

        # Add base samples
        for sample_dir in samples:
            sample_path = Path(sample_dir) / f"{self.chrom}-{self.ctx}.h5"
            if sample_path.exists():
                sample_paths.append(sample_path)

        # Add additional samples
        if add_samples:
            for sample_dir in add_samples:
                sample_path = Path(sample_dir) / f"{self.chrom}-{self.ctx}.h5"
                if sample_path.exists():
                    sample_paths.append(sample_path)

        self.sample_paths = sample_paths
        return sample_paths

    def load_sample(self, sample_path: Union[str, Path], memory_map: bool = True) -> Optional[MethylSample]:
        """
        Load a sample with optimized memory management.

        Args:
            sample_path: Path to the sample file
            memory_map: Whether to use memory mapping for large files

        Returns:
            MethylSample instance
        """
        from methyl_utils import MethylSample

        # Use memory mapping for large files to reduce memory footprint
        sample_path_obj = Path(sample_path)
        file_size_gb = sample_path_obj.stat().st_size / (1024**3)

        # Use memory mapping for files > 1GB or when explicitly requested
        use_memory_map = memory_map and file_size_gb > 1.0

        try:
            # Load sample
            methyl_sample = MethylSample.load_from_h5(sample_path)

            # Ensure arrays are NumPy arrays (handle CuPy to NumPy conversion)
            methyl_sample = self._ensure_numpy_arrays(methyl_sample)

            # Pre-compute and cache statistical properties if this is a centroid
            if methyl_sample.is_centroid and len(methyl_sample.pos) > 1000:
                # Cache statistical properties to avoid recomputation
                _ = methyl_sample.mean  # Trigger computation and caching
                _ = methyl_sample.variance
                _ = methyl_sample.precision

            return methyl_sample

        except Exception as e:
            print(f"Failed to load sample {sample_path}: {e}")
            raise

    def _ensure_numpy_arrays(self, methyl_sample: MethylSample) -> MethylSample:
        """
        Ensure all arrays in MethylSample are NumPy arrays, converting from CuPy if needed.

        Args:
            methyl_sample: MethylSample instance

        Returns:
            MethylSample with NumPy arrays (on CPU)
        """
        # Use to_cpu() method to convert GPU arrays to CPU and ensure numpy arrays
        # This handles both GPU->CPU conversion and Series->numpy array conversion
        return methyl_sample.to_cpu()

    def load_samples_parallel(self, sample_paths: List[Path], max_workers: Optional[int] = None) -> List[MethylSample]:
        """
        Load samples in parallel with memory-aware worker management.

        Args:
            sample_paths: List of sample file paths
            max_workers: Maximum number of parallel workers (None = auto-detect)

        Returns:
            List of loaded MethylSample instances
        """
        if not sample_paths:
            return []

        # Determine optimal worker count
        if max_workers is None:
            max_workers = self._calculate_optimal_workers(len(sample_paths))

        self.logger.info(f"Loading {len(sample_paths)} samples with {max_workers} parallel workers")

        loaded_samples = []
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            # Submit all loading tasks
            futures = {}
            for i, sample_path in enumerate(sample_paths):
                future = executor.submit(self._load_sample_for_alignment_memory_aware, sample_path, i)
                futures[future] = i

            # Process completed tasks with progress monitoring
            successful_loads = 0
            failed_loads = 0

            with tqdm(total=len(sample_paths), desc="Loading samples") as pbar:
                for future in as_completed(futures):
                    sample_idx = futures[future]
                    try:
                        result = future.result()
                        if result is not None:
                            loaded_samples.append(result)
                            successful_loads += 1
                        else:
                            failed_loads += 1
                    except Exception as e:
                        self.logger.error(f"Error processing sample {sample_idx}: {e}")
                        failed_loads += 1
                    finally:
                        pbar.update(1)

                    # Memory monitoring and cache management
                    if successful_loads % 10 == 0:  # Check every 10 samples
                        self._monitor_memory_during_loading()

        finally:
            executor.shutdown(wait=True)

        self.logger.info(f"Sample loading completed: {successful_loads} successful, {failed_loads} failed")
        return loaded_samples

    def _calculate_optimal_workers(self, num_samples: int) -> int:
        """
        Calculate optimal number of workers based on system resources and sample count.

        Args:
            num_samples: Number of samples to process

        Returns:
            Optimal number of workers
        """
        # Get memory information for dynamic worker calculation
        memory_info = get_memory_usage()
        total_memory_gb = self.memory_manager.system_memory_limit_gb
        available_memory_gb = total_memory_gb - (memory_info.get('system_memory_mb', 0) / 1024)

        # Dynamic worker calculation based on memory and CPU
        estimated_memory_per_sample_gb = 0.8  # Conservative estimate: 800MB per sample

        # Memory-based limit: reserve 30% of memory for processing
        memory_reserved_gb = total_memory_gb * 0.3
        max_workers_by_memory = max(1, int((available_memory_gb - memory_reserved_gb) / estimated_memory_per_sample_gb))

        # CPU-based limit: use 75% of available CPUs
        cpu_count = psutil.cpu_count()
        max_workers_by_cpu = max(1, int(cpu_count * 0.75))

        # Context-based adjustment (CHH has more positions, needs more memory)
        context_multiplier = {'CG': 1.0, 'CHG': 4.0, 'CHH': 16.0}.get(self.ctx, 1.0)
        max_workers_by_memory = max(1, int(max_workers_by_memory / context_multiplier))

        # Final worker count
        max_workers = min(max_workers_by_memory, max_workers_by_cpu, num_samples)

        return max_workers

    def _load_sample_for_alignment_memory_aware(self, sample_path: Path, sample_idx: int) -> Optional[MethylSample]:
        """
        Load a sample for alignment with memory-aware caching.

        Args:
            sample_path: Path to the sample file
            sample_idx: Index of the sample for tracking

        Returns:
            MethylSample instance or None if loading fails
        """
        try:
            # Check memory before loading
            memory_info = self.memory_manager.get_memory_usage()
            available_gb = memory_info.get('available_gb', 350)

            # If memory is getting low, clear cache to free up space
            if available_gb < 50:  # Less than 50GB available
                if self.sample_cache:
                    self.sample_cache.clear()

            # Load sample with memory mapping for large files
            sample = self.load_sample(sample_path, memory_map=True)

            if sample is None or len(sample.pos) == 0:
                self.logger.warning(f"Sample {sample_path.name} has no valid data")
                return None

            # Filter positions with coverage to reduce memory usage
            coverage = sample.get_coverage()
            valid_mask = coverage > 0

            if np.any(valid_mask):
                # Create filtered sample
                filtered_sample = sample.create_aligned_sample(valid_mask.astype(bool))
                sample = filtered_sample

                # Log sample statistics for first few samples
                if sample_idx < 5:
                    self.logger.debug(f"Sample {sample_idx}: {len(sample.pos):,} positions, "
                                    f"coverage: {coverage[valid_mask].mean():.1f}")

                return sample
            else:
                self.logger.warning(f"Sample {sample_path.name} has no positions with coverage")
                return None

        except Exception as e:
            self.logger.error(f"Failed to load sample {sample_path}: {e}")
            return None

    def _monitor_memory_during_loading(self):
        """Monitor memory usage during sample loading and take corrective actions."""
        memory_info = get_memory_usage()
        used_percent = memory_info.get('percent_used', 0)

        # If memory usage is too high, clear cache
        if used_percent > 85:
            self.logger.warning(f"High memory usage detected ({used_percent:.1f}%), clearing cache")
            if self.sample_cache:
                self.sample_cache.clear()

        # Force garbage collection periodically
        import gc
        collected = gc.collect()
        if collected > 0:
            self.logger.debug(f"Garbage collection freed {collected} objects")

    def get_cache_stats(self) -> Dict:
        """Get cache statistics if caching is enabled."""
        if self.sample_cache:
            return self.sample_cache.get_stats()
        return {"caching": "disabled"}

    def clear_cache(self):
        """Clear the sample cache."""
        if self.sample_cache:
            self.sample_cache.clear()
        self._aligned_cache.clear()

    def get_aligned_sample(self, sample_path: Path, centroid_positions: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get aligned sample data for given positions.
        
        Args:
            sample_path: Path to sample file
            centroid_positions: Optional positions to align to. If None, returns raw sample data.
        
        Returns:
            Tuple of (mC, uC) arrays
        """
        cache_key = (sample_path, tuple(centroid_positions) if centroid_positions is not None else None)
        if cache_key not in self._aligned_cache:
            if len(self._aligned_cache) >= self.max_cache_size:
                # Evict least recently used (simple FIFO for now)
                oldest_key = next(iter(self._aligned_cache))
                del self._aligned_cache[oldest_key]
            sample_obj = None
            try:
                sample_obj = self.load_sample(sample_path)
                # Align sample to centroid positions if provided
                if centroid_positions is not None:
                    aligned_sample = sample_obj.align_to_positions(centroid_positions)
                    mC = np.asarray(aligned_sample.mC.values, dtype=np.uint32).copy()
                    uC = np.asarray(aligned_sample.uC.values, dtype=np.uint32).copy()
                else:
                    mC = np.asarray(sample_obj.mC.values, dtype=np.uint32).copy()
                    uC = np.asarray(sample_obj.uC.values, dtype=np.uint32).copy()
                self._aligned_cache[cache_key] = (mC, uC)
            finally:
                if sample_obj is not None and hasattr(sample_obj, "close"):
                    try:
                        sample_obj.close()
                    except Exception:
                        pass
        return self._aligned_cache[cache_key]

    # Call clear_cache() after updating centroid

    # Logger setup (would be injected via dependency injection in production)
    @property
    def logger(self):
        """Get logger instance."""
        import logging
        return logging.getLogger(__name__)
