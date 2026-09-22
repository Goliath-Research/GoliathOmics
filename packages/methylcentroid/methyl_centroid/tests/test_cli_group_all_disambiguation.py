"""``--group all`` must target a cohort labeled all, not run every group."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from methyl_centroid import cli
from methyl_centroid.project_resolver import group_token_requests_all_groups


def _write_project(path: Path, *, control_label: str = "all") -> Path:
    payload = {
        "project_name": "test",
        "output_base": str(path.parent),
        "samples_base_path": str(path.parent / "samples"),
        "controls": {
            "label": "healthy",
            "groups": [{"label": control_label, "sample_paths": [str(path.parent / "c.csv")]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [{"label": "PCa", "sample_paths": [str(path.parent / "d.csv")]}],
        },
        "comparisons": [{"control_group": control_label, "disease_group": "PCa"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    (path.parent / "c.csv").write_text("sample\n/work/samples/C1\n", encoding="utf-8")
    (path.parent / "d.csv").write_text("sample\n/work/samples/D1\n", encoding="utf-8")
    return path


def test_group_token_requests_all_groups_when_no_all_label(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "project.json", control_label="healthy")
    assert group_token_requests_all_groups(project, "all") is True


def test_group_token_requests_all_groups_false_when_all_is_label(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "project.json", control_label="all")
    assert group_token_requests_all_groups(project, "all") is False


def test_cli_group_all_runs_single_cohort_when_label_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write_project(tmp_path / "project.json", control_label="all")
    calls: dict[str, object] = {}

    def fake_one(
        project_path,
        group,
        step_override,
        *,
        output_dir=None,
        chromosome=None,
        context=None,
        resolved_config_path=None,
    ):
        calls["one"] = {
            "project": str(project_path),
            "group": group,
            "output_dir": output_dir,
            "chromosome": chromosome,
            "context": context,
        }

    def fake_all(project_path, step_override, *, resolved_config_path=None):
        calls["all"] = True

    monkeypatch.setattr(cli, "run_centroid_for_one_group", fake_one)
    monkeypatch.setattr(cli, "run_centroids_for_all_groups", fake_all)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "methyl-centroid",
            "--project",
            str(project),
            "--group",
            "all",
            "--chromosome",
            "1",
            "--context",
            "CG",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    cli.main()
    assert "all" not in calls
    assert calls["one"]["group"] == "all"
    assert calls["one"]["chromosome"] == "1"
    assert calls["one"]["context"] == "CG"
    assert calls["one"]["output_dir"] == tmp_path / "out"


def test_cli_group_all_runs_every_cohort_when_label_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write_project(tmp_path / "project.json", control_label="healthy")
    calls: dict[str, object] = {}

    def fake_one(*_args, **_kwargs):
        calls["one"] = True

    def fake_all(project_path, step_override, *, resolved_config_path=None):
        calls["all"] = str(project_path)

    monkeypatch.setattr(cli, "run_centroid_for_one_group", fake_one)
    monkeypatch.setattr(cli, "run_centroids_for_all_groups", fake_all)
    monkeypatch.setattr(
        sys,
        "argv",
        ["methyl-centroid", "--project", str(project), "--group", "all"],
    )
    cli.main()
    assert "one" not in calls
    assert calls["all"] == str(project)
