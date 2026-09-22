from __future__ import annotations

import pytest

from methyl_validation.config import MonteCarloConfig


def _base_payload() -> dict:
    return {
        "samples_base_path": "/tmp",
        "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
        "train_fraction": 0.8,
        "n_iterations": 1,
        "base_project": "/tmp/project.json",
        "output_base": "/tmp",
        "backend_profiles": {
            "ecdf": {"enabled": False, "params": {}},
            "tabular_sklearn": {"enabled": True, "params": {}},
            "generative_hybrid": {"enabled": False, "params": {}},
        },
    }


def test_tabular_methods_discriminated_union_parses():
    payload = _base_payload()
    payload["backend_profiles"]["tabular_sklearn"]["params"]["tabular_methods"] = [
        {"method": "random_forest", "params": {"n_estimators": 123}},
        {"method": "xgboost", "params": {"n_estimators": 333, "max_depth": 4}},
        {"method": "logistic_regression", "params": {"max_iter": 222, "c": 0.5}},
    ]
    cfg = MonteCarloConfig.model_validate(payload)
    assert len(cfg.tabular_methods or []) == 3
    assert cfg.tabular_methods[0].method == "random_forest"
    assert cfg.tabular_methods[0].params.n_estimators == 123
    assert cfg.tabular_methods[1].method == "xgboost"
    assert cfg.tabular_methods[1].params.max_depth == 4
    assert cfg.tabular_methods[2].method == "logistic_regression"
    assert cfg.tabular_methods[2].params.max_iter == 222


def test_rejects_legacy_tabular_model_type():
    payload = _base_payload()
    payload["tabular_model_type"] = "hist_gradient_boosting"
    with pytest.raises(ValueError, match="Removed legacy Monte Carlo config keys"):
        MonteCarloConfig.model_validate(payload)


def test_tabular_method_selection_metric_is_validated():
    payload = _base_payload()
    payload["backend_profiles"]["tabular_sklearn"]["params"]["tabular_method_selection_metric"] = "roc_auc"
    with pytest.raises(ValueError, match="tabular_method_selection_metric"):
        MonteCarloConfig.model_validate(payload)


def test_tabular_dataset_reuse_and_test_export_defaults():
    cfg = MonteCarloConfig.model_validate(_base_payload())
    assert cfg.tabular_save_train_dataset is True
    assert cfg.tabular_reuse_train_dataset is True
    assert cfg.tabular_save_test_dataset is True
    assert cfg.tabular_test_dataset_path is None
