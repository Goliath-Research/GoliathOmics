from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_predictor.project_resolver import resolve_predictor_config, resolve_predictor_config_per_comparison


def _apply_predictor_site(tmp_path: Path, predictor_cfg: dict, monkeypatch) -> None:
    """Feed predictor config via a site manifest (replaces study-manifest step_config)."""
    site = tmp_path / "methyl_site.json"
    site.write_text(json.dumps({"actionConfig": {"predictor": predictor_cfg}}), encoding="utf-8")
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))


def _write_binary_project(tmp_path: Path, predictor_cfg: dict, monkeypatch) -> Path:
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "BinaryCompat",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path / "samples"),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "healthy", "sample_paths": ["C_TRAIN_1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [{"label": "pca", "sample_paths": ["D_TRAIN_1"]}],
                },
                "comparisons": [{"control_group": "healthy", "disease_group": "pca"}],
            }
        ),
        encoding="utf-8",
    )
    _apply_predictor_site(tmp_path, predictor_cfg, monkeypatch)
    return project_path


def _write_comparison_multiclass_project(tmp_path: Path, predictor_cfg: dict, monkeypatch) -> Path:
    project_path = tmp_path / "project_multiclass_like.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "ComparisonMulticlassCompat",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path / "samples"),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "all", "sample_paths": ["C_TRAIN_1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "pca1", "sample_paths": ["D1_TRAIN_1"]},
                                {"label": "pca2", "sample_paths": ["D2_TRAIN_1"]},
                                {"label": "pca3", "sample_paths": ["D3_TRAIN_1"]},
                                {"label": "pca4", "sample_paths": ["D4_TRAIN_1"]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            }
        ),
        encoding="utf-8",
    )
    _apply_predictor_site(tmp_path, predictor_cfg, monkeypatch)
    return project_path


def test_binary_resolver_accepts_group_paths_train_holdout(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "train_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/TR_C1", "/x/TR_C2"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/TR_D1"]},
            ],
            "holdout_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/HO_C1"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/HO_D1", "/x/HO_D2"]},
            ],
        },
    )
    cfg = resolve_predictor_config(project_path)
    assert cfg.test_control_paths == []
    assert cfg.test_disease_paths == []
    assert cfg.train_control_paths == ["/x/TR_C1", "/x/TR_C2"]
    assert cfg.train_disease_paths == ["/x/TR_D1"]
    assert cfg.holdout_control_paths == ["/x/HO_C1"]
    assert cfg.holdout_disease_paths == ["/x/HO_D1", "/x/HO_D2"]


def test_binary_resolver_accepts_test_group_paths(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/TE_C1", "/x/TE_C2"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/TE_D1"]},
            ],
        },
    )
    cfg = resolve_predictor_config(project_path)
    assert cfg.test_control_paths == ["/x/TE_C1", "/x/TE_C2"]
    assert cfg.test_disease_paths == ["/x/TE_D1"]
    assert cfg.train_control_paths == []
    assert cfg.train_disease_paths == []
    assert cfg.holdout_control_paths == []
    assert cfg.holdout_disease_paths == []
    assert [row["evaluation_split"] for row in cfg.sample_lineage] == ["test", "test", "test"]


def test_binary_resolver_prefers_test_group_paths_over_training_cohorts(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/TE_C1"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/TE_D1"]},
            ],
        },
    )
    cfg = resolve_predictor_config(project_path)
    assert cfg.test_control_paths == ["/x/TE_C1"]
    assert cfg.test_disease_paths == ["/x/TE_D1"]
    assert "C_TRAIN_1" not in cfg.test_control_paths
    assert "D_TRAIN_1" not in cfg.test_disease_paths


def test_binary_per_comparison_resolver_accepts_test_group_paths(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/TE_C1"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/TE_D1", "/x/TE_D2"]},
            ],
        },
    )
    [(cfg, comparison_label)] = resolve_predictor_config_per_comparison(project_path)
    assert comparison_label == "pca"
    assert cfg.test_control_paths == ["/x/TE_C1"]
    assert cfg.test_disease_paths == ["/x/TE_D1", "/x/TE_D2"]
    assert cfg.holdout_control_paths == []
    assert cfg.holdout_disease_paths == []


def test_comparison_multiclass_resolver_accepts_multiclass_test_group_paths(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_comparison_multiclass_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "all", "class_index": 0, "paths": ["/x/TE_C1"]},
                {"label": "pca_pca1", "class_index": 1, "paths": ["/x/TE_D1"]},
                {"label": "pca_pca2", "class_index": 2, "paths": ["/x/TE_D2"]},
                {"label": "pca_pca3", "class_index": 3, "paths": ["/x/TE_D3"]},
                {"label": "pca_pca4", "class_index": 4, "paths": ["/x/TE_D4"]},
            ],
        },
    )
    cfg = resolve_predictor_config(project_path)
    assert cfg.test_control_paths == []
    assert cfg.test_disease_paths == []
    assert cfg.test_group_paths is not None
    assert [int(row["class_index"]) for row in cfg.test_group_paths] == [0, 1, 2, 3, 4]
    assert [row["evaluation_split"] for row in cfg.sample_lineage] == ["test"] * 5


def test_binary_resolver_rejects_test_group_paths_with_non_binary_class_index(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/TE_C1"]},
                {"label": "other", "class_index": 2, "paths": ["/x/TE_X1"]},
            ]
        },
    )
    with pytest.raises(ValueError, match="class_index 0/1"):
        resolve_predictor_config(project_path)


def test_binary_resolver_rejects_test_group_paths_missing_class_index(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "test_group_paths": [
                {"label": "healthy", "paths": ["/x/TE_C1"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/TE_D1"]},
            ]
        },
    )
    with pytest.raises(ValueError, match="requires explicit class_index"):
        resolve_predictor_config(project_path)


def test_binary_resolver_rejects_group_paths_with_non_binary_class_index(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "holdout_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": ["/x/HO_C1"]},
                {"label": "other", "class_index": 2, "paths": ["/x/HO_X1"]},
            ]
        },
    )
    with pytest.raises(ValueError, match="class_index 0/1"):
        resolve_predictor_config(project_path)


def test_binary_resolver_rejects_group_paths_missing_class_index(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "holdout_group_paths": [
                {"label": "healthy", "paths": ["/x/HO_C1"]},
                {"label": "pca", "class_index": 1, "paths": ["/x/HO_D1"]},
            ]
        },
    )
    with pytest.raises(ValueError, match="requires explicit class_index"):
        resolve_predictor_config(project_path)


def test_binary_resolver_rejects_empty_holdout_group_paths(tmp_path: Path, monkeypatch) -> None:
    project_path = _write_binary_project(
        tmp_path,
        monkeypatch=monkeypatch,
        predictor_cfg={
            "holdout_group_paths": [
                {"label": "healthy", "class_index": 0, "paths": []},
                {"label": "pca", "class_index": 1, "paths": []},
            ]
        },
    )
    with pytest.raises(ValueError, match="contains no sample paths"):
        resolve_predictor_config(project_path)
