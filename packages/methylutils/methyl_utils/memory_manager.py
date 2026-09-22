#!/usr/bin/env python3
"""
Memory Manager for MethylUtils - Large-scale Genomic Data Processing

This module provides advanced memory management capabilities for processing
large genomic datasets (millions to billions of positions) efficiently.

Key Features:
- Memory-mapped file support for HDF5 files
- Intelligent chunking strategies
- GPU memory pooling and optimization
- Automatic memory cleanup and monitoring
- Memory-efficient data structures for genomic data

Optimized for NVIDIA GH200 with 96GB GPU memory and 400GB system RAM.
"""

import os
import gc
import mmap
import psutil
import logging
from typing import Dict, Tuple, Any, Union
from pathlib import Path
from contextlib import contextmanager
from functools import wraps
import numpy as np
import time

# Container-specific imports (with fallbacks)
try:
    import h5py
    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False

try:
    from .gpu_detection import is_gpu_available, get_cupy, cleanup_gpu_memory
    cp = get_cupy()  # Get CuPy instance
    CUPY_AVAILABLE = True
except ImportError:
    cp = None
    CUPY_AVAILABLE = False

logger = logging.getLogger(__name__)

class MemoryManager:
    """
    Advanced memory manager for genomic data processing.

    Handles memory allocation, cleanup, and optimization for processing
    large-scale genomic datasets on NVIDIA GH200 hardware.
    """

    def __init__(self,
                 gpu_memory_limit_gb: float = 80.0,  # 80% of 96GB
                 system_memory_limit_gb: float = 350.0,  # 350GB of 400GB
                 chunk_size_positions: int = 10_000_000):
        """
        Initialize memory manager.

        Args:
            gpu_memory_limit_gb: GPU memory limit in GB
            system_memory_limit_gb: System memory limit in GB
            chunk_size_positions: Default chunk size for position processing
        """
        self.gpu_memory_limit_gb = gpu_memory_limit_gb
        self.system_memory_limit_gb = system_memory_limit_gb
        self.chunk_size_positions = chunk_size_positions

        # Memory tracking
        self.memory_stats = {
            'gpu_allocated': 0.0,
            'gpu_peak': 0.0,
            'system_allocated': 0.0,
            'system_peak': 0.0,
            'chunks_processed': 0,
            'files_mapped': 0
        }

        # GPU memory pool
        self.gpu_pool = None
        if CUPY_AVAILABLE and is_gpu_available():
            try:
                cp = get_cupy()
                if cp:
                    # Initialize GPU memory pool
                    self.gpu_pool = cp.get_default_memory_pool()
                    logger.info("GPU memory pool initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize GPU memory pool: {e}")

        logger.info("MemoryManager initialized")
        logger.info(f"GPU memory limit: {gpu_memory_limit_gb}GB")
        logger.info(f"System memory limit: {system_memory_limit_gb}GB")
        logger.info(f"Chunk size: {chunk_size_positions:,} positions")

    def get_memory_usage(self) -> Dict[str, float]:
        """Get current memory usage statistics."""
        usage = {}

        # System memory
        process = psutil.Process(os.getpid())
        system_mb = process.memory_info().rss / (1024**2)
        usage['system_memory_mb'] = system_mb
        usage['system_memory_percent'] = (system_mb / (self.system_memory_limit_gb * 1024)) * 100

        # GPU memory
        if CUPY_AVAILABLE and is_gpu_available():
            try:
                cp = get_cupy()
                if cp:
                    gpu_info = cp.cuda.runtime.memGetInfo()
                    gpu_used = (gpu_info[1] - gpu_info[0]) / (1024**3)
                    usage['gpu_memory_gb'] = gpu_used
                    usage['gpu_memory_percent'] = (gpu_used / self.gpu_memory_limit_gb) * 100
                    usage['gpu_free_gb'] = gpu_info[0] / (1024**3)
            except Exception as e:
                logger.warning(f"Failed to get GPU memory info: {e}")
                usage['gpu_memory_gb'] = 0.0
                usage['gpu_memory_percent'] = 0.0
        else:
            usage['gpu_memory_gb'] = 0.0
            usage['gpu_memory_percent'] = 0.0

        return usage

    def check_memory_limits(self) -> bool:
        """Check if current memory usage is within limits."""
        usage = self.get_memory_usage()

        system_ok = usage['system_memory_percent'] < 90.0  # 90% limit
        gpu_ok = usage['gpu_memory_percent'] < 85.0       # 85% limit

        if not system_ok:
            logger.warning(f"System memory usage too high: {usage['system_memory_percent']:.1f}%")
        if not gpu_ok:
            logger.warning(f"GPU memory usage too high: {usage['gpu_memory_percent']:.1f}%")

        return system_ok and gpu_ok

    @contextmanager
    def memory_mapped_hdf5(
        self,
        file_path: Union[str, Path],
        group_path: str = "methylation_data"
    ):
        """
        Context manager for TRUE memory-mapped HDF5 file access.

        This method provides TRUE memory mapping using h5py's lazy loading capabilities:
        - Data is ONLY loaded into RAM when actually accessed (lazy loading)
        - Supports datasets larger than available system memory
        - OS manages memory paging automatically
        - Enables processing of massive genomic datasets efficiently

        Memory mapping benefits:
        ✅ Process datasets larger than RAM
        ✅ Reduced initial memory usage
        ✅ Automatic OS memory management
        ✅ Faster startup times
        ✅ Efficient for random access patterns

        Handles MethylUtils HDF5 file structure where methylation_data is a group
        containing individual datasets (pos, mC, uC, etc.)

        Args:
            file_path: Path to HDF5 file
            group_path: Path to data group within HDF5 file (default: "methylation_data")

        Yields:
            Dictionary containing truly memory-mapped h5py datasets (lazy-loaded)
        """
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 dependencies not available")

        file_path = Path(file_path)
        mapped_data = {}

        try:
            # Open HDF5 file
            f = h5py.File(file_path, 'r')
            data_group = f[group_path]

            # TRUE MEMORY MAPPING: Use h5py's lazy loading without loading all data
            # This provides true memory mapping - data is only loaded when accessed
            n_positions = data_group['pos'].shape[0]

            logger.info(f"Setting up TRUE memory mapping for {n_positions:,} positions")
            logger.info("Data will be loaded lazily - only when accessed")

            # Create lazy-loaded dataset objects (TRUE memory mapping)
            # These only load data into memory when actually accessed
            mapped_data = {
                'pos': data_group['pos'],  # Lazy-loaded, true memory mapping
                'mC': data_group['mC'],   # Lazy-loaded, true memory mapping
                'uC': data_group['uC'],   # Lazy-loaded, true memory mapping
                'tnc': data_group['tnc']  # Lazy-loaded, true memory mapping
            }

            # Add optional datasets if present (also lazy-loaded)
            if 'N' in data_group:
                mapped_data['N'] = data_group['N']
            if 'Sx' in data_group:
                mapped_data['Sx'] = data_group['Sx']
            if 'Sx2' in data_group:
                mapped_data['Sx2'] = data_group['Sx2']
            # log_x_sum, log_1_minus_x_sum and BB columns in file are ignored (single centroid: N, Sx, Sx2 only)

            # Keep file handle alive for memory mapping
            mapped_data['_file_handle'] = f
            mapped_data['_is_memory_mapped'] = True
            mapped_data['_n_positions'] = n_positions

            self.memory_stats['files_mapped'] += 1

            file_size = file_path.stat().st_size
            logger.info(f"✅ TRUE memory mapped HDF5 file: {file_path.name}")
            logger.info(f"   File size: {file_size / (1024**3):.2f}GB")
            logger.info(f"   Positions: {n_positions:,}")
            logger.info("   Memory mapping: ENABLED (lazy loading)")
            logger.info(f"   Fields available: {list(k for k in mapped_data.keys() if not k.startswith('_'))}")
            logger.info("   Memory usage: Minimal (data loaded on-demand)")

            yield mapped_data

        except Exception as e:
            logger.error(f"Failed to memory map {file_path}: {e}")
            logger.error(f"Error details: {type(e).__name__}: {str(e)}")

            # Fallback to direct loading - WARNING: This loads ALL data into RAM
            try:
                logger.warning("🔄 FALLBACK: Loading ALL data into RAM (NOT memory mapped)")
                logger.warning("   This may cause memory issues with large files")

                with h5py.File(file_path, 'r') as f:
                    data_group = f[group_path]

                    # Direct load all data with optimal dtypes (loads everything into RAM)
                    mapped_data = {
                        'pos': np.asarray(data_group['pos'][:], dtype=np.uint32),  # Genomic positions
                        'mC': np.asarray(data_group['mC'][:], dtype=np.uint32),   # Large counts
                        'uC': np.asarray(data_group['uC'][:], dtype=np.uint32),   # Large counts
                        'tnc': np.asarray(data_group['tnc'][:], dtype=np.uint8)   # Small counts (0-255)
                    }

                    # Add optional datasets with optimal dtypes
                    if 'N' in data_group:
                        mapped_data['N'] = np.asarray(data_group['N'][:], dtype=np.uint32)
                    if 'Sx' in data_group:
                        mapped_data['Sx'] = np.asarray(data_group['Sx'][:], dtype=np.float32)
                    if 'Sx2' in data_group:
                        mapped_data['Sx2'] = np.asarray(data_group['Sx2'][:], dtype=np.float32)

                    # Mark as not memory mapped
                    mapped_data['_is_memory_mapped'] = False
                    n_positions = len(mapped_data['pos'])
                    mapped_data['_n_positions'] = n_positions

                    total_memory = sum(arr.nbytes for arr in mapped_data.values() if hasattr(arr, 'nbytes'))
                    logger.warning(f"⚠️  Loaded {n_positions:,} positions into RAM")
                    logger.warning(f"   Memory used: {total_memory / (1024**3):.2f}GB")
                    logger.warning("   Memory mapping: DISABLED (all data in RAM)")

                yield mapped_data

            except Exception as fallback_error:
                logger.error(f"Fallback loading also failed: {fallback_error}")
                raise

        finally:
            # Cleanup file handles
            if '_file_handle' in mapped_data:
                try:
                    mapped_data['_file_handle'].close()
                except:
                    pass
            # Don't delete the data arrays as they're still being used by the caller
            gc.collect()

    def calculate_optimal_chunk_size(
        self,
        total_positions: int,
        data_structure: str = "centroid",
        maximize_gpu_usage: bool = True
    ) -> int:
        """
        Calculate optimal chunk size based on available memory.

        For genome-scale processing, we want to maximize GPU usage (up to 100%)
        since each program gets dedicated GPU resources in queue systems.

        Args:
            total_positions: Total number of positions in dataset
            data_structure: Type of data structure to calculate for
            maximize_gpu_usage: If True, use up to 100% of GPU memory for maximum performance

        Returns:
            Optimal chunk size in positions
        """
        # Use the same memory calculation logic as container_optimization
        memory_req = self._calculate_memory_requirements(total_positions, data_structure)

        # Calculate available memory - prioritize GPU for performance
        gpu_available_mb = self.gpu_memory_limit_gb * 1024
        system_available_mb = self.system_memory_limit_gb * 1024

        # For GPU-accelerated processing, maximize GPU usage
        if maximize_gpu_usage and self.gpu_memory_limit_gb > 0:
            # Use up to 95% of GPU memory (leave 5% for overhead)
            available_mb = gpu_available_mb * 0.95
            memory_source = "GPU"
        else:
            # Fallback to system memory with conservative buffer
            available_mb = min(system_available_mb * 0.8, gpu_available_mb * 0.8)
            memory_source = "System/GPU"

        # Calculate how many positions we can fit
        positions_per_mb = total_positions / memory_req["total_with_overhead_mb"]
        optimal_chunk_size = int(available_mb * positions_per_mb)

        # For genome-scale processing, allow much larger chunks
        # GPU can handle hundreds of millions of positions efficiently
        min_chunk = 10_000_000   # 10M minimum for GPU efficiency
        max_chunk = 500_000_000  # 500M maximum (fits in most GPU memory)

        optimal_chunk_size = max(min_chunk, optimal_chunk_size)
        optimal_chunk_size = min(max_chunk, optimal_chunk_size)

        # Adjust for total positions
        optimal_chunk_size = min(optimal_chunk_size, total_positions)

        logger.info(f"Calculated optimal chunk size: {optimal_chunk_size:,} positions")
        logger.info(f"Data structure: {data_structure}")
        logger.info(f"Memory source: {memory_source} ({available_mb:.0f}MB available)")
        logger.info(f"Estimated memory per chunk: {memory_req['total_with_overhead_mb']:.1f}MB")
        logger.info(f"GPU memory utilization: {(memory_req['total_with_overhead_mb'] / gpu_available_mb * 100):.1f}%")

        return optimal_chunk_size

    def _calculate_memory_requirements(
        self, 
        positions: int, 
        data_structure: str
    ) -> Dict[str, Any]:
        """
        Calculate memory requirements for different data structures.
        Uses the same logic as container_optimization.py for consistency.
        """
        # Memory per data type (bytes per position) - matches methyl_frame.py
        memory_per_type = {
            "uint32": 4,   # positions, mC, uC, N
            "uint16": 2,   # compressed mC/uC for centroids (potential optimization)
            "uint8": 1,    # tnc
            "float32": 4,  # Sx, Sx2, methylation levels
            "float64": 8   # high precision calculations (rarely used)
        }

        # Calculate memory requirements using the type dictionary
        if data_structure == "basic_sample":
            # Basic sample: pos(uint32) + mC(uint32) + uC(uint32) + tnc(uint8)
            bytes_per_position = (
                memory_per_type["uint32"] * 3 +  # pos, mC, uC
                memory_per_type["uint8"]         # tnc
            )
        elif data_structure == "centroid":
            # Single centroid type: pos, mC, uC, tnc, N, Sx, Sx2 (no log sums or BB)
            bytes_per_position = (
                memory_per_type["uint32"] * 4 +  # pos, mC, uC, N
                memory_per_type["uint8"] +       # tnc
                memory_per_type["float32"] * 2   # Sx, Sx2
            )
        else:
            raise ValueError(f"Unknown data structure: {data_structure}")

        # Calculate total memory in MB
        total_mb = (positions * bytes_per_position) / (1024**2)
        processing_overhead_mb = total_mb * 0.5  # 50% overhead
        total_with_overhead_mb = total_mb + processing_overhead_mb

        return {
            "data_structure": data_structure,
            "positions": positions,
            "bytes_per_position": bytes_per_position,
            "memory_mb": total_mb,
            "processing_overhead_mb": processing_overhead_mb,
            "total_with_overhead_mb": total_with_overhead_mb,
            "memory_per_type": memory_per_type
        }

    @contextmanager
    def gpu_memory_context(self, expected_size_gb: float = None):
        """
        Context manager for GPU memory allocation.

        Args:
            expected_size_gb: Expected memory usage in GB
        """
        if not CUPY_AVAILABLE or not is_gpu_available():
            yield
            return

        try:
            # Check if we have enough memory
            usage = self.get_memory_usage()
            available_gb = self.gpu_memory_limit_gb - usage['gpu_memory_gb']

            if expected_size_gb and expected_size_gb > available_gb:
                logger.warning(f"Expected GPU memory usage ({expected_size_gb:.2f}GB) exceeds available ({available_gb:.2f}GB)")
                # Force cleanup
                self.force_gpu_cleanup()

            yield

        except Exception as e:
            logger.error(f"GPU memory context error: {e}")
            raise
        finally:
            # Update memory stats
            usage = self.get_memory_usage()
            self.memory_stats['gpu_peak'] = max(self.memory_stats['gpu_peak'], usage['gpu_memory_gb'])
            self.memory_stats['gpu_allocated'] = usage['gpu_memory_gb']

    @contextmanager
    def gpu_operation_context(self, operation_name: str = "gpu_operation", cleanup_threshold: float = 85.0):
        """
        Context manager for GPU operations with automatic cleanup and monitoring.

        This ensures proper GPU resource management for genome-scale processing,
        preventing memory leaks that could affect subsequent operations.

        Args:
            operation_name: Name of the operation for logging and monitoring
            cleanup_threshold: GPU memory threshold percentage for cleanup
        """
        if not CUPY_AVAILABLE or not is_gpu_available():
            yield
            return

        start_time = time.time()
        start_memory = self.get_memory_usage()['gpu_memory_gb']

        try:
            # Pre-operation memory monitoring
            self.monitor_gpu_memory_threshold(cleanup_threshold)

            logger.debug(f"Starting GPU operation: '{operation_name}'")

            yield

        except Exception as e:
            logger.error(f"GPU operation '{operation_name}' failed: {e}")
            # Attempt cleanup even on failure
            self.cleanup_gpu_after_operation(f"{operation_name}_failed")
            raise
        finally:
            # Always cleanup after GPU operation
            end_time = time.time()
            self.cleanup_gpu_after_operation(operation_name)

            # Log operation statistics
            end_memory = self.get_memory_usage()['gpu_memory_gb']
            memory_delta = end_memory - start_memory
            operation_time = end_time - start_time

            logger.debug(f"GPU operation '{operation_name}' completed:")
            logger.debug(f"  Duration: {operation_time:.3f}s")
            logger.debug(f"  Memory delta: {memory_delta:+.2f}GB")
            logger.debug(f"  Final GPU memory: {end_memory:.2f}GB")

    def gpu_operation(self, operation_name: str = None, cleanup_threshold: float = 85.0):
        """
        Decorator for GPU operations that ensures proper resource cleanup.

        Usage:
            @memory_manager.gpu_operation("distance_calculation")
            def compute_distances(data):
                # GPU operations here
                return result

        Args:
            operation_name: Name of the operation (auto-detected if None)
            cleanup_threshold: GPU memory threshold for cleanup
        """
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                name = operation_name or func.__name__

                with self.gpu_operation_context(name, cleanup_threshold):
                    return func(*args, **kwargs)

            return wrapper
        return decorator

    def force_gpu_cleanup(self):
        """Force comprehensive cleanup of GPU memory (CuPy, cuDF/RMM, PyTorch)."""
        try:
            # 1. Collect first so any released Python refs (e.g. builder.release_gpu()) are dropped
            gc.collect()

            if CUPY_AVAILABLE:
                # 2. CuPy: free pools and sync
                cleanup_gpu_memory()
                if self.gpu_pool:
                    self.gpu_pool.free_all_blocks()
                if hasattr(cp, 'cuda'):
                    cp.cuda.Device().synchronize()
                    cp.cuda.runtime.deviceSynchronize()
                if hasattr(cp, 'clear_memo'):
                    cp.clear_memo()

            # 3. PyTorch: clear cached allocator so GPU memory is released
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except ImportError:
                pass
            except Exception as e:
                logger.debug("PyTorch GPU cleanup skipped: %s", e)

            # 4. RMM/cuDF: release pool memory when RAPIDS is used
            try:
                import rmm
                if hasattr(rmm, 'get_current_allocator'):
                    allocator = rmm.get_current_allocator()
                    if hasattr(allocator, 'free_all_blocks'):
                        allocator.free_all_blocks()
            except ImportError:
                pass
            except Exception as e:
                logger.debug("RMM GPU cleanup skipped: %s", e)

            # 5. Final GC so freed GPU allocations are reflected
            gc.collect()

            logger.info("GPU memory cleanup completed")
        except Exception as e:
            logger.warning(f"GPU cleanup failed: {e}")

    def cleanup_gpu_after_operation(self, operation_name: str = "unknown"):
        """
        Comprehensive GPU cleanup after operations to prevent memory leaks.

        Args:
            operation_name: Name of the operation for logging
        """
        if not CUPY_AVAILABLE:
            return

        try:
            start_time = time.time()

            # 1. Force synchronization
            cp.cuda.Device().synchronize()
            cp.cuda.runtime.deviceSynchronize()

            # 2. Clear memory pools
            cp.get_default_memory_pool().free_all_blocks()
            cp.get_default_pinned_memory_pool().free_all_blocks()

            # 3. Force garbage collection
            gc.collect()

            # 4. Clear any cached arrays
            cp.clear_memo()  # Clear CuPy memo cache

            # 5. Update memory statistics
            usage = self.get_memory_usage()
            self.memory_stats['gpu_allocated'] = usage['gpu_memory_gb']
            self.memory_stats['last_cleanup'] = time.time()

            cleanup_time = time.time() - start_time
            logger.debug(f"GPU cleanup after '{operation_name}' completed in {cleanup_time:.3f}s")

        except Exception as e:
            logger.warning(f"GPU cleanup after '{operation_name}' failed: {e}")

    def monitor_gpu_memory_threshold(self, threshold_percent: float = 85.0):
        """
        Monitor GPU memory usage and trigger cleanup if approaching threshold.

        Args:
            threshold_percent: Memory usage threshold percentage (0-100)
        """
        if not CUPY_AVAILABLE:
            return False

        try:
            usage = self.get_memory_usage()
            memory_percent = (usage['gpu_memory_gb'] / self.gpu_memory_limit_gb) * 100

            if memory_percent >= threshold_percent:
                logger.warning(f"GPU memory threshold exceeded: {memory_percent:.1f}% >= {threshold_percent:.1f}%")
                self.cleanup_gpu_after_operation("threshold_monitoring")
                return True

        except Exception as e:
            logger.warning(f"GPU memory monitoring failed: {e}")

        return False

    def optimize_array_dtype(self, array: np.ndarray, target_dtype: str = None) -> np.ndarray:
        """
        Optimize array dtype for memory efficiency.

        Args:
            array: Input array
            target_dtype: Target dtype (auto-detected if None)

        Returns:
            Optimized array
        """
        if target_dtype is None:
            # Auto-detect optimal dtype
            if array.dtype == np.float64:
                # Check if we can use float32
                if np.all(np.abs(array) < 1e6) and np.all(array % 1 == 0):
                    target_dtype = 'float32'
                else:
                    target_dtype = 'float32'  # Still convert for memory savings
            elif array.dtype == np.int64:
                # Check range for smaller integer types
                if np.all(array >= 0) and np.all(array < 2**16):
                    target_dtype = 'uint16'
                elif np.all(array >= -2**15) and np.all(array < 2**15):
                    target_dtype = 'int16'
                else:
                    target_dtype = 'int32'
            else:
                return array  # Already optimal

        # Convert dtype
        optimized = array.astype(target_dtype)
        memory_savings = (array.nbytes - optimized.nbytes) / (1024**2)

        if memory_savings > 1.0:  # Only log if significant savings
            logger.info(f"Optimized array dtype: {array.dtype} -> {target_dtype} "
                       f"(saved {memory_savings:.1f}MB)")

        return optimized

    def create_shared_memory_array(self, shape: Tuple, dtype: str) -> np.ndarray:
        """
        Create array in shared memory for multiprocessing.

        This method creates a numpy array backed by shared memory that can be
        accessed by multiple processes efficiently.

        Args:
            shape: Array shape (tuple of integers)
            dtype: Data type (string or numpy dtype)

        Returns:
            Numpy array backed by shared memory

        Raises:
            ImportError: If multiprocessing.shared_memory is not available
            ValueError: If shape or dtype are invalid
        """
        try:
            import multiprocessing.shared_memory as shared_memory
            import uuid
        except ImportError:
            logger.warning("multiprocessing.shared_memory not available, falling back to regular array")
            logger.warning("Shared memory functionality will not work across processes")

            # Create regular array with consistent interface
            regular_array = np.zeros(shape, dtype=dtype)

            class RegularArray:
                def __init__(self, array):
                    self._array = array
                    self._is_shared_memory = False

                def __array__(self):
                    return self._array

                def __getitem__(self, key):
                    return self._array[key]

                def __setitem__(self, key, value):
                    self._array[key] = value

                def __len__(self):
                    return len(self._array)

                @property
                def shape(self):
                    return self._array.shape

                @property
                def dtype(self):
                    return self._array.dtype

                @property
                def ndim(self):
                    return self._array.ndim

                def __getattr__(self, name):
                    return getattr(self._array, name)

            return RegularArray(regular_array)

        try:
            # Convert string dtype to numpy dtype if needed
            if isinstance(dtype, str):
                np_dtype = np.dtype(dtype)
            else:
                np_dtype = dtype

            # Calculate total size in bytes
            total_elements = np.prod(shape)
            element_size = np_dtype.itemsize
            total_bytes = total_elements * element_size

            if total_bytes == 0:
                raise ValueError("Cannot create shared memory array with 0 bytes")

            # Create unique name for shared memory block
            shm_name = f"methylutils_{uuid.uuid4().hex}"

            # Create shared memory block
            shm = shared_memory.SharedMemory(create=True, size=total_bytes, name=shm_name)

            # Create numpy array from shared memory
            shared_array = np.ndarray(shape, dtype=np_dtype, buffer=shm.buf)

            # Store shared memory reference to prevent garbage collection
            # Use a custom class to hold the array and shared memory reference
            class SharedMemoryArray:
                def __init__(self, array, shm, shm_name):
                    self._array = array
                    self._shared_memory = shm
                    self._shm_name = shm_name
                    self._is_shared_memory = True

                def __array__(self):
                    return self._array

                def __getitem__(self, key):
                    return self._array[key]

                def __setitem__(self, key, value):
                    self._array[key] = value

                def __len__(self):
                    return len(self._array)

                @property
                def shape(self):
                    return self._array.shape

                @property
                def dtype(self):
                    return self._array.dtype

                @property
                def ndim(self):
                    return self._array.ndim

                def __getattr__(self, name):
                    # Delegate other attributes to the underlying array
                    return getattr(self._array, name)

            shared_array = SharedMemoryArray(shared_array, shm, shm_name)

            logger.debug(f"✅ Created shared memory array: shape={shape}, dtype={dtype}, size={total_bytes/1024/1024:.1f}MB")
            return shared_array

        except Exception as e:
            logger.warning(f"Failed to create shared memory array: {e}, falling back to regular array")

            # Create regular array wrapped in the same interface for consistency
            regular_array = np.zeros(shape, dtype=dtype)

            class RegularArray:
                def __init__(self, array):
                    self._array = array
                    self._is_shared_memory = False

                def __array__(self):
                    return self._array

                def __getitem__(self, key):
                    return self._array[key]

                def __setitem__(self, key, value):
                    self._array[key] = value

                def __len__(self):
                    return len(self._array)

                @property
                def shape(self):
                    return self._array.shape

                @property
                def dtype(self):
                    return self._array.dtype

                @property
                def ndim(self):
                    return self._array.ndim

                def __getattr__(self, name):
                    return getattr(self._array, name)

            return RegularArray(regular_array)

    def attach_shared_memory_array(self, shm_name: str, shape: Tuple, dtype: str) -> np.ndarray:
        """
        Attach to an existing shared memory array (for use by child processes).

        This method is used by child processes to attach to shared memory arrays
        created by the parent process.

        Args:
            shm_name: Name of the shared memory block
            shape: Array shape
            dtype: Data type

        Returns:
            Numpy array backed by the existing shared memory block

        Raises:
            ImportError: If multiprocessing.shared_memory is not available
            FileNotFoundError: If the shared memory block doesn't exist
        """
        import multiprocessing.shared_memory as shared_memory

        try:
            # Convert string dtype to numpy dtype if needed
            if isinstance(dtype, str):
                np_dtype = np.dtype(dtype)
            else:
                np_dtype = dtype

            # Attach to existing shared memory block
            shm = shared_memory.SharedMemory(name=shm_name)

            # Create numpy array from shared memory
            array = np.ndarray(shape, dtype=np_dtype, buffer=shm.buf)

            # Wrap in SharedMemoryArray for consistent interface
            class SharedMemoryArray:
                def __init__(self, array, shm, shm_name):
                    self._array = array
                    self._shared_memory = shm
                    self._shm_name = shm_name
                    self._is_shared_memory = True

                def __array__(self):
                    return self._array

                def __getitem__(self, key):
                    return self._array[key]

                def __setitem__(self, key, value):
                    self._array[key] = value

                def __len__(self):
                    return len(self._array)

                @property
                def shape(self):
                    return self._array.shape

                @property
                def dtype(self):
                    return self._array.dtype

                @property
                def ndim(self):
                    return self._array.ndim

                def __getattr__(self, name):
                    return getattr(self._array, name)

            shared_array = SharedMemoryArray(array, shm, shm_name)

            logger.debug(f"Attached to shared memory array: {shm_name}, shape={shape}, dtype={dtype}")
            return shared_array

        except ImportError:
            raise ImportError("multiprocessing.shared_memory not available")
        except FileNotFoundError:
            raise FileNotFoundError(f"Shared memory block '{shm_name}' not found")
        except Exception as e:
            logger.error(f"Failed to attach to shared memory array: {e}")
            raise

    def cleanup_shared_memory_array(self, array: np.ndarray):
        """
        Clean up a shared memory array and release its resources.

        This method should be called when you're done with a shared memory array
        to prevent memory leaks.

        Args:
            array: Shared memory array to clean up
        """
        try:
            # Only cleanup if it's actually a shared memory array
            if getattr(array, '_is_shared_memory', False) and hasattr(array, '_shared_memory'):
                shm = array._shared_memory
                shm_name = getattr(array, '_shm_name', 'unknown')

                # Close and unlink the shared memory block
                shm.close()
                try:
                    shm.unlink()  # Only the creator should unlink
                except Exception:
                    pass  # Ignore if already unlinked

                logger.debug(f"✅ Cleaned up shared memory array: {shm_name}")

                # Remove references
                delattr(array, '_shared_memory')
                delattr(array, '_shm_name')
                delattr(array, '_is_shared_memory')

            else:
                logger.debug("Array is not a shared memory array, nothing to cleanup")

        except Exception as e:
            logger.warning(f"Failed to cleanup shared memory array: {e}")

    def is_shared_memory_array(self, array: np.ndarray) -> bool:
        """
        Check if an array is backed by shared memory.

        Args:
            array: Array to check

        Returns:
            True if the array is backed by shared memory
        """
        return getattr(array, '_is_shared_memory', False)

    def monitor_performance(self, operation_name: str):
        """
        Decorator/context manager for performance monitoring.

        Args:
            operation_name: Name of the operation to monitor
        """
        class PerformanceMonitor:
            def __init__(self, name):
                self.name = name
                self.start_time = None
                self.start_memory = None

            def __enter__(self):
                self.start_time = time.time()
                self.start_memory = self.get_memory_usage()
                logger.debug(f"Started monitoring: {self.name}")
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                end_time = time.time()
                end_memory = self.get_memory_usage()

                duration = end_time - self.start_time
                memory_delta = end_memory['system_memory_mb'] - self.start_memory['system_memory_mb']

                logger.info(f"Performance: {self.name}")
                logger.info(f"  Duration: {duration:.3f}s")
                logger.info(f"  Memory delta: {memory_delta:+.1f}MB")
                if 'gpu_memory_gb' in end_memory:
                    gpu_delta = end_memory['gpu_memory_gb'] - self.start_memory['gpu_memory_gb']
                    logger.info(f"  GPU memory delta: {gpu_delta:+.2f}GB")

        return PerformanceMonitor(operation_name)

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get comprehensive memory statistics."""
        stats = self.memory_stats.copy()
        current_usage = self.get_memory_usage()
        stats.update(current_usage)
        return stats

    def reset_stats(self):
        """Reset memory statistics."""
        self.memory_stats = {
            'gpu_allocated': 0.0,
            'gpu_peak': 0.0,
            'system_allocated': 0.0,
            'system_peak': 0.0,
            'chunks_processed': 0,
            'files_mapped': 0
        }
        logger.info("Memory statistics reset")

# Global memory manager instance
_memory_manager = None

def get_memory_manager() -> MemoryManager:
    """Get global memory manager instance."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager

def memory_usage_monitor(func):
    """Decorator to monitor memory usage of functions."""
    def wrapper(*args, **kwargs):
        manager = get_memory_manager()
        with manager.monitor_performance(func.__name__):
            return func(*args, **kwargs)
    return wrapper

# Convenience functions
def check_memory_limits() -> bool:
    """Check if memory usage is within limits."""
    return get_memory_manager().check_memory_limits()

def get_memory_usage() -> Dict[str, float]:
    """Get current memory usage."""
    return get_memory_manager().get_memory_usage()

def force_gpu_cleanup():
    """Force GPU memory cleanup."""
    get_memory_manager().force_gpu_cleanup()

if __name__ == "__main__":
    # Test the memory manager
    manager = get_memory_manager()

    print("🧠 MethylUtils Memory Manager")
    print("=" * 40)

    # Test memory usage monitoring
    usage = manager.get_memory_usage()
    print(f"System Memory: {usage['system_memory_mb']:.1f}MB "
          f"({usage['system_memory_percent']:.1f}%)")

    if 'gpu_memory_gb' in usage:
        print(f"GPU Memory: {usage['gpu_memory_gb']:.2f}GB "
              f"({usage['gpu_memory_percent']:.1f}%)")

    # Test memory limits
    within_limits = manager.check_memory_limits()
    print(f"Within Memory Limits: {'✅' if within_limits else '❌'}")

    print("\n✅ Memory Manager ready for genome-scale processing!")


# Standalone convenience functions for shared memory arrays
def create_shared_memory_array(shape, dtype=np.float32):
    """
    Create a shared memory array.

    Args:
        shape: Shape of the array
        dtype: Data type

    Returns:
        SharedMemoryArray or RegularArray instance
    """
    manager = get_memory_manager()
    return manager.create_shared_memory_array(shape, dtype)


def attach_shared_memory_array(name):
    """
    Attach to an existing shared memory array.

    Args:
        name: Name of the shared memory block

    Returns:
        SharedMemoryArray instance
    """
    manager = get_memory_manager()
    return manager.attach_shared_memory_array(name)


def cleanup_shared_memory_array(array):
    """
    Clean up a shared memory array.

    Args:
        array: SharedMemoryArray instance to clean up
    """
    manager = get_memory_manager()
    return manager.cleanup_shared_memory_array(array)


def is_shared_memory_array(array):
    """
    Check if an array is backed by shared memory.

    Args:
        array: Array to check

    Returns:
        True if backed by shared memory, False otherwise
    """
    manager = get_memory_manager()
    return manager.is_shared_memory_array(array)
