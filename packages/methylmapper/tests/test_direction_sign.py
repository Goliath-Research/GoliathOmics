"""Tests for direction-to-sign support in custom DMP CSVs."""

import numpy as np
import pandas as pd

from methyl_mapper.bedtools_mapper import BedtoolsMapper


def test_direction_to_sign_maps_common_tokens() -> None:
    s = pd.Series(["hyper", "hypo", "up", "down", "+", "-", "1", "-1", "unknown"])
    out = BedtoolsMapper._direction_to_sign(s)
    assert out.iloc[0] == 1.0
    assert out.iloc[1] == -1.0
    assert out.iloc[2] == 1.0
    assert out.iloc[3] == -1.0
    assert out.iloc[4] == 1.0
    assert out.iloc[5] == -1.0
    assert out.iloc[6] == 1.0
    assert out.iloc[7] == -1.0
    assert np.isnan(out.iloc[8])


def test_normalize_dmp_columns_maps_direction_aliases() -> None:
    df = pd.DataFrame({"delta direction": ["hyper"], "chr": ["1"], "pos": [123]})
    norm = BedtoolsMapper._normalize_dmp_columns(df)
    assert "direction" in norm.columns
    assert "chromosome" in norm.columns
    assert "position" in norm.columns

