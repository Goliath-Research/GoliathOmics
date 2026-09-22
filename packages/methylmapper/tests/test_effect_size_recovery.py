"""Tests for recovering effect_size from alternate export columns."""

import numpy as np
import pandas as pd

from methyl_mapper.bedtools_mapper import BedtoolsMapper


def test_recover_effect_size_from_mean_column_when_flat_fallback() -> None:
    df = pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 200],
            "effect_size": [1.0, 1.0],
            "effect_size_mean": [0.42, 0.81],
        }
    )

    norm = BedtoolsMapper._normalize_dmp_columns(df)
    assert np.allclose(norm["effect_size"].to_numpy(dtype=float), np.array([0.42, 0.81]))


def test_recover_effect_size_from_per_comparison_columns() -> None:
    df = pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 200],
            "effect_size__pca1": [0.20, 0.65],
            "effect_size__pca2": [0.45, 0.55],
        }
    )

    norm = BedtoolsMapper._normalize_dmp_columns(df)
    assert "effect_size" in norm.columns
    assert np.allclose(norm["effect_size"].to_numpy(dtype=float), np.array([0.45, 0.65]))
