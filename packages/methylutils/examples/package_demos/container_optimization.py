#!/usr/bin/env python3
"""
Container Optimization for MethylUtils on NVIDIA GH200

This module provides optimized container configurations and resource management
for processing human genomes with millions of positions using NVIDIA GH200 GPUs.

Key Optimizations:
- Memory pooling for 96GB GPU memory
- Multi-threaded CPU processing with 16 vCPU
- Optimized data structures for genomic scale
- Container-specific performance tuning
"""

from typing import Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ContainerOptimizer:
    """
    Container-specific optimizations for NVIDIA GH200 and human genome processing.
    """

    def __init__(self):
        self.gpu_memory_gb = 96
        self.cpu_count = 16
        self.ram_gb = 400

        # Container paths
        self.container_work_dir = Path("/home/ubuntu/Work/cuda")
        self.host_work_dir = Path.home() / "Work" / "cuda"

        # Performance settings
        self.chunk_size_millions = 10  # Process 10M positions per chunk
        self.memory_pool_fraction = 0.8  # Use 80% of GPU memory
        self.cpu_thread_pool = self.cpu_count // 2  # 8 threads for I/O

    def get_container_config(self) -> Dict[str, Any]:
        """Get Docker/Podman container configuration for optimal performance."""

        config = {
            "image": "methylutils:cuda-optimized",
            "runtime": "nvidia",
            "gpus": "all",
            "shm_size": "256gb",  # Shared memory for multiprocessing
            "memory": "350gb",   # Leave 50GB for system
            "cpus": "16",
            "cpuset_cpus": "0-15",  # Pin to specific CPUs
            "volumes": [
                f"{self.host_work_dir}:/workspace:rw",
                "/tmp:/tmp:rw"
            ],
            "environment": self.get_environment_variables(),
            "ulimits": {
                "memlock": -1,  # Unlimited locked memory
                "stack": 67108864  # 64MB stack
            },
            "security_opt": ["seccomp=unconfined"],
            "cap_add": ["SYS_PTRACE"]
        }

        return config

    def get_environment_variables(self) -> Dict[str, str]:
        """Get optimized environment variables for container."""

        return {
            # CUDA optimizations
            "CUDA_VISIBLE_DEVICES": "0",
            "CUDA_MPS_PIPE_DIRECTORY": "/tmp/nvidia-mps",
            "CUDA_MPS_LOG_DIRECTORY": "/tmp/nvidia-log",

            # Memory optimizations
            "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512",
            "OMP_NUM_THREADS": "8",
            "MKL_NUM_THREADS": "8",
            "NUMEXPR_NUM_THREADS": "8",

            # Python optimizations
            "PYTHONUNBUFFERED": "1",
            "PYTHONHASHSEED": "0",

            # HDF5 optimizations
            "HDF5_USE_FILE_LOCKING": "FALSE",
            "HDF5_MPI_OPT_TYPES": "TRUE",

            # Performance monitoring
            "METHYLUTILS_PROFILE": "TRUE",
            "METHYLUTILS_CHUNK_SIZE": str(self.chunk_size_millions * 1000000)
        }

    def optimize_for_genome_scale(self) -> Dict[str, Any]:
        """
        Get optimization settings for processing human genomes (3+ billion positions).
        """

        # Human genome statistics
        total_positions = 3_000_000_000  # ~3 billion positions
        chunk_size = self.chunk_size_millions * 1_000_000

        optimization = {
            "total_positions": total_positions,
            "chunk_size": chunk_size,
            "num_chunks": total_positions // chunk_size,
            "memory_per_chunk_gb": self.calculate_memory_per_chunk(),
            "processing_strategy": self.get_processing_strategy(),
            "gpu_memory_pool_mb": int(self.gpu_memory_gb * 1024 * self.memory_pool_fraction),
            "gpu_memory_max_usage_mb": int(self.gpu_memory_gb * 1024 * 0.95),  # 95% for max performance
            "cpu_thread_pools": {
                "io_threads": self.cpu_thread_pool,
                "compute_threads": self.cpu_count - self.cpu_thread_pool
            }
        }

        return optimization

    def get_memory_requirements(self, positions: int = None, data_structure: str = "centroid") -> Dict[str, Any]:
        """
        Calculate detailed memory requirements for different data structures.

        Args:
            positions: Number of positions (uses chunk_size if None)
            data_structure: Type of data structure ("basic_sample", "centroid")

        Returns:
            Dictionary with detailed memory breakdown
        """
        if positions is None:
            positions = self.chunk_size_millions * 1_000_000

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
            # Single centroid: pos, mC, uC, tnc, N, Sx, Sx2 (no log sums or BB)
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

    def calculate_memory_per_chunk(self) -> Dict[str, Any]:
        """
        Calculate memory usage per chunk for different data types.

        Uses the get_memory_requirements method for consistent calculations.
        """
        positions_per_chunk = self.chunk_size_millions * 1_000_000

        # Get memory requirements for different data structures
        basic_sample_req = self.get_memory_requirements(positions_per_chunk, "basic_sample")
        centroid_req = self.get_memory_requirements(positions_per_chunk, "centroid")

        # Calculate processing overhead (temporary arrays, processing buffers)
        processing_overhead_factor = 1.5  # 50% overhead for processing
        total_memory_mb = centroid_req["total_with_overhead_mb"] * processing_overhead_factor  # Input + output + overhead

        # Create detailed breakdown
        memory_breakdown = {
            "basic_sample_mb": basic_sample_req["memory_mb"],
            "centroid_mb": centroid_req["memory_mb"],
            "processing_overhead_mb": centroid_req["processing_overhead_mb"],
            "total_per_chunk_mb": total_memory_mb,
            "bytes_per_position": {
                "basic_sample": basic_sample_req["bytes_per_position"],
                "centroid": centroid_req["bytes_per_position"]
            },
            "data_types_used": centroid_req["memory_per_type"],
            "chunk_info": {
                "positions_per_chunk": positions_per_chunk,
                "chunk_size_mb": self.chunk_size_millions
            }
        }

        return memory_breakdown

    def calculate_genome_chunk_size(self, total_positions: int, data_structure: str = "centroid") -> int:
        """
        Calculate optimal chunk size for genome-scale processing using maximum GPU utilization.

        For genome-scale processing on dedicated GPU resources, we want to maximize
        GPU memory usage (up to 95%) since sequential processing with full resources
        is more efficient than parallel sharing.

        Args:
            total_positions: Total number of genomic positions
            data_structure: Type of data structure ("basic_sample", "centroid")

        Returns:
            Optimal chunk size in positions
        """
        # Get memory requirements for the data structure
        memory_req = self.get_memory_requirements(total_positions, data_structure)

        # Use 95% of GPU memory for maximum performance
        gpu_available_mb = self.gpu_memory_gb * 1024 * 0.95

        # Calculate positions per MB
        positions_per_mb = total_positions / memory_req["total_with_overhead_mb"]

        # Calculate optimal chunk size
        optimal_chunk_size = int(gpu_available_mb * positions_per_mb)

        # Genome-scale bounds
        min_chunk = 10_000_000   # 10M minimum for GPU efficiency
        max_chunk = 500_000_000  # 500M maximum (fits in most GPU memory)

        optimal_chunk_size = max(min_chunk, optimal_chunk_size)
        optimal_chunk_size = min(max_chunk, optimal_chunk_size)
        optimal_chunk_size = min(optimal_chunk_size, total_positions)

        logger.info("Genome chunk size calculation:")
        logger.info(f"  Total positions: {total_positions:,}")
        logger.info(f"  Data structure: {data_structure}")
        logger.info(f"  Optimal chunk size: {optimal_chunk_size:,} positions")
        logger.info(f"  Memory per chunk: {memory_req['total_with_overhead_mb']:.1f}MB")
        logger.info(f"  GPU utilization: {(memory_req['total_with_overhead_mb'] / (self.gpu_memory_gb * 1024) * 100):.1f}%")

        return optimal_chunk_size

    def get_processing_strategy(self) -> Dict[str, Any]:
        """Get optimal processing strategy for genome scale."""

        return {
            "parallelization": {
                "gpu_accelerated": True,
                "cpu_threads_io": self.cpu_thread_pool,
                "cpu_threads_compute": self.cpu_count - self.cpu_thread_pool,
                "memory_mapped_files": True,
                "async_io": True
            },
            "chunking": {
                "strategy": "sliding_window",
                "overlap_positions": 1000,
                "compression": "zstd_level_9",
                "cache_strategy": "lru_cache"
            },
            "memory_management": {
                "pooling": True,
                "garbage_collection": "aggressive",
                "memory_mapping": True,
                "zero_copy": True
            },
            "io_optimization": {
                "buffer_size_mb": 256,
                "parallel_reads": 4,
                "write_buffering": True,
                "compression_level": 9
            }
        }

    def create_container_script(self) -> str:
        """Generate container launch script."""

        script = f'''#!/bin/bash
# MethylUtils Container Launch Script for NVIDIA GH200
# Optimized for human genome processing

set -e

# Container configuration
IMAGE="methylutils:cuda-optimized"
CONTAINER_NAME="methylutils-genome-processor"
WORK_DIR="/workspace"

# GPU and resource configuration
GPU_MEMORY="96g"
CPU_COUNT="16"
RAM_LIMIT="350g"

echo "🚀 Launching MethylUtils container for genome processing..."
echo "📊 Configuration:"
echo "   GPU Memory: $GPU_MEMORY"
echo "   CPU Cores: $CPU_COUNT"
echo "   RAM Limit: $RAM_LIMIT"
echo "   Working Directory: {self.container_work_dir}"

# Launch container with optimizations
docker run --rm --name "$CONTAINER_NAME" \\
    --gpus all \\
    --runtime nvidia \\
    --memory="$RAM_LIMIT" \\
    --cpus="$CPU_COUNT" \\
    --cpuset-cpus="0-15" \\
    --shm-size="256gb" \\
    -v "{self.host_work_dir}:/workspace:rw" \\
    -v "/tmp:/tmp:rw" \\
    -e CUDA_VISIBLE_DEVICES="0" \\
    -e OMP_NUM_THREADS="8" \\
    -e MKL_NUM_THREADS="8" \\
    -e PYTHONUNBUFFERED="1" \\
    -e METHYLUTILS_CHUNK_SIZE="{self.chunk_size_millions * 1000000}" \\
    --ulimit memlock=-1 \\
    --ulimit stack=67108864 \\
    --security-opt seccomp=unconfined \\
    --cap-add SYS_PTRACE \\
    -it "$IMAGE" \\
    bash

echo "✅ Container exited successfully"
'''

        return script

    def get_monitoring_config(self) -> Dict[str, Any]:
        """Get monitoring configuration for genome-scale processing."""

        return {
            "gpu_monitoring": {
                "memory_threshold": 0.9,  # Alert at 90% GPU memory usage
                "utilization_threshold": 0.95,  # Alert at 95% GPU utilization
                "temperature_threshold": 80,  # Alert at 80°C
                "polling_interval": 5  # Check every 5 seconds
            },
            "cpu_monitoring": {
                "memory_threshold": 0.85,  # Alert at 85% RAM usage
                "cpu_threshold": 0.90,  # Alert at 90% CPU usage
                "polling_interval": 10
            },
            "performance_metrics": {
                "processing_rate": True,  # Positions/second
                "memory_efficiency": True,  # Memory usage per position
                "io_throughput": True,  # MB/s read/write
                "gpu_efficiency": True  # GPU utilization percentage
            }
        }

def create_dockerfile() -> str:
    """Generate optimized Dockerfile for MethylUtils."""

    dockerfile = '''# MethylUtils CUDA-Optimized Container
FROM nvidia/cuda:13.0-devel-ubuntu22.04

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    python3.10 \\
    python3.10-dev \\
    python3.10-venv \\
    build-essential \\
    git \\
    hdf5-tools \\
    libhdf5-dev \\
    libzstd-dev \\
    && rm -rf /var/lib/apt/lists/*

# Create Python virtual environment
RUN python3.10 -m venv /opt/methylutils
ENV PATH="/opt/methylutils/bin:$PATH"

# Install Python packages with CUDA support
RUN pip install --upgrade pip setuptools wheel

# Install CuPy with CUDA 13.0 support
RUN pip install cupy-cuda13x

# Install scientific computing libraries
RUN pip install \\
    numpy \\
    scipy \\
    pandas \\
    h5py \\
    hdf5plugin \\
    nvidia-ml-py \\
    psutil \\
    gputil \\
    plotly \\
    pydantic \\
    numba \\
    zarr \\
    tqdm

# Install MethylUtils
COPY . /opt/methylutils-src
WORKDIR /opt/methylutils-src
RUN pip install -e .

# Create workspace directory
RUN mkdir -p /workspace
WORKDIR /workspace

# Set environment variables for optimization
ENV CUDA_VISIBLE_DEVICES=0 \\
    OMP_NUM_THREADS=8 \\
    MKL_NUM_THREADS=8 \\
    NUMEXPR_NUM_THREADS=8 \\
    PYTHONUNBUFFERED=1 \\
    HDF5_USE_FILE_LOCKING=FALSE \\
    PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \\
    CMD python3 -c "import methyl_utils; print('MethylUtils ready')"

# Default command
CMD ["python3", "-c", "import methyl_utils; print('MethylUtils CUDA container ready for genome processing')"]
'''

    return dockerfile

def main():
    """Main function to demonstrate container optimization."""

    optimizer = ContainerOptimizer()

    print("🔧 MethylUtils Container Optimization for NVIDIA GH200")
    print("=" * 60)

    # Container configuration
    container_config = optimizer.get_container_config()
    print("\n🐳 Container Configuration:")
    for key, value in container_config.items():
        print(f"   {key}: {value}")

    # Genome-scale optimization
    genome_opt = optimizer.optimize_for_genome_scale()
    print("\n🧬 Genome-Scale Optimization:")
    print(f"   Total Positions: {genome_opt['total_positions']:,}")
    print(f"   Chunk Size: {genome_opt['chunk_size']:,}")
    print(f"   Number of Chunks: {genome_opt['num_chunks']:,}")
    print(f"   GPU Memory Pool: {genome_opt['gpu_memory_pool_mb']:,} MB")

    # Memory calculation
    memory_calc = optimizer.calculate_memory_per_chunk()
    print("\n💾 Memory Requirements per Chunk:")
    for key, value in memory_calc.items():
        if isinstance(value, (int, float)):
            print(f"   {key}: {value:.1f}")
        elif isinstance(value, dict):
            print(f"   {key}:")
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, (int, float)):
                    print(f"     {sub_key}: {sub_value}")
                else:
                    print(f"     {sub_key}: {sub_value}")
        else:
            print(f"   {key}: {value}")

    # Genome-scale chunk size optimization
    genome_chunk_size = optimizer.calculate_genome_chunk_size(3_000_000_000)  # 3 billion positions
    print("\n🧬 Genome-Scale Chunk Optimization:")
    print(f"   Recommended chunk size: {genome_chunk_size:,} positions")
    print(f"   Number of chunks: {(3_000_000_000 // genome_chunk_size):,}")
    print(f"   GPU Memory Usage: {(genome_opt['gpu_memory_max_usage_mb'] / 1024):.1f} GB")
    print(f"   Memory Efficiency: {(genome_chunk_size / (genome_opt['gpu_memory_max_usage_mb'] * 1024 / 25)):.1f}x optimal")

    # Processing strategy
    strategy = optimizer.get_processing_strategy()
    print("\n⚡ Processing Strategy:")
    for category, settings in strategy.items():
        print(f"   {category}:")
        for key, value in settings.items():
            print(f"     {key}: {value}")

    # Generate container script
    script_path = optimizer.container_work_dir / "launch_methylutils.sh"
    script_content = optimizer.create_container_script()

    print(f"\n📝 Generating container launch script: {script_path}")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    with open(script_path, 'w') as f:
        f.write(script_content)
    script_path.chmod(0o755)

    # Generate Dockerfile
    dockerfile_path = optimizer.container_work_dir / "Dockerfile"
    dockerfile_content = create_dockerfile()

    print(f"📝 Generating optimized Dockerfile: {dockerfile_path}")
    with open(dockerfile_path, 'w') as f:
        f.write(dockerfile_content)

    print("\n✅ Container optimization complete!")
    print(f"   Launch script: {script_path}")
    print(f"   Dockerfile: {dockerfile_path}")
    print("\n🚀 Ready for human genome processing on NVIDIA GH200!")

if __name__ == "__main__":
    main()
