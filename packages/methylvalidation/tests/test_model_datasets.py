"""Canonical model_bundle design-matrix HDF5 I/O."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_validation.model_datasets import (
    TRAIN_DATASET_NAME,
    read_dataset_frame,
    write_dataset_frame,
)


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_id": ["S0", "S1"],
            "class_index": [0, 1],
            "class_label": ["healthy", "disease"],
            "prob_class1": [0.2, 0.8],
            "age": [40.0, 55.0],
        }
    )


def test_write_read_dataset_h5_float32(tmp_path: Path):
    path = tmp_path / TRAIN_DATASET_NAME
    write_dataset_frame(path, _sample_frame())
    assert path.suffix == ".h5"
    out = read_dataset_frame(path)
    assert list(out.columns) == [
        "sample_id",
        "class_index",
        "class_label",
        "prob_class1",
        "age",
    ]
    assert out["prob_class1"].dtype == np.float32
    assert out["age"].dtype == np.float32
    assert out.loc[0, "prob_class1"] == pytest.approx(0.2, abs=1e-6)


def test_read_legacy_parquet_compat(tmp_path: Path):
    path = tmp_path / "train_dataset.parquet"
    frame = _sample_frame()
    frame.to_parquet(path, index=False)
    out = read_dataset_frame(path)
    assert list(out["sample_id"]) == ["S0", "S1"]
    assert out.loc[1, "prob_class1"] == pytest.approx(0.8)
