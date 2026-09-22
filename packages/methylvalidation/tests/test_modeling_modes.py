"""Tests for process-agnostic DMP / gene modeling mode resolution."""

from __future__ import annotations

from methyl_validation.config import MonteCarloConfig
from methyl_validation.modeling_modes import (
    apply_modeling_modes_to_validation_dict,
    infer_dmp_modeling_mode,
    infer_gene_modeling_mode,
    infer_gene_recurrence_source,
    mapper_csv_pattern_for_dmp_mode,
    resolve_gene_stability_preferences,
)


def _minimal_mc(**overrides):
    base = {
        "samples_base_path": "/work/samples",
        "base_project": "/tmp/project.json",
        "output_base": "/work/projects/test",
        "train_fraction": 0.8,
        "n_iterations": 10,
        "cohorts": [
            {"label": "healthy", "csv": "/tmp/h.csv"},
            {"label": "disease", "csv": "/tmp/d.csv"},
        ],
    }
    base.update(overrides)
    return base


def test_mode1_raw_pool_discovery_only() -> None:
    derived = apply_modeling_modes_to_validation_dict(
        {"dmp_modeling_mode": "raw_pool", "gene_modeling_mode": "none"}
    )
    assert derived["stability_featurecuts_enabled"] is False
    assert derived["stability_gene_featurecuts_enabled"] is False
    assert infer_dmp_modeling_mode(derived) == "raw_pool"
    assert infer_gene_modeling_mode(derived) == "none"


def test_mode2_dmp_featurecuts_seeds_flags() -> None:
    derived = apply_modeling_modes_to_validation_dict(
        {
            "dmp_modeling_mode": "featurecuts",
            "gene_modeling_mode": "none",
            "dmp_featurecuts_target_ba": 0.95,
        }
    )
    assert derived["stability_featurecuts_enabled"] is True
    assert derived["stability_target_balanced_accuracy"] == 0.95
    assert mapper_csv_pattern_for_dmp_mode("featurecuts") == "dmps-*-selected.csv"


def test_mode3_mapper_gene_recurrence() -> None:
    derived = apply_modeling_modes_to_validation_dict(
        {"dmp_modeling_mode": "raw_pool", "gene_modeling_mode": "mapper_ranked"}
    )
    assert derived["stability_gene_recurrence_source"] == "mapper"
    prefs = resolve_gene_stability_preferences(derived)
    assert prefs["prefer_mapper_gene_panels"] is True
    assert prefs["gene_recurrence_source"] == "mapper"


def test_mode4_gene_featurecuts_split_ba() -> None:
    cfg = MonteCarloConfig.model_validate(
        _minimal_mc(
            dmp_modeling_mode="raw_pool",
            gene_modeling_mode="featurecuts",
            dmp_featurecuts_target_ba=0.95,
            gene_featurecuts_target_ba=0.90,
            gene_featurecuts_loci_source="raw_pool",
        )
    )
    assert cfg.stability_gene_featurecuts_enabled is True
    assert cfg.stability_featurecuts_enabled is False
    assert cfg.gene_featurecuts_target_ba == 0.90
    assert cfg.stability_gene_featurecuts_dmp_source == "discovery"
    assert cfg.stability_gene_recurrence_source == "classifier"
    prefs = resolve_gene_stability_preferences(cfg.model_dump(mode="python"))
    assert prefs["prefer_classifier_gene_panels"] is True
    assert prefs["gene_recurrence_source"] == "classifier"


def test_gene_featurecuts_overrides_stale_enricher_recurrence_source() -> None:
    derived = apply_modeling_modes_to_validation_dict(
        {
            "dmp_modeling_mode": "raw_pool",
            "gene_modeling_mode": "featurecuts",
            "stability_gene_recurrence_source": "enricher",
        }
    )
    assert derived["stability_gene_recurrence_source"] == "classifier"
    assert infer_gene_recurrence_source(derived) == "classifier"


def test_mode5_from_stable_dmp_panel_loci_source() -> None:
    derived = apply_modeling_modes_to_validation_dict(
        {
            "dmp_modeling_mode": "stable_panel",
            "gene_modeling_mode": "from_stable_dmp_panel",
            "gene_featurecuts_target_ba": 0.95,
        }
    )
    assert derived["gene_featurecuts_loci_source"] == "stable_panel"
    assert derived["stability_gene_featurecuts_dmp_source"] == "stable"
    assert infer_gene_recurrence_source(derived) == "classifier"


def test_legacy_flags_infer_modes() -> None:
    assert infer_dmp_modeling_mode({"stability_featurecuts_enabled": True}) == "featurecuts"
    assert infer_gene_modeling_mode({"stability_gene_featurecuts_enabled": True}) == "featurecuts"


def test_stability_min_balanced_accuracy_rejected_without_featurecuts() -> None:
    import pytest

    from methyl_validation.modeling_modes import (
        validate_stability_min_balanced_accuracy_requires_featurecuts,
    )

    with pytest.raises(ValueError, match="stability_min_balanced_accuracy requires"):
        validate_stability_min_balanced_accuracy_requires_featurecuts(
            {
                "dmp_modeling_mode": "raw_pool",
                "gene_modeling_mode": "none",
                "stability_min_balanced_accuracy": 0.5,
            }
        )

    with pytest.raises(ValueError, match="stability_min_balanced_accuracy requires"):
        MonteCarloConfig.model_validate(
            _minimal_mc(
                dmp_modeling_mode="raw_pool",
                gene_modeling_mode="none",
                stability_min_balanced_accuracy=0.5,
            )
        )

    MonteCarloConfig.model_validate(
        _minimal_mc(
            dmp_modeling_mode="featurecuts",
            gene_modeling_mode="none",
            dmp_featurecuts_target_ba=0.95,
            stability_min_balanced_accuracy=0.5,
        )
    )
    MonteCarloConfig.model_validate(
        _minimal_mc(
            dmp_modeling_mode="raw_pool",
            gene_modeling_mode="featurecuts",
            gene_featurecuts_target_ba=0.90,
            stability_min_balanced_accuracy=0.5,
        )
    )


def test_parse_validation_profile_rejects_min_ba_without_featurecuts() -> None:
    import pytest

    from methyl_validation.config import parse_validation_profile

    with pytest.raises(ValueError, match="stability_min_balanced_accuracy requires"):
        parse_validation_profile(
            {
                "dmp_modeling_mode": "raw_pool",
                "gene_modeling_mode": "none",
                "stability_min_balanced_accuracy": 0.6,
            }
        )
