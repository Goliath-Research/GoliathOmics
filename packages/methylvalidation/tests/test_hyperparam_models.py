"""Tests for typed portal/cfg hyperparameter grid models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from methyl_validation.hyperparam_models import (
    HyperparamGridSpec,
    HyperparamSearchRequest,
    HyperparamTrialOverlay,
)


def test_grid_expand_cartesian_product() -> None:
    grid = HyperparamGridSpec(
        axes={"validation.stability_dmp_freq": [0.6, 0.7], "validation.max_genes": [100, 200]}
    )
    overlays = grid.expand()
    assert len(overlays) == 4
    assert overlays[0].overrides == {
        "validation.stability_dmp_freq": 0.6,
        "validation.max_genes": 100,
    }
    assert overlays[-1].overrides == {
        "validation.stability_dmp_freq": 0.7,
        "validation.max_genes": 200,
    }
    assert [o.index for o in overlays] == [0, 1, 2, 3]


def test_empty_grid_expands_to_no_trials() -> None:
    assert HyperparamGridSpec().expand() == []


def test_search_request_requires_project_path() -> None:
    with pytest.raises(ValidationError):
        HyperparamSearchRequest.model_validate({"grid": {"axes": {}}})


def test_search_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        HyperparamSearchRequest.model_validate(
            {"project_path": "/p", "unexpected": 1}
        )


def test_trial_overlay_index_non_negative() -> None:
    with pytest.raises(ValidationError):
        HyperparamTrialOverlay(index=-1, overrides={})
