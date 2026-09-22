"""Tests for predictor test path expansion (CSV lists next to project file)."""

from pathlib import Path

from methyl_predictor.project_resolver import _expand_test_paths


def test_expand_relative_csv_next_to_project_not_under_samples_base(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "s1").mkdir()
    (samples / "s2").mkdir()
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    project_json = cfg_dir / "myproject.json"
    project_json.write_text("{}", encoding="utf-8")
    (cfg_dir / "pytest_predictor_samples.csv").write_text(
        "sample\ns1\ns2\n",
        encoding="utf-8",
    )

    base = str(samples)
    expanded = _expand_test_paths(
        ["configs/pytest_predictor_samples.csv"],
        base,
        project_config_path=project_json,
    )

    assert expanded == [
        str((samples / "s1").resolve()),
        str((samples / "s2").resolve()),
    ]


def test_bare_sample_names_still_resolve_under_samples_base(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "ctrl_a").mkdir()
    expanded = _expand_test_paths(["ctrl_a"], str(samples), project_config_path=None)
    assert expanded == [str((samples / "ctrl_a").resolve())]
