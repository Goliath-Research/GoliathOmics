"""Cached ECDF validation state for fast repeated top-k DMP evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pandas as pd


@dataclass
class ValidationPrefixCache:
    """Cached ECDF validation state for fast repeated top-k evaluation."""

    sorted_df: pd.DataFrame
    weights: np.ndarray
    y_val: np.ndarray
    splits: List[Tuple[np.ndarray, np.ndarray]]
    prefix_ll_c1: List[np.ndarray]
    prefix_ll_c2: List[np.ndarray]
    prefix_ll_c1_calib: List[np.ndarray]
    prefix_ll_c2_calib: List[np.ndarray]
    temperature: float
