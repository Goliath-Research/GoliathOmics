"""Core utilities for MethylDetector."""

from pathlib import Path
import pandas as pd
import json
from typing import List, Dict
from pydantic import BaseModel

# Import directly from MethylUtils
from methyl_utils.gpu_detection import (
    is_gpu_available,
    is_cupyx_scipy_special_available,
    cleanup_gpu_memory
)

# GPU cleanup handler management
import atexit

# Global flag to track if cleanup handlers are already registered
_global_cleanup_handlers_registered = False

def register_global_cleanup_handlers():
    """Register global cleanup handlers if not already registered."""
    global _global_cleanup_handlers_registered
    if not _global_cleanup_handlers_registered:
        atexit.register(cleanup_gpu_memory)
        _global_cleanup_handlers_registered = True
        return True
    return False

def are_global_cleanup_handlers_registered():
    """Check if global cleanup handlers are already registered."""
    return _global_cleanup_handlers_registered

class GPUConfig:
    """Singleton for GPU configuration."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.GPU_AVAILABLE = is_gpu_available() and is_cupyx_scipy_special_available()
            cls._instance.cp = None
            cls._instance.special = None
            if cls._instance.GPU_AVAILABLE:
                try:
                    import cupy as cp
                    from cupyx.scipy.special import digamma as cupy_digamma, polygamma as cupy_polygamma, betaln as cupy_betaln
                    cls._instance.cp = cp
                    cls._instance.special = type('Special', (), {
                        'digamma': cupy_digamma,
                        'polygamma': cupy_polygamma,
                        'betaln': cupy_betaln
                    })
                except ImportError:
                    cls._instance.GPU_AVAILABLE = False
        return cls._instance
    
    def cleanup(self):
        """Clean up GPU memory."""
        if self.GPU_AVAILABLE:
            cleanup_gpu_memory()

# Import logging functions from MethylUtils
try:
    from methyl_utils.logging_utils import setup_logging, setup_module_logging
except ImportError:
    # Fallback imports if MethylUtils not available
    import logging
    def setup_logging(verbose: bool = False, log_file=None, log_level=None):
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=level, format='%(levelname)s: %(message)s')
    
    def setup_module_logging(module_name: str, verbose: bool = False):
        return logging.getLogger(module_name)

def load_config_from_json(config_path: Path) -> BaseModel:
    """Load Pydantic config from JSON."""
    with open(config_path, 'r') as f:
        config_data = json.load(f)
    from ..models.config import MethylDetectorConfig
    return MethylDetectorConfig(**config_data)

def save_csv(data: List[Dict], filename: Path, columns: List[str]) -> None:
    """Save data as CSV."""
    df = pd.DataFrame(data, columns=columns)
    df.to_csv(filename, index=False)

def save_json(data: Dict, filename: Path) -> None:
    """Save data as JSON."""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2, default=str)

def save_summary_txt(summary_lines: List[str], filename: Path) -> None:
    """Save text summary."""
    with open(filename, 'w') as f:
        f.write("\n".join(summary_lines))
