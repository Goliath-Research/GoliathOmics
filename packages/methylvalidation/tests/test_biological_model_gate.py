"""Biological review gate for methyl-validation --model."""

import pytest

from methyl_validation.config import MonteCarloConfig, assert_production_model_build_allowed


def _minimal_mc_dict(**extra):
    return {
        "samples_base_path": "/tmp/s",
        "cohorts": [
            {"label": "healthy", "csv": "h.csv"},
            {"label": "disease", "csv": "d.csv"},
        ],
        "train_fraction": 0.8,
        "n_iterations": 1,
        "base_project": "p.json",
        "output_base": "/tmp/out",
        **extra,
    }


def test_assert_production_model_build_allowed_blocks_when_required():
    cfg = MonteCarloConfig.model_validate(
        _minimal_mc_dict(
            require_biological_review_for_model=True,
            biological_review_confirmed=False,
        )
    )
    with pytest.raises(ValueError, match="biological_review_confirmed"):
        assert_production_model_build_allowed(cfg)


def test_assert_production_model_build_allowed_passes_when_confirmed():
    cfg = MonteCarloConfig.model_validate(
        _minimal_mc_dict(
            require_biological_review_for_model=True,
            biological_review_confirmed=True,
        )
    )
    assert_production_model_build_allowed(cfg)


def test_assert_production_model_build_allowed_passes_when_not_required():
    cfg = MonteCarloConfig.model_validate(_minimal_mc_dict())
    assert_production_model_build_allowed(cfg)
