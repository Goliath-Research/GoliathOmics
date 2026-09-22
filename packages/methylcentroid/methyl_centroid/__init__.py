"""
MethylCentroid
==============

Advanced Methylation Centroid Calculation.

This package provides a modular, SOLID-compliant architecture for calculating
DNA methylation centroids from multiple samples.

Features:
- Memory-efficient processing (chunked genomic processing)
- GPU acceleration support (via CuPy)
- Robust sample management and alignment
- Comprehensive configuration system
- Detailed performance profiling and logging
"""

from .config import MethylCentroidConfig, BatchProcessingConfig
from .core import MethylCentroid
from .core.sample_manager import SampleManager

__version__ = "2.0.0"
__all__ = [
    "MethylCentroid",
    "MethylCentroidConfig",
    "BatchProcessingConfig",
    "SampleManager"
]