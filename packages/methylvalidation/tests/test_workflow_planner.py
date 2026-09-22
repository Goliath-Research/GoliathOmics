"""Tests for ValidationPipeline Monte Carlo planner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from methyl_validation.config import parse_validation_profile
from methyl_validation.workflow_planner import ValidationPlanRequest, plan_validation_context


def _write_minimal_binary_project(tmp_path: Path) -> Path:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "H1").mkdir()
    (samples / "H2").mkdir()
    (samples / "D1").mkdir()
    (samples / "D2").mkdir()
    healthy = tmp_path / "healthy.csv"
    disease = tmp_path / "disease.csv"
    healthy.write_text("sample\nH1\nH2\n", encoding="utf-8")
    disease.write_text("sample\nD1\nD2\n", encoding="utf-8")
    profile_path = tmp_path / "test_mc.profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "pipelineProfile": "test_mc",
                "actionConfig": {
                    "validation": {
                        "n_iterations": 2,
                        "train_fraction": 0.5,
                        "seed": 7,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    os.environ["METHYL_PROFILE"] = str(profile_path)
    payload = {
        "project_name": "demo_mc",
        "output_base": str(tmp_path / "work"),
        "samples_base_path": str(samples),
        "controls": {
            "label": "healthy",
            "groups": [{"label": "healthy", "sample_paths": [str(healthy)]}],
        },
        "diseases": {
            "label": "disease",
            "groups": [{"label": "disease", "sample_paths": [str(disease)]}],
        },
        "comparisons": [
            {
                "label": "healthy_vs_disease",
                "control_group": "healthy",
                "disease_group": "disease",
            }
        ],
    }
    project_json = tmp_path / "project.json"
    project_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return project_json


def test_plan_validation_context_materializes_iterations(tmp_path: Path) -> None:
    project_json = _write_minimal_binary_project(tmp_path)
    context = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=2,
            qualityIterations=0,
        )
    )
    assert len(context.iterations) == 2
    first = context.iterations[0].model_dump(mode="json", by_alias=True)
    assert first["phase"] == "feature"
    assert first["runId"] == "feature_run_0001"
    assert first["$type"] == "StratifiedCohortDraw"
    assert first["groups"]
    assert first["groups"][0]["$type"] == "MethylGroup"
    assert "taskConfig" in first
    run_project = Path(first["projectPath"])
    assert run_project.is_file()
    assert context.projectPath.endswith("demo_mc")


def test_plan_validation_context_excludes_locked_external_holdout(tmp_path: Path) -> None:
    project_json = _write_minimal_binary_project(tmp_path)
    project = json.loads(project_json.read_text(encoding="utf-8"))
    samples = tmp_path / "samples"
    for sample_id in ("H3", "H4", "D3", "D4"):
        (samples / sample_id).mkdir()
    (tmp_path / "healthy.csv").write_text(
        "sample\nH1\nH2\nH3\nH4\n",
        encoding="utf-8",
    )
    (tmp_path / "disease.csv").write_text(
        "sample\nD1\nD2\nD3\nD4\n",
        encoding="utf-8",
    )
    project["validation_partitions"] = {
        "development_train": [],
        "internal_validation": [],
        "locked_test": [str(samples / "H4"), str(samples / "D4")],
        "pivotal_validation": [],
        "post_market_monitoring": [],
        "independence_keys": ["sample_id"],
    }
    project_json.write_text(json.dumps(project, indent=2), encoding="utf-8")

    context = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
        )
    )

    run_dir = Path(context.iterations[0].runDir)
    split_text = "\n".join(
        (run_dir / name).read_text(encoding="utf-8")
        for name in (
            "train_control.csv",
            "train_disease.csv",
            "test_control.csv",
            "test_disease.csv",
        )
    )
    assert "H4" not in split_text
    assert "D4" not in split_text


def test_plan_validation_context_requires_validation_block(tmp_path: Path) -> None:
    project_json = _write_minimal_binary_project(tmp_path)
    os.environ.pop("METHYL_PROFILE", None)
    with pytest.raises(Exception, match="validation action config|Field required"):
        plan_validation_context(
            ValidationPlanRequest(
                projectPath=str(project_json),
                featureIterations=1,
            )
        )


def test_plan_validation_context_profile_overrides_exclude_legacy_defaults(tmp_path: Path) -> None:
    """Workflow resolvedConfig must not re-expand flat backend defaults on dump."""
    project_json = _write_minimal_binary_project(tmp_path)
    resolved = {
        "train_fraction": 0.8,
        "n_iterations": 1,
        "seed": 42,
        "run_stability": True,
        "backend_profiles": {
            "ecdf": {
                "enabled": True,
                "params": {
                    "feature_mode": "raw_gene",
                    "feature_family_set": "gene_scored",
                },
            }
        },
    }
    profile = parse_validation_profile(resolved)
    assert profile is not None
    context = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
        ),
        profile_overrides=profile,
    )
    assert len(context.iterations) == 1


def test_plan_validation_context_regenerates_legacy_step_config_run_project(tmp_path: Path) -> None:
    """Pre-migration MC run dirs with embedded step_config must be rewritten."""
    project_json = _write_minimal_binary_project(tmp_path)
    work = tmp_path / "work"
    runs_root = work / "demo_mc" / "monte_carlo_runs" / "run_0001"
    runs_root.mkdir(parents=True)
    legacy = runs_root / "project.json"
    legacy.write_text(
        json.dumps(
            {
                "project_name": "run_0001",
                "output_base": str(work / "demo_mc" / "monte_carlo_runs"),
                "step_config": {"detection": {"alpha": 0.05}},
            }
        ),
        encoding="utf-8",
    )
    context = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
        )
    )
    run_project = Path(context.iterations[0].projectPath)
    assert run_project == legacy.resolve()
    payload = json.loads(run_project.read_text(encoding="utf-8"))
    assert "step_config" not in payload
    assert payload.get("controls")
    assert payload.get("diseases")


def test_plan_validation_context_resume_includes_monte_carlo_runs_root(tmp_path: Path) -> None:
    project_json = _write_minimal_binary_project(tmp_path)
    plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
        )
    )
    resumed = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
            overwrite=False,
        )
    )
    task_config = resumed.iterations[0].model_dump(mode="json")["taskConfig"]
    assert "monteCarloRunsRoot" in task_config
    assert Path(task_config["monteCarloRunsRoot"]).is_dir()


def test_plan_validation_context_binary_centroid_groups(tmp_path: Path) -> None:
    """Binary MC iterations must expose centroidGroups and run-scoped detect dirs."""
    project_json = _write_minimal_binary_project(tmp_path)
    context = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=2,
            qualityIterations=0,
            overwrite=True,
        )
    )
    assert len(context.centroidSeedGroups) == 2
    first = context.iterations[0].model_dump(mode="json", by_alias=True)
    assert "centroidGroups" in first
    assert len(first["centroidGroups"]) == 2
    for grp in first["centroidGroups"]:
        assert "addSamples" in grp and "removeSamples" in grp and "centroidDir" in grp
        assert grp.get("centroidSeedDir")
    run_project_path = Path(first["projectPath"])
    run_root = run_project_path.parent
    for grp in first["centroidGroups"]:
        assert grp["centroidDir"].startswith(str(run_root / "centroids"))
        assert "/controls/all/all" not in grp["centroidDir"]
    assert first["centroid1Dir"] == str(run_root / "centroids" / "controls" / "healthy" / "healthy")
    assert first["centroid2Dir"] == str(run_root / "centroids" / "diseases" / "disease" / "disease")
    assert first["detectOutDir"] == str(run_root / "detections" / "healthy" / "disease")
    second = context.iterations[1].model_dump(mode="json", by_alias=True)
    assert second.get("previousRunDir") is None
    assert "centroidGroups" in second


def test_plan_validation_context_binary_resume_rehydrates_centroid_groups(tmp_path: Path) -> None:
    project_json = _write_minimal_binary_project(tmp_path)
    plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
        )
    )
    resumed = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project_json),
            featureIterations=1,
            qualityIterations=0,
            overwrite=False,
        )
    )
    first = resumed.iterations[0].model_dump(mode="json")
    assert "centroidGroups" in first
    assert len(first["centroidGroups"]) == 2
    assert first["centroid1Dir"]
    assert first["centroid2Dir"]
    assert first["detectOutDir"]
