"""Tests for training analyte consistency checks."""

import json
import os
from argparse import Namespace
from pathlib import Path

from methyl_validation.analyte_guard import (
    check_training_analyte_match,
    effective_training_analyte,
    normalize_analyte,
)
from methyl_validation.mc_config_load import load_monte_carlo_config


def test_normalize_plasma_to_cfdna():
    assert normalize_analyte("plasma") == "cfdna"
    assert normalize_analyte("buffy_coat") == "buffy_coat"


def test_effective_training_analyte_prefers_model_field():
    reg = {"primary_analyte": "cfdna", "model_training_analyte": "buffy_coat"}
    assert effective_training_analyte(reg) == "buffy_coat"


def test_check_training_analyte_mismatch(tmp_path: Path):
    prod = tmp_path / "production"
    prod.mkdir()
    (prod / "locked_model_spec.json").write_text(
        json.dumps({"regulatory": {"model_training_analyte": "buffy_coat"}}),
        encoding="utf-8",
    )
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "t",
                "output_base": str(tmp_path),
                "group1": {"label": "a", "sample_paths": []},
                "group2": {"label": "b", "sample_paths": []},
                "regulatory": {
                    "primary_analyte": "cfdna",
                    "model_training_analyte": "cfdna",
                },
            }
        ),
        encoding="utf-8",
    )
    ok, msg = check_training_analyte_match(
        project_json=project,
        production_dir=prod,
    )
    assert ok is False
    assert "mismatch" in msg


def test_load_monte_carlo_config_propagates_project_regulatory(tmp_path: Path) -> None:
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    profile_path = tmp_path / "mc.profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "pipelineProfile": "mc_test",
                "actionConfig": {
                    "validation": {
                        "train_fraction": 0.6,
                        "n_iterations": 2,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    os.environ["METHYL_PROFILE"] = str(profile_path)
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "reg_test",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path),
                "groups": [
                    {"label": "healthy", "sample_paths": [str(h)]},
                    {"label": "disease", "sample_paths": [str(d)]},
                ],
                "regulatory": {
                    "primary_analyte": "buffy_coat",
                    "model_training_analyte": "buffy_coat",
                },
            }
        ),
        encoding="utf-8",
    )
    config, project_mode = load_monte_carlo_config(Namespace(project=str(project), config=None), None)
    assert project_mode is True
    assert config.regulatory.primary_analyte == "buffy_coat"
    assert config.regulatory.model_training_analyte == "buffy_coat"
