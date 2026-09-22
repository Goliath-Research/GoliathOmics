from __future__ import annotations

import pytest

from methyl_validation.cli import _resolve_model_mc_backends
from methyl_validation.config import MonteCarloConfig
from methyl_validation.mc_config_load import _update_backend_params
from methyl_validation.utils.migrate_backend_config import (
    LEGACY_BACKEND_KEYS,
    _migrate_validation_section,
    merge_legacy_validation_keys_into_backend_profiles,
)


def test_migrator_recognizes_every_model_legacy_key() -> None:
    """The migrator must strip every key the model rejects.

    If a key is in MonteCarloConfig._LEGACY_BACKEND_KEYS but not in the migrator's
    LEGACY_BACKEND_KEYS, snapshots carrying it are rejected but never migrated
    (the mapper_gene_columns reload trap).
    """
    missing = set(MonteCarloConfig._LEGACY_BACKEND_KEYS) - set(LEGACY_BACKEND_KEYS)
    assert missing == set(), f"migrator does not strip model legacy keys: {sorted(missing)}"


def test_merge_strips_mapper_gene_columns() -> None:
    validation = {"mapper_gene_columns": ["geneA", "geneB"], "stability_dmp_freq": 0.7}
    migrated, moved = merge_legacy_validation_keys_into_backend_profiles(validation)
    assert "mapper_gene_columns" in moved
    assert "mapper_gene_columns" not in migrated


def test_dump_clean_json_omits_legacy_keys_and_round_trips() -> None:
    """Canonical serialization has no removed flat keys and reloads cleanly."""
    import json

    cfg = MonteCarloConfig.model_validate(_base_payload())
    clean = json.loads(cfg.dump_clean_json())
    assert set(clean) & set(MonteCarloConfig._REMOVED_FLAT_KEYS) == set()
    schema_props = set(MonteCarloConfig.model_json_schema().get("properties", {}))
    assert schema_props & set(MonteCarloConfig._REMOVED_FLAT_KEYS) == set()
    reloaded = MonteCarloConfig.model_validate(clean)
    assert reloaded.train_fraction == cfg.train_fraction
    assert reloaded.n_iterations == cfg.n_iterations


def _base_payload() -> dict:
    return {
        "samples_base_path": "/tmp",
        "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
        "train_fraction": 0.8,
        "n_iterations": 2,
        "base_project": "/tmp/project.json",
        "output_base": "/tmp",
        "backend_profiles": {
            "ecdf": {"enabled": False, "params": {}},
            "tabular_sklearn": {"enabled": True, "params": {}},
            "generative_hybrid": {"enabled": True, "params": {}},
        },
    }


def test_model_mc_all_uses_only_enabled_profiles():
    cfg = MonteCarloConfig.model_validate(_base_payload())
    backends = _resolve_model_mc_backends(cfg, run_all=True)
    assert backends == ["tabular_sklearn", "generative_hybrid"]


def test_model_mc_all_fails_when_no_enabled_profiles():
    payload = _base_payload()
    payload["backend_profiles"]["tabular_sklearn"]["enabled"] = False
    payload["backend_profiles"]["generative_hybrid"]["enabled"] = False
    with pytest.raises(ValueError, match="At least one backend profile must have enabled=true"):
        MonteCarloConfig.model_validate(payload)


def test_merge_legacy_keys_preserves_backend_enabled_flags():
    validation = {
        "train_fraction": 0.8,
        "gene_scored_min_support_n": 5,
        "backend_profiles": {
            "ecdf": {"enabled": True, "params": {"feature_family_set": "dmp_scored"}},
            "tabular_sklearn": {"enabled": True, "params": {"feature_family_set": "dmp_scored+gene_scored"}},
            "generative_hybrid": {"enabled": True, "params": {"feature_family_set": "dmp_scored"}},
        },
    }
    migrated, moved = merge_legacy_validation_keys_into_backend_profiles(validation)
    assert moved == ["gene_scored_min_support_n"]
    assert "gene_scored_min_support_n" not in migrated
    assert migrated["backend_profiles"]["ecdf"]["enabled"] is True
    assert migrated["backend_profiles"]["tabular_sklearn"]["enabled"] is True
    assert migrated["backend_profiles"]["generative_hybrid"]["enabled"] is True
    assert migrated["backend_profiles"]["tabular_sklearn"]["params"]["gene_scored_min_support_n"] == 5
    assert migrated["backend_profiles"]["ecdf"]["params"]["feature_family_set"] == "dmp_scored"
    assert (
        migrated["backend_profiles"]["tabular_sklearn"]["params"]["feature_family_set"]
        == "dmp_scored+gene_scored"
    )
    assert migrated["backend_profiles"]["generative_hybrid"]["params"]["feature_family_set"] == "dmp_scored"


def test_migration_moves_legacy_keys_into_backend_profiles():
    legacy = {
        "model_backend": "generative_hybrid",
        "tabular_max_dmps": 7000,
        "generative_latent_dim": 24,
        "generative_epochs": 99,
    }
    migrated, summary = _migrate_validation_section(legacy)
    assert "model_backend" not in migrated
    assert migrated["backend_profiles"]["generative_hybrid"]["enabled"] is True
    assert migrated["backend_profiles"]["ecdf"]["enabled"] is False
    assert migrated["backend_profiles"]["tabular_sklearn"]["params"]["tabular_max_dmps"] == 7000
    assert migrated["backend_profiles"]["generative_hybrid"]["params"]["generative_latent_dim"] == 24
    assert summary["active_backend"] == "generative_hybrid"


def test_with_backend_selection_resyncs_runtime_fields():
    payload = _base_payload()
    payload["backend_profiles"]["ecdf"]["enabled"] = True
    payload["backend_profiles"]["generative_hybrid"]["enabled"] = False
    payload["backend_profiles"]["ecdf"]["params"] = {
        "feature_mode": "raw_dmp",
        "tabular_max_dmps": 5000,
    }
    payload["backend_profiles"]["tabular_sklearn"]["params"] = {
        "feature_mode": "observed_hybrid",
        "tabular_max_dmps": 7777,
    }
    cfg = MonteCarloConfig.model_validate(payload)
    switched = cfg.with_backend_selection("tabular_sklearn")
    assert switched.model_backend == "tabular_sklearn"
    assert switched.feature_mode == "observed_hybrid"
    assert switched.tabular_max_dmps == 7777


def _cell_fraction_composition_group(*, name: str = "cell_fractions") -> dict:
    return {
        "name": name,
        "columns": ["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"],
        "reference": "Neu",
        "pseudocount": 1e-6,
        "standardize": True,
    }


def test_composition_groups_resolve_from_active_backend_not_ecdf_only():
    """ALR groups live per-backend; tabular must not read empty ecdf.params."""
    tabular_group = _cell_fraction_composition_group(name="tabular_cf")
    generative_group = _cell_fraction_composition_group(name="generative_cf")
    ecdf_group = _cell_fraction_composition_group(name="ecdf_cf")

    payload = _base_payload()
    payload["backend_profiles"]["ecdf"] = {
        "enabled": True,
        "params": {"covariate_composition_groups": [ecdf_group]},
    }
    payload["backend_profiles"]["tabular_sklearn"] = {
        "enabled": True,
        "params": {
            "tabular_methods": [{"method": "logistic_regression", "params": {}}],
            "covariate_composition_groups": [tabular_group],
        },
    }
    payload["backend_profiles"]["generative_hybrid"] = {
        "enabled": True,
        "params": {"covariate_composition_groups": [generative_group]},
    }
    cfg = MonteCarloConfig.model_validate(payload)

    tab = cfg.with_backend_selection("tabular_sklearn")
    assert tab.covariate_composition_groups is not None
    assert len(tab.covariate_composition_groups) == 1
    assert tab.covariate_composition_groups[0].name == "tabular_cf"
    assert tab.covariate_composition_groups[0].reference == "Neu"

    gen = cfg.with_backend_selection("generative_hybrid")
    assert gen.covariate_composition_groups is not None
    assert gen.covariate_composition_groups[0].name == "generative_cf"

    ecdf = cfg.with_backend_selection("ecdf")
    assert ecdf.covariate_composition_groups is not None
    assert ecdf.covariate_composition_groups[0].name == "ecdf_cf"


def test_tabular_composition_groups_not_lost_when_ecdf_omits_them():
    tabular_group = _cell_fraction_composition_group()
    payload = _base_payload()
    payload["backend_profiles"]["ecdf"] = {"enabled": False, "params": {}}
    payload["backend_profiles"]["tabular_sklearn"] = {
        "enabled": True,
        "params": {
            "tabular_methods": [{"method": "random_forest", "params": {}}],
            "covariate_composition_groups": [tabular_group],
        },
    }
    payload["backend_profiles"]["generative_hybrid"]["enabled"] = False
    cfg = MonteCarloConfig.model_validate(payload)
    tab = cfg.with_backend_selection("tabular_sklearn")
    groups = tab.covariate_composition_groups
    assert groups is not None and len(groups) == 1
    assert groups[0].columns == ["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"]
    assert groups[0].reference == "Neu"


def test_update_backend_params_resyncs_runtime_fields():
    payload = _base_payload()
    payload["backend_profiles"]["ecdf"]["enabled"] = False
    payload["backend_profiles"]["tabular_sklearn"]["enabled"] = False
    payload["backend_profiles"]["generative_hybrid"]["enabled"] = True
    cfg = MonteCarloConfig.model_validate(payload)
    updated = _update_backend_params(
        cfg,
        "generative_hybrid",
        {"generative_latent_dim": 31, "covariates_path": "/tmp/covariates.csv"},
    )
    assert updated.backend_profiles.generative_hybrid.params.generative_latent_dim == 31
    assert updated.generative_latent_dim == 31
    assert updated.covariates_path == "/tmp/covariates.csv"


def test_gene_feature_loading_is_shared_and_migrated():
    legacy = {
        "model_backend": "tabular_sklearn",
        "gene_feature_loading": "range",
    }
    migrated, _summary = _migrate_validation_section(legacy)
    assert migrated["backend_profiles"]["ecdf"]["params"]["gene_feature_loading"] == "range"
    assert migrated["backend_profiles"]["tabular_sklearn"]["params"]["gene_feature_loading"] == "range"
    cfg = MonteCarloConfig.model_validate(_base_payload() | {"backend_profiles": migrated["backend_profiles"]})
    switched = cfg.with_backend_selection("tabular_sklearn")
    assert switched.gene_feature_loading == "range"


def test_stability_early_stop_requires_min_iterations_within_budget():
    payload = _base_payload()
    payload["n_iterations"] = 5
    payload["stability_early_stop_enabled"] = True
    payload["stability_min_iterations"] = 6
    with pytest.raises(ValueError, match="stability_min_iterations must be <= n_iterations"):
        MonteCarloConfig.model_validate(payload)


def test_stability_early_stop_requires_window_smaller_than_min_iterations():
    payload = _base_payload()
    payload["n_iterations"] = 10
    payload["stability_early_stop_enabled"] = True
    payload["stability_min_iterations"] = 5
    payload["stability_convergence_window"] = 5
    with pytest.raises(ValueError, match="stability_convergence_window must be smaller"):
        MonteCarloConfig.model_validate(payload)

