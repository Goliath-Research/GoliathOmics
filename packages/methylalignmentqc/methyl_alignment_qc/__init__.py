"""
MethylAlignmentQC - Parse NVIDIA Clara Parabricks Alignment QC Metrics

Parse alignment QC metrics into per-sample JSON files for database storage.
Output: one V2 JSON per sample as {output_dir}/{sample_basename}.json.
"""

from .cli.main import main
from .utils.monitor import run_with_logging

__version__ = "0.1.0"
__all__ = ["main", "run_with_logging"]
