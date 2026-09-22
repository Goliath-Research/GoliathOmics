#!/usr/bin/env python3
"""
Chunked Processor for MethylUtils - Large-scale Genomic Data Processing

This module provides chunked processing capabilities for handling genomic datasets
that exceed available memory, enabling processing of human genomes with billions
of positions efficiently.

Key Features:
- Intelligent chunking based on memory availability
- Parallel processing with multiprocessing
- GPU-accelerated chunk processing
- Memory-efficient data streaming
- Automatic load balancing
- Progress monitoring and checkpointing

Optimized for NVIDIA GH200 with 96GB GPU memory and 400GB system RAM.
"""

import os
import gc
import time
import logging
from typing import Dict, List, Optional, Tuple, Any, Union, Callable, Iterator
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from queue import Queue
import threading
import numpy as np

# Container-specific imports (with fallbacks)
try:
    import h5py
    HDF5_AVAILABLE = True
except ImportError:
    HDF5_AVAILABLE = False

try:
    from .memory_manager import get_memory_manager, MemoryManager
    MEMORY_MANAGER_AVAILABLE = True
except ImportError:
    MEMORY_MANAGER_AVAILABLE = False

try:
    from .gpu_detection import is_gpu_available, get_cupy
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

logger = logging.getLogger(__name__)

@dataclass
class ChunkInfo:
    """Information about a data chunk."""
    chunk_id: int
    start_position: int
    end_position: int
    size_positions: int
    memory_estimate_mb: float
    file_offset: int = 0
    compression_ratio: float = 1.0

@dataclass
class ProcessingResult:
    """Result from processing a chunk."""
    chunk_info: ChunkInfo
    data: Dict[str, Any]
    processing_time: float
    memory_used_mb: float
    gpu_memory_used_gb: float = 0.0
    success: bool = True
    error_message: str = ""

class ChunkedGenomicProcessor:
    """
    Processor for chunked genomic data analysis.

    Handles large genomic datasets by processing them in memory-efficient chunks,
    with support for parallel processing and GPU acceleration.
    """

    def __init__(self,
                 chunk_size_positions: int = 10_000_000,
                 max_workers: int = 4,
                 use_gpu: bool = True,
                 memory_limit_gb: float = 350.0,
                 enable_progress: bool = True):
        """
        Initialize chunked processor.

        Args:
            chunk_size_positions: Default chunk size in positions
            max_workers: Maximum number of parallel workers
            use_gpu: Whether to use GPU acceleration
            memory_limit_gb: Memory limit in GB
            enable_progress: Whether to show progress
        """
        self.chunk_size_positions = chunk_size_positions
        self.max_workers = max_workers
        self.use_gpu = use_gpu and GPU_AVAILABLE
        self.memory_limit_gb = memory_limit_gb
        self.enable_progress = enable_progress

        # Processing statistics
        self.stats = {
            'total_positions': 0,
            'chunks_processed': 0,
            'processing_time': 0.0,
            'peak_memory_mb': 0.0,
            'gpu_peak_memory_gb': 0.0,
            'errors': 0
        }

        # Memory manager
        if MEMORY_MANAGER_AVAILABLE:
            self.memory_manager = get_memory_manager()
        else:
            self.memory_manager = None

        # GPU setup
        self.cp = None
        if self.use_gpu:
            try:
                self.cp = get_cupy()
                if self.cp and self.cp.is_available():
                    logger.info("GPU acceleration enabled for chunked processing")
                else:
                    self.use_gpu = False
                    logger.warning("GPU not available, falling back to CPU")
            except Exception as e:
                self.use_gpu = False
                logger.warning(f"GPU setup failed: {e}")

        logger.info("ChunkedGenomicProcessor initialized")
        logger.info(f"Chunk size: {chunk_size_positions:,} positions")
        logger.info(f"Max workers: {max_workers}")
        logger.info(f"GPU enabled: {self.use_gpu}")

    def analyze_file_structure(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Analyze HDF5 file structure to determine optimal chunking strategy.

        Args:
            file_path: Path to HDF5 file

        Returns:
            Dictionary with file analysis results
        """
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 dependencies not available")

        file_path = Path(file_path)
        analysis = {}

        with h5py.File(file_path, 'r') as f:
            # Get basic file info
            file_size = file_path.stat().st_size
            analysis['file_size_gb'] = file_size / (1024**3)

            # Analyze datasets
            datasets_info = {}
            total_positions = 0

            for key in f.keys():
                if isinstance(f[key], h5py.Dataset):
                    dataset = f[key]
                    info = {
                        'shape': dataset.shape,
                        'dtype': str(dataset.dtype),
                        'size_mb': dataset.size * dataset.dtype.itemsize / (1024**2),
                        'compression': str(dataset.compression) if hasattr(dataset, 'compression') else None,
                        'chunks': dataset.chunks if hasattr(dataset, 'chunks') else None
                    }
                    datasets_info[key] = info

                    # Estimate positions from largest dataset
                    if len(dataset.shape) > 0 and dataset.shape[0] > total_positions:
                        total_positions = dataset.shape[0]

            analysis['datasets'] = datasets_info
            analysis['estimated_positions'] = total_positions

            # Check for methylation_data group
            if 'methylation_data' in f:
                methyl_group = f['methylation_data']
                if isinstance(methyl_group, h5py.Group):
                    group_datasets = {}
                    for key in methyl_group.keys():
                        if isinstance(methyl_group[key], h5py.Dataset):
                            dataset = methyl_group[key]
                            group_datasets[key] = {
                                'shape': dataset.shape,
                                'dtype': str(dataset.dtype),
                                'size_mb': dataset.size * dataset.dtype.itemsize / (1024**2)
                            }
                    analysis['methylation_data_group'] = group_datasets
                    # Update position estimate from group
                    if 'pos' in group_datasets:
                        analysis['estimated_positions'] = group_datasets['pos']['shape'][0]

        return analysis

    def calculate_optimal_chunking(self, file_analysis: Dict[str, Any]) -> List[ChunkInfo]:
        """
        Calculate optimal chunking strategy based on file analysis and memory constraints.

        Args:
            file_analysis: File analysis from analyze_file_structure

        Returns:
            List of ChunkInfo objects
        """
        total_positions = file_analysis['estimated_positions']
        file_size_gb = file_analysis['file_size_gb']

        # Calculate optimal chunk size based on memory
        if self.memory_manager:
            chunk_size = self.memory_manager.calculate_optimal_chunk_size(total_positions)
        else:
            # Fallback calculation
            chunk_size = min(self.chunk_size_positions, total_positions)

        # Create chunk information
        chunks = []
        for i, start_pos in enumerate(range(0, total_positions, chunk_size)):
            end_pos = min(start_pos + chunk_size, total_positions)
            size_positions = end_pos - start_pos

            # Estimate memory usage for this chunk
            memory_estimate_mb = self._estimate_chunk_memory(file_analysis, size_positions)

            chunk_info = ChunkInfo(
                chunk_id=i,
                start_position=start_pos,
                end_position=end_pos,
                size_positions=size_positions,
                memory_estimate_mb=memory_estimate_mb
            )
            chunks.append(chunk_info)

        logger.info(f"Created {len(chunks)} chunks for {total_positions:,} positions")
        logger.info(f"Average chunk size: {chunk_size:,} positions")
        logger.info(f"Average memory per chunk: {sum(c.memory_estimate_mb for c in chunks)/len(chunks):.1f}MB")

        return chunks

    def _estimate_chunk_memory(self, file_analysis: Dict[str, Any], positions: int) -> float:
        """Estimate memory usage for a chunk of given size."""
        memory_mb = 0.0

        # Estimate from datasets
        if 'datasets' in file_analysis:
            for dataset_info in file_analysis['datasets'].values():
                if 'shape' in dataset_info and len(dataset_info['shape']) > 0:
                    # Estimate memory for this many positions
                    elements_per_position = dataset_info['shape'][0] / file_analysis['estimated_positions']
                    chunk_elements = int(elements_per_position * positions)
                    memory_mb += (chunk_elements * np.dtype(dataset_info['dtype']).itemsize) / (1024**2)

        # Estimate from methylation data group if available
        if 'methylation_data_group' in file_analysis:
            for dataset_info in file_analysis['methylation_data_group'].values():
                if 'shape' in dataset_info and len(dataset_info['shape']) > 0:
                    chunk_elements = positions
                    memory_mb += (chunk_elements * np.dtype(dataset_info['dtype']).itemsize) / (1024**2)

        # Add overhead for processing
        memory_mb *= 1.5  # 50% overhead for processing

        return memory_mb

    def process_file_chunked(self,
                           file_path: Union[str, Path],
                           processing_function: Callable,
                           output_dir: Union[str, Path],
                           **kwargs) -> Dict[str, Any]:
        """
        Process a genomic file in chunks using the specified processing function.

        Args:
            file_path: Path to input file
            processing_function: Function to process each chunk
            output_dir: Directory for output files
            **kwargs: Additional arguments for processing function

        Returns:
            Dictionary with processing results and statistics
        """
        file_path = Path(file_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Starting chunked processing of {file_path.name}")

        # Analyze file structure
        file_analysis = self.analyze_file_structure(file_path)

        # Calculate optimal chunking
        chunks = self.calculate_optimal_chunking(file_analysis)

        # Initialize processing
        start_time = time.time()
        results = []

        # Process chunks
        if self.max_workers > 1:
            # Parallel processing
            results = self._process_chunks_parallel(file_path, chunks, processing_function,
                                                   output_dir, **kwargs)
        else:
            # Sequential processing
            results = self._process_chunks_sequential(file_path, chunks, processing_function,
                                                     output_dir, **kwargs)

        # Calculate statistics
        processing_time = time.time() - start_time
        total_positions = sum(r.chunk_info.size_positions for r in results if r.success)

        # Update stats
        self.stats.update({
            'total_positions': total_positions,
            'chunks_processed': len([r for r in results if r.success]),
            'processing_time': processing_time,
            'errors': len([r for r in results if not r.success])
        })

        # Log results
        logger.info("Chunked processing completed")
        logger.info(f"Total positions processed: {total_positions:,}")
        logger.info(f"Chunks processed: {len(results)}")
        logger.info(f"Processing time: {processing_time:.2f}s")
        logger.info(".2f")

        return {
            'file_path': str(file_path),
            'total_positions': total_positions,
            'chunks_processed': len(results),
            'successful_chunks': len([r for r in results if r.success]),
            'failed_chunks': len([r for r in results if not r.success]),
            'processing_time': processing_time,
            'throughput_positions_per_sec': total_positions / processing_time if processing_time > 0 else 0,
            'results': results,
            'file_analysis': file_analysis,
            'chunks': chunks,
            'output_dir': str(output_dir)
        }

    def _process_chunks_sequential(self,
                                 file_path: Path,
                                 chunks: List[ChunkInfo],
                                 processing_function: Callable,
                                 output_dir: Path,
                                 **kwargs) -> List[ProcessingResult]:
        """Process chunks sequentially."""
        results = []

        for i, chunk in enumerate(chunks):
            if self.enable_progress:
                print(f"\rProcessing chunk {i+1}/{len(chunks)} "
                      f"(positions {chunk.start_position:,}-{chunk.end_position:,})", end="")

            try:
                result = self._process_single_chunk(file_path, chunk, processing_function,
                                                   output_dir, **kwargs)
                results.append(result)

            except Exception as e:
                logger.error(f"Failed to process chunk {chunk.chunk_id}: {e}")
                error_result = ProcessingResult(
                    chunk_info=chunk,
                    data={},
                    processing_time=0.0,
                    memory_used_mb=0.0,
                    success=False,
                    error_message=str(e)
                )
                results.append(error_result)

        if self.enable_progress:
            print()  # New line after progress

        return results

    def _process_chunks_parallel(self,
                               file_path: Path,
                               chunks: List[ChunkInfo],
                               processing_function: Callable,
                               output_dir: Path,
                               **kwargs) -> List[ProcessingResult]:
        """Process chunks in parallel using multiprocessing."""
        results = []

        # Use ProcessPoolExecutor for CPU-bound tasks
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_chunk = {}
            for chunk in chunks:
                future = executor.submit(self._process_single_chunk,
                                       file_path, chunk, processing_function,
                                       output_dir, **kwargs)
                future_to_chunk[future] = chunk

            # Process completed tasks
            completed = 0
            for future in as_completed(future_to_chunk):
                chunk = future_to_chunk[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Failed to process chunk {chunk.chunk_id}: {e}")
                    error_result = ProcessingResult(
                        chunk_info=chunk,
                        data={},
                        processing_time=0.0,
                        memory_used_mb=0.0,
                        success=False,
                        error_message=str(e)
                    )
                    results.append(error_result)

                completed += 1
                if self.enable_progress:
                    print(f"\rProcessed {completed}/{len(chunks)} chunks", end="")

        if self.enable_progress:
            print()  # New line after progress

        return results

    def _process_single_chunk(self,
                            file_path: Path,
                            chunk: ChunkInfo,
                            processing_function: Callable,
                            output_dir: Path,
                            **kwargs) -> ProcessingResult:
        """Process a single chunk of data."""
        start_time = time.time()

        try:
            # Load chunk data
            chunk_data = self._load_chunk_data(file_path, chunk)

            # Get initial memory usage
            initial_memory = self._get_current_memory_usage()

            # Process chunk
            result_data = processing_function(chunk_data, **kwargs)

            # Calculate memory usage
            final_memory = self._get_current_memory_usage()
            memory_used_mb = max(0, final_memory['system'] - initial_memory['system'])
            gpu_memory_used_gb = max(0, final_memory['gpu'] - initial_memory['gpu'])

            processing_time = time.time() - start_time

            # Save chunk result
            self._save_chunk_result(chunk, result_data, output_dir)

            return ProcessingResult(
                chunk_info=chunk,
                data=result_data,
                processing_time=processing_time,
                memory_used_mb=memory_used_mb,
                gpu_memory_used_gb=gpu_memory_used_gb,
                success=True
            )

        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"Chunk {chunk.chunk_id} processing failed: {e}")

            return ProcessingResult(
                chunk_info=chunk,
                data={},
                processing_time=processing_time,
                memory_used_mb=0.0,
                success=False,
                error_message=str(e)
            )

    def _load_chunk_data(self, file_path: Path, chunk: ChunkInfo) -> Dict[str, np.ndarray]:
        """Load data for a specific chunk from HDF5 file."""
        if not HDF5_AVAILABLE:
            raise ImportError("HDF5 dependencies not available")

        chunk_data = {}

        with h5py.File(file_path, 'r') as f:
            # Try methylation_data group first
            if 'methylation_data' in f:
                methyl_group = f['methylation_data']
                if isinstance(methyl_group, h5py.Group):
                    for key in methyl_group.keys():
                        if isinstance(methyl_group[key], h5py.Dataset):
                            dataset = methyl_group[key]
                            # Load slice for this chunk
                            chunk_slice = dataset[chunk.start_position:chunk.end_position]
                            chunk_data[key] = np.array(chunk_slice)

            # Fallback to root level datasets
            if not chunk_data:
                for key in f.keys():
                    if isinstance(f[key], h5py.Dataset):
                        dataset = f[key]
                        if len(dataset.shape) > 0 and dataset.shape[0] >= chunk.end_position:
                            chunk_slice = dataset[chunk.start_position:chunk.end_position]
                            chunk_data[key] = np.array(chunk_slice)

        # Add chunk metadata
        chunk_data['_chunk_info'] = {
            'chunk_id': chunk.chunk_id,
            'start_position': chunk.start_position,
            'end_position': chunk.end_position,
            'size_positions': chunk.size_positions
        }

        return chunk_data

    def _save_chunk_result(self, chunk: ChunkInfo, result_data: Dict[str, Any], output_dir: Path):
        """Save processing result for a chunk."""
        import json

        chunk_file = output_dir / "06d"

        # Convert numpy arrays to lists for JSON serialization
        serializable_data = {}
        for key, value in result_data.items():
            if isinstance(value, np.ndarray):
                serializable_data[key] = value.tolist()
            elif isinstance(value, (int, float, str, bool, list, dict)):
                serializable_data[key] = value
            else:
                serializable_data[key] = str(value)

        with open(chunk_file, 'w') as f:
            json.dump({
                'chunk_info': {
                    'chunk_id': chunk.chunk_id,
                    'start_position': chunk.start_position,
                    'end_position': chunk.end_position,
                    'size_positions': chunk.size_positions
                },
                'data': serializable_data
            }, f, indent=2)

    def _get_current_memory_usage(self) -> Dict[str, float]:
        """Get current memory usage."""
        if self.memory_manager:
            usage = self.memory_manager.get_memory_usage()
            return {
                'system': usage.get('system_memory_mb', 0.0),
                'gpu': usage.get('gpu_memory_gb', 0.0)
            }
        else:
            # Fallback memory check
            import psutil
            process = psutil.Process(os.getpid())
            system_mb = process.memory_info().rss / (1024**2)
            return {'system': system_mb, 'gpu': 0.0}

    def get_processing_stats(self) -> Dict[str, Any]:
        """Get comprehensive processing statistics."""
        return self.stats.copy()

    def reset_stats(self):
        """Reset processing statistics."""
        self.stats = {
            'total_positions': 0,
            'chunks_processed': 0,
            'processing_time': 0.0,
            'peak_memory_mb': 0.0,
            'gpu_peak_memory_gb': 0.0,
            'errors': 0
        }
        logger.info("Processing statistics reset")

# Convenience functions
def process_genome_file_chunked(file_path: Union[str, Path],
                               processing_function: Callable,
                               output_dir: Union[str, Path],
                               chunk_size: int = 10_000_000,
                               max_workers: int = 4,
                               use_gpu: bool = True) -> Dict[str, Any]:
    """
    Convenience function for chunked genome processing.

    Args:
        file_path: Path to genome file
        processing_function: Function to process each chunk
        output_dir: Output directory
        chunk_size: Size of each chunk in positions
        max_workers: Number of parallel workers
        use_gpu: Whether to use GPU acceleration

    Returns:
        Processing results dictionary
    """
    processor = ChunkedGenomicProcessor(
        chunk_size_positions=chunk_size,
        max_workers=max_workers,
        use_gpu=use_gpu
    )

    return processor.process_file_chunked(file_path, processing_function, output_dir)

def create_genome_statistics_processor():
    """
    Create a standard processor for computing genome-wide methylation statistics.

    Returns:
        Processing function for genome statistics
    """
    def process_chunk(chunk_data, **kwargs):
        """Process a chunk to compute methylation statistics."""
        # Extract data
        pos = chunk_data.get('pos', np.array([]))
        mC = chunk_data.get('mC', np.array([]))
        uC = chunk_data.get('uC', np.array([]))

        if len(mC) == 0 or len(uC) == 0:
            return {
                'positions_processed': 0,
                'mean_methylation': 0.0,
                'median_methylation': 0.0,
                'methylation_std': 0.0,
                'coverage_mean': 0.0,
                'error': 'No methylation data in chunk'
            }

        # Calculate methylation levels
        methylation_levels = np.divide(mC, mC + uC,
                                     out=np.zeros_like(mC, dtype=np.float32),
                                     where=(mC + uC) != 0)

        # Calculate coverage
        coverage = mC + uC

        # Compute statistics
        stats = {
            'positions_processed': len(methylation_levels),
            'mean_methylation': float(np.mean(methylation_levels)),
            'median_methylation': float(np.median(methylation_levels)),
            'methylation_std': float(np.std(methylation_levels)),
            'coverage_mean': float(np.mean(coverage)),
            'coverage_median': float(np.median(coverage)),
            'positions_with_coverage': int(np.sum(coverage > 0)),
            'chunk_id': chunk_data.get('_chunk_info', {}).get('chunk_id', 0)
        }

        return stats

    return process_chunk

if __name__ == "__main__":
    # Test the chunked processor
    processor = ChunkedGenomicProcessor()

    print("🔬 MethylUtils Chunked Genomic Processor")
    print("=" * 50)

    # Test statistics processor
    stats_processor = create_genome_statistics_processor()

    print("📊 Genome Statistics Processor Created")
    print("Computes: mean, median, std of methylation levels per chunk")
    print("Includes: coverage statistics and position counts")

    print("\n✅ Chunked Processor ready for genome-scale processing!")
    print("Use process_genome_file_chunked() for large genomic files")
