"""
MethylDetector - DMP detection and classifier training using ECDF-based comparison.

This package provides tools for classifying two groups of genomics samples
using divergences calculated against a common reference sample (centroid).
"""

# Check for MethylUtils dependency (hard requirement)
try:
    import methyl_utils
    _METHYL_UTILS_AVAILABLE = True
except ImportError:
    _METHYL_UTILS_AVAILABLE = False
    import warnings
    warnings.warn(
        "MethylUtils not found. MethylUtils is a hard requirement for MethylDetector. "
        "Please ensure MethylUtils is installed and available in PYTHONPATH. "
        "You can set the METHYL_UTILS_PATH environment variable or use the run_in_container.sh script.",
        ImportWarning,
        stacklevel=2
    )

from .core.methyldetector import MethylDetector
from .models.config import MethylDetectorConfig
from .models.results import MethylDetectorResult

# Import MethylCentroidPair from MethylUtils
from methyl_utils import MethylCentroidPair

__version__ = "0.2.0"
__all__ = [
    "MethylDetector",
    "MethylDetectorConfig",
    "MethylDetectorResult",
    "MethylCentroidPair"
] 