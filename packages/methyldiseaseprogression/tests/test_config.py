"""Tests for progression step Pydantic config."""

from methyl_disease_progression.config import ProgressionStepConfig


def test_progression_step_config_defaults():
    cfg = ProgressionStepConfig()
    assert cfg.enabled is False
    assert cfg.gene_score_mode == "effect_x_support"


def test_progression_step_config_nested_gene_set_metrics():
    cfg = ProgressionStepConfig.model_validate(
        {
            "enabled": True,
            "gene_set_metrics": {"enabled": True, "gene_universe": "mapper_all"},
        }
    )
    assert cfg.enabled is True
    assert cfg.gene_set_metrics is not None
    assert cfg.gene_set_metrics.enabled is True
    assert cfg.gene_set_metrics.gene_universe == "mapper_all"


def test_progression_rejects_unknown_gene_score_mode():
    from pydantic import ValidationError

    try:
        ProgressionStepConfig.model_validate({"gene_score_mode": "invalid"})
        assert False, "expected validation error"
    except ValidationError:
        pass
