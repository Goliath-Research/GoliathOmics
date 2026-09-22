from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_validation.eval_split_resolver import (
    assert_model_mc_train_partition,
    resolve_eval_paths_and_labels,
)


class _Project:
    def __init__(self, groups):
        self._groups = groups

    def get_resolved_groups(self):
        return self._groups


def test_model_mc_test_partition_is_explicit_and_disjoint(tmp_path: Path) -> None:
    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    (tmp_path / "test_groups.json").write_text(
        json.dumps(
            [
                {"label": "control", "class_index": 0, "paths": [str(tmp_path / "T0")]},
                {"label": "disease", "class_index": 1, "paths": [str(tmp_path / "T1")]},
            ]
        ),
        encoding="utf-8",
    )

    paths, labels = resolve_eval_paths_and_labels(
        project_json,
        ["control", "disease"],
        project_loader=lambda _path: _Project(
            [
                ("control", [str(tmp_path / "S0")]),
                ("disease", [str(tmp_path / "S1")]),
            ]
        ),
        evaluation_partition="test",
    )

    assert paths == [str(tmp_path / "T0"), str(tmp_path / "T1")]
    assert labels is not None
    assert labels.tolist() == [0, 1]


def test_model_mc_test_partition_missing_or_overlapping_fails(tmp_path: Path) -> None:
    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    loader = lambda _path: _Project([("control", [str(tmp_path / "S0")])])

    with pytest.raises(FileNotFoundError, match="requires test_groups.json"):
        resolve_eval_paths_and_labels(
            project_json,
            ["control"],
            project_loader=loader,
            evaluation_partition="test",
        )

    (tmp_path / "test_groups.json").write_text(
        json.dumps([{"label": "control", "paths": [str(tmp_path / "S0")]}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="partitions overlap"):
        resolve_eval_paths_and_labels(
            project_json,
            ["control"],
            project_loader=loader,
            evaluation_partition="test",
        )


def test_assert_model_mc_train_partition_disjoint_and_matches_sidecars(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    for name in ("TR0", "TR1", "TE0"):
        (samples / name).mkdir()
    project_json = tmp_path / "project.json"
    project_json.write_text(
        json.dumps({"samples_base_path": str(samples)}),
        encoding="utf-8",
    )
    (tmp_path / "train_control.csv").write_text("sample\nTR0\n", encoding="utf-8")
    (tmp_path / "train_disease.csv").write_text("sample\nTR1\n", encoding="utf-8")
    (tmp_path / "test_groups.json").write_text(
        json.dumps(
            [
                {"label": "control", "class_index": 0, "paths": [str(samples / "TE0")]},
            ]
        ),
        encoding="utf-8",
    )

    class _Loader:
        def __call__(self, _path):
            return _Project(
                [
                    ("control", [str(samples / "TR0")]),
                    ("disease", [str(samples / "TR1")]),
                ]
            )

    import methyl_validation.eval_split_resolver as mod

    prev = mod.load_project
    mod.load_project = _Loader()  # type: ignore[assignment]
    try:
        out = assert_model_mc_train_partition(project_json)
        assert out["checked"] == 1
        assert out["n_train"] == 2
        assert out["n_test"] == 1

        (tmp_path / "test_groups.json").write_text(
            json.dumps(
                [
                    {"label": "control", "class_index": 0, "paths": [str(samples / "TR0")]},
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="overlap"):
            assert_model_mc_train_partition(project_json)
    finally:
        mod.load_project = prev


def test_assert_model_mc_train_partition_noop_without_test_groups(tmp_path: Path) -> None:
    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    assert assert_model_mc_train_partition(project_json) == {
        "checked": 0,
        "n_train": 0,
        "n_test": 0,
    }
