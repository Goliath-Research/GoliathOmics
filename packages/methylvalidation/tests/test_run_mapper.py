from __future__ import annotations

import json
from pathlib import Path

from methyl_validation import pipeline_runner
from methyl_validation.config import MonteCarloConfig
from methyl_validation.mc_manifest import DISCOVERY_DMP_CSV_PATTERN, SELECTED_DMP_CSV_PATTERN


def _minimal_mc(**extra) -> MonteCarloConfig:
    return MonteCarloConfig.model_validate(
        {
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
    )


def test_run_mapper_does_not_pass_per_cancer_group(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str]] = {}

    def _fake_run_cmd(cmd):
        captured["cmd"] = list(cmd)
        return 0, "ok", ""

    monkeypatch.setattr(pipeline_runner, "run_cmd", _fake_run_cmd)
    monkeypatch.setattr(pipeline_runner, "_mapper_override_dict", lambda _p, **_kw: {})

    project_json = tmp_path / "run_0001" / "project.json"
    project_json.parent.mkdir(parents=True)
    project_json.write_text("{}", encoding="utf-8")

    rc, out, err = pipeline_runner.run_mapper(project_json, per_cancer_group=True)
    assert rc == 0
    assert captured["cmd"] == ["methyl-mapper", "--project", str(project_json)]
    assert "--per-cancer-group" not in captured["cmd"]


def test_run_mapper_passes_mapper_step_override_from_resolved_config(monkeypatch, tmp_path: Path):
    captured: dict[str, list[str]] = {}

    def _fake_run_cmd(cmd):
        captured["cmd"] = list(cmd)
        return 0, "ok", ""

    monkeypatch.setattr(pipeline_runner, "run_cmd", _fake_run_cmd)

    run_dir = tmp_path / "run_0001"
    run_dir.mkdir(parents=True)
    project_json = run_dir / "project.json"
    project_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        pipeline_runner,
        "_mapper_override_dict",
        lambda _p, **_kw: {"csv_filename_pattern": "dmps-*-classifier-extended.csv"},
    )

    pipeline_runner.run_mapper(project_json)
    assert captured["cmd"][0:3] == ["methyl-mapper", "--project", str(project_json)]
    assert "--step-override" in captured["cmd"]


def test_mapper_override_uses_in_memory_mc_config_without_queue_snapshot(tmp_path: Path):
    """Local methyl-validation / hyperparam search: no queue/mc_config.json."""
    run_dir = tmp_path / "monte_carlo_runs" / "run_0001"
    run_dir.mkdir(parents=True)
    project_json = run_dir / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    assert not (tmp_path / "monte_carlo_runs" / "queue" / "mc_config.json").exists()

    cfg = _minimal_mc(
        stability_gene_featurecuts_enabled=True,
        dmp_modeling_mode="raw_pool",
        gene_featurecuts_loci_source="raw_pool",
    )
    override = pipeline_runner._mapper_override_dict(project_json, config=cfg)
    assert override.get("csv_filename_pattern") == DISCOVERY_DMP_CSV_PATTERN


def test_mapper_override_selects_selected_csv_for_featurecuts_loci(tmp_path: Path):
    run_dir = tmp_path / "monte_carlo_runs" / "run_0001"
    run_dir.mkdir(parents=True)
    project_json = run_dir / "project.json"
    project_json.write_text("{}", encoding="utf-8")

    cfg = _minimal_mc(
        stability_gene_featurecuts_enabled=True,
        dmp_modeling_mode="featurecuts",
        gene_featurecuts_loci_source="featurecuts_selected",
    )
    override = pipeline_runner._mapper_override_dict(project_json, config=cfg)
    assert override.get("csv_filename_pattern") == SELECTED_DMP_CSV_PATTERN


def test_run_mapper_passes_config_into_override(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def _fake_run_cmd(cmd):
        captured["cmd"] = list(cmd)
        return 0, "ok", ""

    def _fake_override(project_json, *, config=None):
        captured["config"] = config
        return {"csv_filename_pattern": DISCOVERY_DMP_CSV_PATTERN}

    monkeypatch.setattr(pipeline_runner, "run_cmd", _fake_run_cmd)
    monkeypatch.setattr(pipeline_runner, "_mapper_override_dict", _fake_override)

    project_json = tmp_path / "run_0001" / "project.json"
    project_json.parent.mkdir(parents=True)
    project_json.write_text("{}", encoding="utf-8")
    cfg = _minimal_mc(stability_gene_featurecuts_enabled=True)

    pipeline_runner.run_mapper(project_json, config=cfg)
    assert captured["config"] is cfg
    assert "--step-override" in captured["cmd"]
    ov_path = Path(captured["cmd"][captured["cmd"].index("--step-override") + 1])
    assert json.loads(ov_path.read_text(encoding="utf-8"))["csv_filename_pattern"] == DISCOVERY_DMP_CSV_PATTERN


def test_mapper_default_pattern_is_discovery_not_biological_sorted():
    from methyl_mapper.project_resolver import (
        DMP_CSV_PATTERN_BIOLOGICAL,
        DMP_CSV_PATTERN_DISCOVERY,
    )

    assert DMP_CSV_PATTERN_DISCOVERY == "dmps-*-discovery.csv"
    assert "biological-sorted" not in DMP_CSV_PATTERN_BIOLOGICAL
