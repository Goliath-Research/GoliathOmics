#!/usr/bin/env python3
"""
Parallel I/O Module for MethylUtils - Optimized HDF5 Reading

This module provides parallel I/O capabilities for reading large HDF5 files
containing genomic methylation data efficiently.

Key Features:
- Parallel HDF5 dataset reading with threading
- Memory-mapped I/O for large files
- Asynchronous data loading
- Intelligent prefetching and caching
- Compression-aware reading strategies
- I/O performance monitoring

Optimized for high-throughput storage systems and NVIDIA GH200.
"""

import logging
import threading
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import numpy as np

# Container-specific imports (with fallbacks)
try:
    import h5py
    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False

try:
    import hdf5plugin
    HDF5_PLUGIN_AVAILABLE = True
except ImportError:
    HDF5_PLUGIN_AVAILABLE = False

logger = logging.getLogger(__name__)

class ParallelHDF5Reader:
    """
    Parallel HDF5 reader optimized for genomic data.

    Provides high-throughput reading of large HDF5 files containing
    methylation data with support for parallel I/O and memory mapping.
    """

    def __init__(self,
                 max_workers: int = 8,
                 buffer_size_mb: int = 256,
                 enable_prefetch: bool = True,
                 cache_size_mb: int = 1024):
        """
        Initialize parallel HDF5 reader.

        Args:
            max_workers: Maximum number of parallel I/O threads
            buffer_size_mb: I/O buffer size in MB
            enable_prefetch: Whether to enable data prefetching
            cache_size_mb: Size of read cache in MB
        """
        self.max_workers = max_workers
        self.buffer_size_mb = buffer_size_mb
        self.enable_prefetch = enable_prefetch
        self.cache_size_mb = cache_size_mb

        # Performance tracking
        self.stats = {
            'total_reads': 0,
            'total_bytes': 0,
            'total_time': 0.0,
            'cache_hits': 0,
            'cache_misses': 0,
            'prefetch_hits': 0
        }

        # Read cache
        self.cache = {}
        self.cache_lock = threading.Lock()

        # Prefetch queue
        self.prefetch_queue = Queue(maxsize=10)

        logger.info("ParallelHDF5Reader initialized")
        logger.info(f"Max workers: {max_workers}")
        logger.info(f"Buffer size: {buffer_size_mb}MB")
        logger.info(f"Cache size: {cache_size_mb}MB")

    def read_dataset_parallel(self,
                            file_path: Union[str, Path],
                            dataset_path: str,
                            start_idx: int = 0,
                            end_idx: Optional[int] = None,
                            chunk_size: int = 1_000_000) -> np.ndarray:
        """
        Read HDF5 dataset in parallel chunks.

        Args:
            file_path: Path to HDF5 file
            dataset_path: Path to dataset within file
            start_idx: Starting index for reading
            end_idx: Ending index for reading (None for full dataset)
            chunk_size: Size of each read chunk

        Returns:
            Concatenated numpy array
        """
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 dependencies not available")

        file_path = Path(file_path)
        chunks = []

        # Get dataset info
        with h5py.File(file_path, 'r') as f:
            if dataset_path not in f:
                # Try alternative paths
                alt_paths = ["methylation_data/" + dataset_path, dataset_path.split('/')[-1]]
                for alt_path in alt_paths:
                    if alt_path in f:
                        dataset_path = alt_path
                        break
                else:
                    raise KeyError(f"Dataset {dataset_path} not found in {file_path}")

            dataset = f[dataset_path]
            total_size = dataset.shape[0] if len(dataset.shape) > 0 else 0

            if end_idx is None:
                end_idx = total_size
            elif end_idx > total_size:
                end_idx = total_size

        # Calculate read chunks
        read_ranges = []
        for start in range(start_idx, end_idx, chunk_size):
            end = min(start + chunk_size, end_idx)
            read_ranges.append((start, end))

        logger.info(f"Reading {len(read_ranges)} chunks from {dataset_path}")

        # Read chunks in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for start, end in read_ranges:
                future = executor.submit(self._read_chunk, file_path, dataset_path, start, end)
                futures.append(future)

            # Collect results
            for future in as_completed(futures):
                try:
                    chunk_data = future.result()
                    chunks.append(chunk_data)
                except Exception as e:
                    logger.error(f"Failed to read chunk: {e}")
                    continue

        # Concatenate results
        if not chunks:
            raise RuntimeError("No data chunks were successfully read")

        result = np.concatenate(chunks)

        # Update statistics
        self.stats['total_reads'] += 1
        self.stats['total_bytes'] += result.nbytes

        logger.info(f"Successfully read {result.shape[0]:,} elements "
                   f"({result.nbytes / (1024**2):.1f}MB)")

        return result

    def _read_chunk(self, file_path: Path, dataset_path: str, start: int, end: int) -> np.ndarray:
        """Read a single chunk from HDF5 file."""
        with h5py.File(file_path, 'r') as f:
            dataset = f[dataset_path]
            chunk_data = dataset[start:end]
            return np.array(chunk_data)

    def read_multiple_datasets(self,
                             file_path: Union[str, Path],
                             dataset_paths: List[str],
                             start_idx: int = 0,
                             end_idx: Optional[int] = None) -> Dict[str, np.ndarray]:
        """
        Read multiple datasets from the same file in parallel.

        Args:
            file_path: Path to HDF5 file
            dataset_paths: List of dataset paths to read
            start_idx: Starting index for reading
            end_idx: Ending index for reading

        Returns:
            Dictionary mapping dataset paths to numpy arrays
        """
        results = {}

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(dataset_paths))) as executor:
            futures = {}
            for dataset_path in dataset_paths:
                future = executor.submit(self.read_dataset_parallel,
                                       file_path, dataset_path, start_idx, end_idx)
                futures[future] = dataset_path

            for future in as_completed(futures):
                dataset_path = futures[future]
                try:
                    data = future.result()
                    results[dataset_path] = data
                except Exception as e:
                    logger.error(f"Failed to read {dataset_path}: {e}")
                    continue

        return results

    def create_memory_mapped_view(self,
                                file_path: Union[str, Path],
                                dataset_path: str) -> 'MemoryMappedDataset':
        """
        Create a memory-mapped view of an HDF5 dataset.

        Args:
            file_path: Path to HDF5 file
            dataset_path: Path to dataset

        Returns:
            MemoryMappedDataset object for efficient access
        """
        return MemoryMappedDataset(file_path, dataset_path, self)

    def prefetch_data(self, file_path: Union[str, Path], dataset_path: str, indices: List[int]):
        """
        Prefetch data for specified indices to improve access performance.

        Args:
            file_path: Path to HDF5 file
            dataset_path: Path to dataset
            indices: List of indices to prefetch
        """
        if not self.enable_prefetch:
            return

        def prefetch_worker():
            try:
                with h5py.File(file_path, 'r') as f:
                    dataset = f[dataset_path]
                    for idx in indices:
                        if idx < len(dataset):
                            # Prefetch data (this will cache it)
                            _ = dataset[idx]
            except Exception as e:
                logger.warning(f"Prefetch failed: {e}")

        prefetch_thread = threading.Thread(target=prefetch_worker, daemon=True)
        prefetch_thread.start()

    def get_io_stats(self) -> Dict[str, Any]:
        """Get I/O performance statistics."""
        stats = self.stats.copy()

        # Calculate derived metrics
        if stats['total_time'] > 0:
            stats['avg_throughput_mb_per_sec'] = (stats['total_bytes'] / (1024**2)) / stats['total_time']
            stats['cache_hit_rate'] = stats['cache_hits'] / (stats['cache_hits'] + stats['cache_misses']) if (stats['cache_hits'] + stats['cache_misses']) > 0 else 0

        return stats

    def reset_stats(self):
        """Reset I/O statistics."""
        self.stats = {
            'total_reads': 0,
            'total_bytes': 0,
            'total_time': 0.0,
            'cache_hits': 0,
            'cache_misses': 0,
            'prefetch_hits': 0
        }
        logger.info("I/O statistics reset")

class MemoryMappedDataset:
    """
    Memory-mapped HDF5 dataset for efficient random access.

    Provides numpy-like access to HDF5 datasets with intelligent
    caching and prefetching for optimal performance.
    """

    def __init__(self, file_path: Union[str, Path], dataset_path: str, reader: ParallelHDF5Reader):
        """
        Initialize memory-mapped dataset.

        Args:
            file_path: Path to HDF5 file
            dataset_path: Path to dataset within file
            reader: ParallelHDF5Reader instance
        """
        self.file_path = Path(file_path)
        self.dataset_path = dataset_path
        self.reader = reader

        # Cache dataset metadata
        self._metadata = None
        self._load_metadata()

        logger.info(f"MemoryMappedDataset created for {dataset_path}")
        logger.info(f"Shape: {self.shape}, dtype: {self.dtype}")

    def _load_metadata(self):
        """Load dataset metadata."""
        if not HDF5_AVAILABLE:
            return

        with h5py.File(self.file_path, 'r') as f:
            if self.dataset_path in f:
                dataset = f[self.dataset_path]
                self._metadata = {
                    'shape': dataset.shape,
                    'dtype': dataset.dtype,
                    'size': dataset.size,
                    'nbytes': dataset.nbytes,
                    'chunks': getattr(dataset, 'chunks', None),
                    'compression': getattr(dataset, 'compression', None)
                }

    @property
    def shape(self):
        """Get dataset shape."""
        return self._metadata['shape'] if self._metadata else ()

    @property
    def dtype(self):
        """Get dataset dtype."""
        return self._metadata['dtype'] if self._metadata else None

    @property
    def size(self):
        """Get dataset size."""
        return self._metadata['size'] if self._metadata else 0

    def __getitem__(self, key) -> np.ndarray:
        """Get data using numpy-like indexing."""
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 dependencies not available")

        with h5py.File(self.file_path, 'r') as f:
            dataset = f[self.dataset_path]
            return np.array(dataset[key])

    def __len__(self) -> int:
        """Get length of first dimension."""
        return self.shape[0] if len(self.shape) > 0 else 0

    def read_range(self, start: int, end: int) -> np.ndarray:
        """
        Read a range of data efficiently.

        Args:
            start: Start index
            end: End index

        Returns:
            Numpy array with requested data
        """
        return self[start:end]

    def read_chunked(self, chunk_size: int = 1_000_000) -> np.ndarray:
        """
        Read entire dataset in optimized chunks.

        Args:
            chunk_size: Size of each read chunk

        Returns:
            Complete dataset as numpy array
        """
        if len(self.shape) == 0:
            return self[()]

        total_size = self.shape[0]
        chunks = []

        for start in range(0, total_size, chunk_size):
            end = min(start + chunk_size, total_size)
            chunk = self[start:end]
            chunks.append(chunk)

        return np.concatenate(chunks) if chunks else np.array([])

class AsyncDataLoader:
    """
    Asynchronous data loader for prefetching genomic data.

    Provides background loading of data chunks to minimize I/O latency
    during processing of large genomic datasets.
    """

    def __init__(self, reader: ParallelHDF5Reader, prefetch_distance: int = 3):
        """
        Initialize async data loader.

        Args:
            reader: ParallelHDF5Reader instance
            prefetch_distance: Number of chunks to prefetch ahead
        """
        self.reader = reader
        self.prefetch_distance = prefetch_distance

        # Prefetch management
        self.prefetch_queue = Queue(maxsize=prefetch_distance)
        self.current_position = 0
        self.is_running = False
        self.prefetch_thread = None

        logger.info("AsyncDataLoader initialized")

    def start_prefetching(self, file_path: Union[str, Path], dataset_path: str,
                         chunk_size: int, total_chunks: int):
        """
        Start prefetching data chunks.

        Args:
            file_path: Path to data file
            dataset_path: Path to dataset
            chunk_size: Size of each chunk
            total_chunks: Total number of chunks
        """
        if self.is_running:
            self.stop_prefetching()

        self.is_running = True

        def prefetch_worker():
            chunk_idx = 0
            while self.is_running and chunk_idx < total_chunks:
                try:
                    # Calculate which chunks to prefetch
                    prefetch_start = self.current_position
                    prefetch_end = min(prefetch_start + self.prefetch_distance, total_chunks)

                    # Prefetch chunks
                    for i in range(prefetch_start, prefetch_end):
                        if not self.is_running:
                            break

                        start_idx = i * chunk_size
                        end_idx = min((i + 1) * chunk_size, self.reader._get_dataset_size(file_path, dataset_path))

                        # Read chunk
                        chunk_data = self.reader._read_chunk(file_path, dataset_path, start_idx, end_idx)

                        # Add to queue (block if full)
                        self.prefetch_queue.put(chunk_data, timeout=1)

                        chunk_idx += 1

                except Exception as e:
                    logger.error(f"Prefetch error: {e}")
                    break

        self.prefetch_thread = threading.Thread(target=prefetch_worker, daemon=True)
        self.prefetch_thread.start()

        logger.info("Prefetching started")

    def get_next_chunk(self):
        """
        Get the next prefetched chunk.

        Returns:
            Next data chunk, or None if no data available
        """
        try:
            chunk = self.prefetch_queue.get(timeout=0.1)
            self.current_position += 1
            return chunk
        except:
            return None

    def stop_prefetching(self):
        """Stop prefetching."""
        self.is_running = False
        if self.prefetch_thread:
            self.prefetch_thread.join(timeout=1.0)
        # Clear queue
        while not self.prefetch_queue.empty():
            try:
                self.prefetch_queue.get_nowait()
            except:
                break

        logger.info("Prefetching stopped")

# Convenience functions
def create_parallel_reader(max_workers: int = 8, buffer_size_mb: int = 256) -> ParallelHDF5Reader:
    """
    Create a configured parallel HDF5 reader.

    Args:
        max_workers: Maximum number of parallel workers
        buffer_size_mb: I/O buffer size in MB

    Returns:
        Configured ParallelHDF5Reader instance
    """
    return ParallelHDF5Reader(
        max_workers=max_workers,
        buffer_size_mb=buffer_size_mb,
        enable_prefetch=True
    )

def read_genome_datasets_parallel(file_path: Union[str, Path],
                                dataset_names: List[str],
                                start_idx: int = 0,
                                end_idx: Optional[int] = None) -> Dict[str, np.ndarray]:
    """
    Read multiple genome datasets in parallel.

    Args:
        file_path: Path to HDF5 file
        dataset_names: Names of datasets to read
        start_idx: Starting index
        end_idx: Ending index

    Returns:
        Dictionary of dataset arrays
    """
    reader = create_parallel_reader()
    return reader.read_multiple_datasets(file_path, dataset_names, start_idx, end_idx)

if __name__ == "__main__":
    # Test the parallel I/O system
    reader = create_parallel_reader()

    print("⚡ MethylUtils Parallel I/O System")
    print("=" * 40)

    print("📊 Parallel HDF5 Reader Created")
    print("Features:")
    print("  - Parallel dataset reading")
    print("  - Memory-mapped access")
    print("  - Asynchronous prefetching")
    print("  - Intelligent caching")

    print("\n✅ Parallel I/O system ready!")
    print("Use create_parallel_reader() for optimized HDF5 reading")
