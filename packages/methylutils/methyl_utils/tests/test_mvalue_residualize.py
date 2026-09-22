"""Tests for leakage-safe M-value residualization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from methyl_utils.mvalue_residualize import (
    ResidualizeApplier,
    ResidualizeModel,
    apply_if_model,
    apply_residualize,
    beta_to_m,
    design_matrix_from_covariates,
    drop_near_zero_variance,
    fit_ols_mvalues,
    m_to_beta,
    sample_id_from_path,
)


EPS = 1e-6


def test_beta_m_roundtrip_away_from_bounds() -> None:
    beta = np.array([0.2, 0.5, 0.8])
    rec = m_to_beta(beta_to_m(beta, EPS), EPS)
    np.testing.assert_allclose(rec, beta, rtol=1e-10, atol=1e-10)


def test_m_to_beta_stays_in_open_unit_interval() -> None:
    m = np.array([-50.0, 0.0, 50.0])
    beta = m_to_beta(m, EPS)
    assert np.all(beta > 0.0) and np.all(beta < 1.0)


def test_drop_near_zero_variance_ignores_constant_smoking() -> None:
    z = np.column_stack(
        [
            np.linspace(-1.0, 1.0, 8),
            np.ones(8),
        ]
    )
    kept, names, dropped = drop_near_zero_variance(z, ["age", "smoking"], threshold=1e-8)
    assert names == ["age"]
    assert dropped == ["smoking"]
    assert kept.shape == (8, 1)


def test_train_only_fit_does_not_use_heldout_rows() -> None:
    rng = np.random.default_rng(0)
    n_train, n_test, n_pos = 20, 8, 5
    z_all = rng.normal(size=(n_train + n_test, 2))
    gamma = np.array([0.4, -0.2])
    intercept = 0.1
    noise = rng.normal(scale=0.05, size=(n_pos, n_train + n_test))
    m_all = intercept + (z_all @ gamma)[None, :] + noise
    beta_all = m_to_beta(m_all, EPS)

    coef_train = fit_ols_mvalues(beta_to_m(beta_all[:, :n_train], EPS), z_all[:n_train])
    coef_leaky = fit_ols_mvalues(beta_to_m(beta_all, EPS), z_all)
    # Leakage moves coefficients; train-only must differ from full-data fit.
    assert not np.allclose(coef_train, coef_leaky, atol=1e-12)

    pos = np.arange(n_pos, dtype=np.uint32)
    test_i = n_train
    adj_honest = apply_residualize(
        beta_all[:, test_i], pos, z_all[test_i], pos, coef_train, EPS
    )
    adj_leaky = apply_residualize(
        beta_all[:, test_i], pos, z_all[test_i], pos, coef_leaky, EPS
    )
    assert not np.allclose(adj_honest, adj_leaky, atol=1e-12)


def test_apply_if_model_none_is_identity() -> None:
    beta = np.array([0.11, 0.42, 0.73])
    pos = np.array([10, 20, 30], dtype=np.uint32)
    out = apply_if_model(None, None, "S1", pos, beta)
    np.testing.assert_array_equal(out, beta)


def test_zero_coef_apply_near_identity_for_interior_betas() -> None:
    pos = np.array([1, 2, 3], dtype=np.uint32)
    beta = np.array([0.3, 0.5, 0.7])
    coef = np.zeros((3, 2), dtype=np.float64)  # intercept + 1 covariate
    adj = apply_residualize(beta, pos, np.array([0.5]), pos, coef, EPS)
    np.testing.assert_allclose(adj, beta, rtol=1e-10, atol=1e-10)


def test_sample_id_from_h5_uses_parent() -> None:
    assert sample_id_from_path("/work/samples/S42/1-CG.h5") == "S42"
    assert sample_id_from_path("/work/samples/S42") == "S42"


def test_residualize_model_roundtrip(tmp_path) -> None:
    model = ResidualizeModel(
        chrom="1",
        ctx="CG",
        positions=np.array([10, 20], dtype=np.uint32),
        coef=np.array([[0.1, 0.2], [0.0, -0.1]], dtype=np.float64),
        covariate_names=["alr_CD8T_vs_Neu"],
        eps=EPS,
        dropped_covariates=["smoking_score"],
        train_sample_ids=["A", "B"],
    )
    path = model.save(tmp_path)
    loaded = ResidualizeModel.load(path)
    np.testing.assert_array_equal(loaded.positions, model.positions)
    np.testing.assert_allclose(loaded.coef, model.coef, rtol=1e-6)
    assert loaded.covariate_names == ["alr_CD8T_vs_Neu"]
    assert loaded.dropped_covariates == ["smoking_score"]


def test_applier_matches_direct_apply() -> None:
    ids = ["S0", "S1"]
    cov = pd.DataFrame(
        {
            "sample_id": ids,
            "CD8T": [0.2, 0.1],
            "CD4T": [0.2, 0.2],
            "NK": [0.1, 0.1],
            "Bcell": [0.1, 0.1],
            "Mono": [0.1, 0.1],
            "Neu": [0.3, 0.4],
            "age_score": [0.1, 0.8],
        }
    )
    z, names, dropped, missing = design_matrix_from_covariates(
        cov,
        ids,
        numeric_columns=["age_score"],
        composition_columns=["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"],
        composition_reference="Neu",
        composition_pseudocount=1e-6,
        variance_threshold=1e-12,
    )
    assert not missing
    assert "age_score" in names
    assert dropped == []
    m = np.array([[0.2, -0.1], [0.0, 0.3]])  # 2 pos × 2 samples
    coef = fit_ols_mvalues(m, z)
    model = ResidualizeModel(
        chrom="1",
        ctx="CG",
        positions=np.array([5, 9], dtype=np.uint32),
        coef=coef,
        covariate_names=names,
        eps=EPS,
        train_sample_ids=ids,
    )
    applier = ResidualizeApplier(
        model,
        cov,
        numeric_columns=["age_score"],
        composition_columns=["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"],
        composition_reference="Neu",
        composition_pseudocount=1e-6,
    )
    beta = m_to_beta(m[:, 1], EPS)
    out = applier.apply_for_sample("S1", model.positions, beta)
    direct = apply_residualize(beta, model.positions, z[1], model.positions, coef, EPS)
    np.testing.assert_allclose(out, direct, rtol=1e-10, atol=1e-10)


def test_design_matrix_reports_missing_ids() -> None:
    cov = pd.DataFrame({"sample_id": ["A"], "age_score": [1.0]})
    z, names, dropped, missing = design_matrix_from_covariates(
        cov,
        ["A", "B"],
        numeric_columns=["age_score"],
    )
    assert missing == ["B"]
    assert z.shape[0] == 0
    assert names == []
    assert dropped == []
