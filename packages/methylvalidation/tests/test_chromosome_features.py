"""Unit tests for panel-scoped chromosome derived features."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from methyl_validation.chromosome_features import (
    _binary_entropy,
    _discrete_hellinger,
    _weighted_mean,
    build_ecdf_derived_measures_schema,
    chromosome_feature_names,
    compute_chromosome_feature_matrix,
    compute_ecdf_derived_features_from_schema,
)


def test_chromosome_feature_names_include_globals_and_per_chrom():
    names = chromosome_feature_names(
        ["1", "2"],
        class_labels=["healthy", "disease"],
        distance_metrics=["js", "hellinger"],
    )
    assert "sample::global_mean_beta" in names
    assert "chrom::1::mean_beta" in names
    assert "chrom::1::js_to__healthy" in names
    assert "chrom::2::hellinger_to__disease" in names
    assert "sample::js_margin" in names


def test_binary_entropy_and_weighted_mean():
    vals = np.array([0.1, 0.9, 0.5], dtype=np.float64)
    w = np.array([1.0, 2.0, 1.0], dtype=np.float64)
    assert np.isfinite(_weighted_mean(vals, w))
    assert np.isfinite(_binary_entropy(vals, w))


def test_discrete_hellinger_identical_is_zero():
    p = np.array([0.2, 0.3, 0.5], dtype=np.float64)
    assert _discrete_hellinger(p, p) == pytest.approx(0.0, abs=1e-6)


def test_compute_chromosome_feature_matrix_shape():
    locus_df = pd.DataFrame(
        {
            "chromosome": ["1", "1", "2"],
            "context": ["CG", "CG", "CG"],
            "position": [100, 200, 300],
            "effect_size": [0.5, -0.3, 0.8],
        }
    )
    X_raw = np.array(
        [
            [0.2, 0.4, 0.6],
            [0.3, 0.5, 0.7],
        ],
        dtype=np.float32,
    )
    w = np.abs(locus_df["effect_size"].to_numpy(dtype=np.float64))
    X_out, names, report = compute_chromosome_feature_matrix(
        X_raw,
        locus_df,
        w,
        class_labels=["healthy", "disease"],
        healthy_class_label="healthy",
        centroid_dir_by_class_label=None,
    )
    assert X_out.shape == (2, len(names))
    assert report["n_features"] == len(names)
    assert np.isfinite(X_out[0, names.index("sample::global_mean_beta")])


def test_ecdf_derived_measures_schema_roundtrip():
    locus_df = pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 200],
            "effect_size": [0.5, -0.3],
        }
    )
    w = np.abs(locus_df["effect_size"].to_numpy(dtype=np.float64))
    schema = build_ecdf_derived_measures_schema(
        locus_df,
        effect_size_weights=w,
        class_labels=["healthy", "disease"],
        healthy_class_label="healthy",
        centroid_dir_by_class_label={"healthy": "/tmp/h", "disease": "/tmp/d"},
    )
    assert schema is not None
    row = np.array([0.2, 0.4], dtype=np.float64)
    vec, names = compute_ecdf_derived_features_from_schema(row, schema)
    assert len(vec) == len(names)
    assert names == schema["feature_names"]
