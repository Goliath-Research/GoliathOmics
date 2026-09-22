from pathlib import Path

import pandas as pd
import pytest

from methyl_detector.core.methyldetector import _validate_fixed_panel_recurrence_metadata


def test_fixed_panel_recurrence_rejects_frequency_over_one():
    df = pd.DataFrame(
        {
            "chromosome": [1],
            "position": [100],
            "frequency": [1.2],
            "count": [12],
            "n_runs": [10],
        }
    )
    with pytest.raises(ValueError, match="frequency"):
        _validate_fixed_panel_recurrence_metadata(df, Path("panel.csv"))


def test_fixed_panel_recurrence_rejects_frequency_ratio_mismatch():
    df = pd.DataFrame(
        {
            "chromosome": [1],
            "position": [100],
            "frequency": [0.7],
            "count": [8],
            "n_runs": [10],
        }
    )
    with pytest.raises(ValueError, match="count / n_runs"):
        _validate_fixed_panel_recurrence_metadata(df, Path("panel.csv"))


def test_fixed_panel_recurrence_accepts_valid_rows():
    df = pd.DataFrame(
        {
            "chromosome": [1, 1],
            "position": [100, 200],
            "frequency": [0.8, 1.0],
            "count": [8, 10],
            "n_runs": [10, 10],
        }
    )
    _validate_fixed_panel_recurrence_metadata(df, Path("panel.csv"))
