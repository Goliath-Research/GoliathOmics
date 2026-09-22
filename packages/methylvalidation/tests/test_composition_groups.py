"""Typed composition (simplex) groups: universal ALR encoding for covariates."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_validation.config import EcdfBackendParams
from methyl_validation.covariate_preprocessor import (
    CompositionGroupSpec,
    fit_covariates,
    normalize_composition_groups,
    transform_covariates,
)


def _two_simplex_csv(path: Path, sample_ids: list[str]) -> Path:
    rows = []
    for i, sid in enumerate(sample_ids):
        cd8 = 0.05 + 0.002 * i
        cd4 = 0.15
        neu = 1.0 - (cd8 + cd4)
        # A second, independent simplex (e.g. a future composition).
        a = 0.3 + 0.01 * i
        b = 1.0 - a
        rows.append(
            {
                "sample_id": sid,
                "CD8T": cd8,
                "CD4T": cd4,
                "Neu": neu,
                "compartA": a,
                "compartB": b,
                "age": 40.0 + i,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_normalize_defaults_reference_to_last_requires_pseudocount():
    specs = normalize_composition_groups(
        [{"name": "cf", "columns": ["CD8T", "CD4T", "Neu"], "pseudocount": 1e-6}]
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.reference == "Neu"
    assert spec.pseudocount == pytest.approx(1e-6)
    assert spec.standardize is True
    assert spec.alr_names() == ["alr_CD8T_vs_Neu", "alr_CD4T_vs_Neu"]


def test_normalize_rejects_missing_pseudocount():
    with pytest.raises(ValueError, match="requires pseudocount"):
        normalize_composition_groups(
            [{"name": "cf", "columns": ["CD8T", "CD4T", "Neu"]}]
        )


def test_normalize_rejects_shared_columns_and_small_groups():
    with pytest.raises(ValueError, match="at least 2 columns"):
        normalize_composition_groups(
            [{"name": "x", "columns": ["only"], "pseudocount": 1e-6}]
        )
    with pytest.raises(ValueError, match="must not share columns"):
        normalize_composition_groups(
            [
                {"name": "a", "columns": ["p", "q"], "pseudocount": 1e-6},
                {"name": "b", "columns": ["q", "r"], "pseudocount": 1e-6},
            ]
        )


def test_legacy_single_group_maps_to_default():
    specs = normalize_composition_groups(
        None,
        legacy_transform="alr",
        legacy_columns=["CD8T", "CD4T", "Neu"],
        legacy_reference="Neu",
        legacy_pseudocount=1e-6,
    )
    assert [s.name for s in specs] == ["default"]
    assert specs[0].reference == "Neu"


def test_two_disjoint_groups_produce_alr_and_drop_reference(tmp_path: Path):
    ids = [f"S{i}" for i in range(6)]
    cov = _two_simplex_csv(tmp_path / "cov.csv", ids)
    groups = normalize_composition_groups(
        [
            {
                "name": "cf",
                "columns": ["CD8T", "CD4T", "Neu"],
                "reference": "Neu",
                "pseudocount": 1e-6,
            },
            {
                "name": "cmp",
                "columns": ["compartA", "compartB"],
                "reference": "compartB",
                "pseudocount": 1e-6,
            },
        ]
    )
    X, prep, report = fit_covariates(
        str(cov),
        ids,
        covariate_id_column="sample_id",
        strict_join=True,
        numeric_columns=["age"],
        composition_groups=groups,
    )
    assert prep is not None and X is not None
    # age + (3-1) + (2-1) = 4 columns; raw proportions never appear.
    assert X.shape == (6, 4)
    names = prep.output_columns
    assert "alr_CD8T_vs_Neu" in names
    assert "p_compartA" in names
    assert not any(n in names for n in ("CD8T", "CD4T", "Neu", "compartA", "compartB"))
    assert set(report["composition_output_columns"]) == {
        "alr_CD8T_vs_Neu",
        "alr_CD4T_vs_Neu",
        "p_compartA",
    }


def test_composition_columns_may_not_be_declared_numeric(tmp_path: Path):
    ids = [f"S{i}" for i in range(6)]
    cov = _two_simplex_csv(tmp_path / "cov.csv", ids)
    groups = normalize_composition_groups(
        [{"name": "cf", "columns": ["CD8T", "CD4T", "Neu"], "pseudocount": 1e-6}]
    )
    with pytest.raises(ValueError, match="overlap composition group columns"):
        fit_covariates(
            str(cov),
            ids,
            covariate_id_column="sample_id",
            strict_join=True,
            numeric_columns=["CD8T", "age"],
            composition_groups=groups,
        )


def test_standardize_off_keeps_raw_alr_and_frozen_transform_matches(tmp_path: Path):
    ids = [f"S{i}" for i in range(6)]
    cov = _two_simplex_csv(tmp_path / "cov.csv", ids)
    groups = normalize_composition_groups(
        [
            {
                "name": "cf",
                "columns": ["CD8T", "CD4T", "Neu"],
                "reference": "Neu",
                "pseudocount": 1e-6,
                "standardize": False,
            }
        ]
    )
    X, prep, _report = fit_covariates(
        str(cov),
        ids,
        covariate_id_column="sample_id",
        strict_join=True,
        numeric_columns=["age"],
        composition_groups=groups,
        standardize_numeric=True,
    )
    assert prep is not None and X is not None
    assert set(prep.composition_no_standardize_columns) == {
        "alr_CD8T_vs_Neu",
        "alr_CD4T_vs_Neu",
    }
    alr_idx = prep.output_columns.index("alr_CD8T_vs_Neu")
    # Non-standardized ALR keeps its natural (non-zero-mean) scale.
    assert abs(float(np.mean(X[:, alr_idx]))) > 1e-3
    # Frozen apply reproduces the fitted matrix exactly on the same rows.
    X2, _ = transform_covariates(str(cov), ids, prep, strict_join=True)
    assert np.allclose(X, X2, atol=1e-6)


def test_config_rejects_composition_overlap_with_numeric_role():
    with pytest.raises(ValueError, match="overlap numeric/ordinal/categorical"):
        EcdfBackendParams(
            covariate_numeric_columns=["CD8T", "age"],
            covariate_composition_groups=[
                {
                    "name": "cf",
                    "columns": ["CD8T", "CD4T", "Neu"],
                    "pseudocount": 1e-6,
                }
            ],
        )


def test_categorical_one_hot_drops_one_level(tmp_path: Path):
    """Nominal covariates: L levels → L−1 columns (dummy-variable trap avoided)."""
    ids = ["S0", "S1", "S2", "S3"]
    cov = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ids,
            "ethnicity": ["A", "B", "A", "C"],
            "age": [40, 41, 42, 43],
        }
    ).to_csv(cov, index=False)
    X, prep, report = fit_covariates(
        str(cov),
        ids,
        covariate_id_column="sample_id",
        strict_join=True,
        numeric_columns=["age"],
        categorical_columns=["ethnicity"],
        standardize_numeric=False,
    )
    assert prep is not None and X is not None
    # Sorted levels A,B,C + __UNKNOWN__; drop first non-unknown (=A) → B,C,__UNKNOWN__
    assert prep.categorical_drop_levels["ethnicity"] == "A"
    eth_cols = [c for c in prep.output_columns if c.startswith("ethnicity__")]
    assert "ethnicity__A" not in eth_cols
    # Column names are ``{col}__{level}``; unknown token is ``__UNKNOWN__``.
    assert set(eth_cols) == {
        "ethnicity__B",
        "ethnicity__C",
        "ethnicity____UNKNOWN__",
    }
    assert report["categorical_drop_levels"]["ethnicity"] == "A"
    # Sample S0 (ethnicity A) has zeros on all ethnicity one-hots.
    eth_idx = [prep.output_columns.index(c) for c in eth_cols]
    assert np.allclose(X[0, eth_idx], 0.0)
    # Sample S1 (ethnicity B) lights only ethnicity__B.
    assert X[1, prep.output_columns.index("ethnicity__B")] == 1.0
    X2, _ = transform_covariates(str(cov), ids, prep, strict_join=True)
    assert np.allclose(X, X2)


def test_ordinal_is_single_numeric_feature(tmp_path: Path):
    ids = ["S0", "S1", "S2", "S3"]
    cov = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ids,
            "risk_band": ["low", "medium", "high", "medium"],
        }
    ).to_csv(cov, index=False)
    X, prep, _report = fit_covariates(
        str(cov),
        ids,
        covariate_id_column="sample_id",
        strict_join=True,
        ordinal_columns=["risk_band"],
        ordinal_maps={"risk_band": {"low": 1, "medium": 2, "high": 3}},
        standardize_numeric=False,
    )
    assert prep is not None and X is not None
    assert prep.output_columns == ["risk_band"]
    assert X.shape == (4, 1)
    assert list(X[:, 0]) == [1.0, 2.0, 3.0, 2.0]
