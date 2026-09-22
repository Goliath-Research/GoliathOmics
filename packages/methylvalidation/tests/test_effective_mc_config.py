"""Breaking-change and effective Monte Carlo configuration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from methyl_validation.config import MonteCarloConfig
from methyl_validation.effective_config import build_effective_mc_config, write_effective_mc_config_snapshot
from methyl_validation.mc_config_load import write_mc_config_snapshot


REMOVED_KEYS = sorted(MonteCarloConfig._REMOVED_FLAT_KEYS)


def _base(**extra):
    payload = {
        "samples_base_path": "/tmp/s",
        "cohorts": [
            {"label": "healthy", "csv": "h.csv"},
            {"label": "disease", "csv": "d.csv"},
        ],
        "train_fraction": 0.8,
        "n_iterations": 10,
        "base_project": "p.json",
        "output_base": "/tmp/out",
        "stability_early_stop_enabled": False,
        "backend_profiles": {
            "ecdf": {
                "enabled": True,
                "params": {
                    "feature_mode": "raw_gene",
                    "covariates_path": "/tmp/cell_fractions.csv",
                    "covariate_id_column": "sample_id",
                    "covariate_numeric_columns": [
                        "CD8T",
                        "CD4T",
                        "NK",
                        "Bcell",
                        "Mono",
                        "Neu",
                    ],
                    "covariates_strict_join": True,
                    "covariate_standardize_numeric": True,
                },
            },
            "tabular_sklearn": {"enabled": False, "params": {}},
            "generative_hybrid": {"enabled": False, "params": {}},
        },
    }
    payload.update(extra)
    return payload


def test_removed_keys_absent_from_schema():
    props = set(MonteCarloConfig.model_json_schema().get("properties", {}))
    assert props & set(REMOVED_KEYS) == set()


@pytest.mark.parametrize("key", REMOVED_KEYS)
def test_removed_key_rejected(key: str):
    payload = _base()
    payload[key] = True if key not in {"healthy_csv", "disease_csv"} else "x.csv"
    with pytest.raises(ValidationError, match="Removed legacy Monte Carlo config keys"):
        MonteCarloConfig.model_validate(payload)


def test_canonical_snapshot_round_trip(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(_base())
    path = tmp_path / "mc_config.json"
    write_mc_config_snapshot(cfg, path)
    reloaded = MonteCarloConfig.from_json_file(path)
    assert reloaded.n_iterations == 10
    assert reloaded.backend_profiles.ecdf.enabled is True
    assert path.with_name("mc_config.effective.json").is_file()
    effective = json.loads(path.with_name("mc_config.effective.json").read_text(encoding="utf-8"))
    assert effective["schema"] == "mc_config.effective.v1"
    assert effective["stability"]["early_stopping"] == {"enabled": False}
    ecdf = effective["model_training"]["backends"]["ecdf"]
    assert ecdf["enabled"] is True
    assert ecdf["feature_mode"] == "raw_gene"
    assert ecdf["include_covariates"] is True
    assert ecdf["second_stage_active"] is True
    assert ecdf["covariates"]["strict_join"] is True
    assert effective["model_training"]["backends"]["tabular_sklearn"] == {"enabled": False}


def test_early_stopping_expands_when_enabled():
    cfg = MonteCarloConfig.model_validate(
        _base(
            n_iterations=40,
            stability_early_stop_enabled=True,
            stability_min_iterations=20,
            stability_convergence_window=5,
            stability_convergence_jaccard=0.98,
            stability_convergence_max_size_delta=0.02,
            stability_convergence_patience=3,
        )
    )
    early = build_effective_mc_config(cfg)["stability"]["early_stopping"]
    assert early["enabled"] is True
    assert early["minimum_qualifying_iterations"] == 20
    assert early["comparison_window"] == 5
    assert early["minimum_jaccard_similarity"] == 0.98
    assert early["maximum_relative_size_change"] == 0.02
    assert early["required_consecutive_passes"] == 3


def test_write_effective_snapshot_path(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(_base())
    out = write_effective_mc_config_snapshot(cfg, tmp_path / "mc_config.effective.json")
    assert out.is_file()


def test_effective_hybrid_flag_inert_without_stacker():
    """Hybrid include is not effective unless the second-stage stacker will run."""
    payload = _base()
    ecdf_params = payload["backend_profiles"]["ecdf"]["params"]
    ecdf_params.pop("covariates_path", None)
    ecdf_params.pop("covariate_numeric_columns", None)
    ecdf_params["ecdf_second_stage_enabled"] = False
    ecdf_params["ecdf_second_stage_include_observed_hybrid"] = True
    cfg = MonteCarloConfig.model_validate(payload)
    ecdf = build_effective_mc_config(cfg)["model_training"]["backends"]["ecdf"]
    assert ecdf["second_stage_active"] is False
    assert ecdf["include_observed_hybrid"] is False
    assert ecdf["ecdf_second_stage_include_observed_hybrid"] is True
    assert "observed_feature_quantiles" not in ecdf


def test_effective_hybrid_flag_active_with_stacker():
    payload = _base()
    ecdf_params = payload["backend_profiles"]["ecdf"]["params"]
    ecdf_params["ecdf_second_stage_enabled"] = True
    ecdf_params["ecdf_second_stage_include_observed_hybrid"] = True
    cfg = MonteCarloConfig.model_validate(payload)
    ecdf = build_effective_mc_config(cfg)["model_training"]["backends"]["ecdf"]
    assert ecdf["second_stage_active"] is True
    assert ecdf["include_observed_hybrid"] is True
    assert "observed_feature_quantiles" in ecdf
