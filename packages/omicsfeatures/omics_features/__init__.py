"""Shared samples-x-features seam for MethylPipeline omics packs."""

from .feature_store import (
    feature_h5_path,
    find_feature_h5,
    read_sample_features,
    write_sample_features,
)
from .matrix import load_feature_matrix
from .de_select import DeSelectConfig, run_de_select, select_de_features

__all__ = [
    "feature_h5_path",
    "find_feature_h5",
    "read_sample_features",
    "write_sample_features",
    "load_feature_matrix",
    "DeSelectConfig",
    "select_de_features",
    "run_de_select",
]
