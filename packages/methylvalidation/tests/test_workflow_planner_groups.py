"""Planner enrichment tests for hierarchical PCa1-5 MC iterations."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from methyl_validation.project_gen import build_group_centroid_scope
from methyl_validation.workflow_planner import ValidationPlanRequest, plan_validation_context


def _minimal_hierarchical_project(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    for name, samples in (
        ("all.csv", ["s1", "s2", "s3", "s4"]),
        ("pca1.csv", ["d1", "d2", "d3", "d4"]),
        ("pca2.csv", ["e1", "e2", "e3", "e4"]),
    ):
        lines = ["sample", *samples]
        (data / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    samples_base = tmp_path / "samples"
    for sample in ["s1", "s2", "s3", "s4", "d1", "d2", "d3", "d4", "e1", "e2", "e3", "e4"]:
        sample_dir = samples_base / sample
        sample_dir.mkdir(parents=True, exist_ok=True)
        (sample_dir / "21-CG.h5").write_bytes(b"h5")

    project = {
        "project_name": "hier_mc_test",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": str(samples_base),
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": [str(data / "all.csv")]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [
                {
                    "label": "PCa",
                    "stages": [
                        {"label": "PCa1", "sample_paths": [str(data / "pca1.csv")]},
                        {"label": "PCa2", "sample_paths": [str(data / "pca2.csv")]},
                    ],
                }
            ],
        },
        "comparisons": [
            {"control_group": "all", "disease_group": "PCa_PCa1", "label": "PCa_PCa1"},
            {"control_group": "all", "disease_group": "PCa_PCa2", "label": "PCa_PCa2"},
        ],
        "chromosomes": ["21"],
        "contexts": ["CG"],
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(project, indent=2), encoding="utf-8")
    profile_path = tmp_path / "hier_mc.profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "pipelineProfile": "hier_mc",
                "actionConfig": {
                    "validation": {
                        "n_iterations": 2,
                        "seed": 7,
                        "train_fraction": 0.5,
                        "cohorts": [
                            {"label": "all", "csv": str(data / "all.csv")},
                            {"label": "PCa_PCa1", "csv": str(data / "pca1.csv")},
                            {"label": "PCa_PCa2", "csv": str(data / "pca2.csv")},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    os.environ["METHYL_PROFILE"] = str(profile_path)
    return path


def test_build_group_centroid_scope_emits_add_remove(tmp_path: Path) -> None:
    project = _minimal_hierarchical_project(tmp_path)
    groups = build_group_centroid_scope(
        base_project_path=project,
        run_dir=tmp_path / "run_0001",
        cohort_labels=["all", "PCa_PCa1", "PCa_PCa2"],
        train_by_label={
            "all": [str((tmp_path / "samples/s1").resolve()), str((tmp_path / "samples/s2").resolve())],
            "PCa_PCa1": [str((tmp_path / "samples/d1").resolve())],
            "PCa_PCa2": [str((tmp_path / "samples/e1").resolve()), str((tmp_path / "samples/e2").resolve())],
        },
        previous_train_by_label={
            "all": [str((tmp_path / "samples/s1").resolve())],
            "PCa_PCa1": [str((tmp_path / "samples/d2").resolve())],
            "PCa_PCa2": [],
        },
    )
    assert len(groups) == 3
    all_group = next(g for g in groups if g.label == "all")
    assert len(all_group.addSamples) == 1
    assert len(all_group.removeSamples) == 0
    pca1 = next(g for g in groups if g.label == "PCa_PCa1")
    assert pca1.addSamples
    assert pca1.removeSamples
    assert all(g.centroidDir for g in groups)


def test_plan_validation_context_hierarchical_groups(tmp_path: Path) -> None:
    project = _minimal_hierarchical_project(tmp_path)
    ctx = plan_validation_context(
        ValidationPlanRequest(
            projectPath=str(project),
            featureIterations=2,
            layout="hierarchical_multiclass",
            overwrite=True,
        )
    )
    iterations = ctx.iterations
    assert len(iterations) == 2
    first = iterations[0].model_dump(mode="json")
    assert "centroidGroups" in first
    assert len(first["centroidGroups"]) == 3
    for grp in first["centroidGroups"]:
        assert "addSamples" in grp and "removeSamples" in grp and "centroidDir" in grp
    second = iterations[1].model_dump(mode="json", by_alias=True)
    assert "centroidGroups" in second
    for grp in second["centroidGroups"]:
        assert grp.get("centroidSeedDir")
