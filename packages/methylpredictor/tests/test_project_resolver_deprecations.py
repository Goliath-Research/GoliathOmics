"""Deprecation coverage for predictor project resolver."""

import json
from pathlib import Path

import pytest

from methyl_predictor.project_resolver import resolve_predictor_config


def test_resolve_predictor_config_warns_on_validator_alias(tmp_path: Path, monkeypatch) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "PredictorAlias",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path / "samples"),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "healthy", "sample_paths": ["c1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [{"label": "pca", "sample_paths": ["d1"]}],
                },
                "comparisons": [{"control_group": "healthy", "disease_group": "pca"}],
            }
        ),
        encoding="utf-8",
    )
    # Legacy 'validator' action key (now supplied via site actionConfig) must still
    # resolve with a deprecation warning steering callers to 'predictor'.
    site = tmp_path / "methyl_site.json"
    site.write_text(json.dumps({"actionConfig": {"validator": {"debug": True}}}), encoding="utf-8")
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))

    with pytest.warns(DeprecationWarning, match="step_config.validator"):
        cfg = resolve_predictor_config(project_path)

    assert cfg.debug is True

