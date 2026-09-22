"""Monte Carlo config, layout inference, predictor policy, and multiclass split."""

import json
from pathlib import Path

import pytest

from methyl_validation.config import MonteCarloConfig
from methyl_validation.predictor_policy import (
    assert_monte_carlo_predictor_allowed,
    assert_validation_predictor_accuracy_mode,
    monte_carlo_rejects_blind_predictor,
)
from methyl_validation.project_gen import (
    generate_run_project_hierarchical_multiclass,
    infer_monte_carlo_layout,
)
from methyl_validation.split import stratified_split_multiclass


def test_stratified_split_multiclass_three_classes():
    cohorts = [
        ("a", [f"/x/a{i}" for i in range(6)]),
        ("b", [f"/x/b{i}" for i in range(6)]),
        ("c", [f"/x/c{i}" for i in range(6)]),
    ]
    train_m, val_m = stratified_split_multiclass(cohorts, train_fraction=0.5, seed=99)
    assert len(train_m["a"]) == 3 and len(val_m["a"]) == 3
    assert sorted(train_m["a"] + val_m["a"]) == sorted(cohorts[0][1])


def test_stratified_split_multiclass_unique_labels_required():
    with pytest.raises(ValueError, match="unique"):
        stratified_split_multiclass(
            [("a", ["/1"]), ("a", ["/2"])],
            train_fraction=0.5,
            seed=1,
        )


def test_monte_carlo_config_rejects_legacy_healthy_disease():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Removed legacy Monte Carlo config keys"):
        MonteCarloConfig.model_validate(
            {
                "samples_base_path": "/tmp/s",
                "healthy_csv": "h.csv",
                "disease_csv": "d.csv",
                "train_fraction": 0.8,
                "n_iterations": 2,
                "base_project": "p.json",
                "output_base": "/tmp/out",
            }
        )


def test_monte_carlo_config_explicit_cohorts():
    c = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp/s",
            "cohorts": [
                {"label": "g0", "csv": "a.csv"},
                {"label": "g1", "csv": "b.csv"},
                {"label": "g2", "csv": "c.csv"},
            ],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "p.json",
            "output_base": "/tmp/out",
        }
    )
    assert [x.label for x in c.cohorts] == ["g0", "g1", "g2"]


def test_infer_monte_carlo_layout_binary(tmp_path: Path):
    p = tmp_path / "proj.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "x",
                "output_base": "/o",
                "samples_base_path": "/s",
                "controls": {"label": "c", "groups": [{"label": "h", "sample_paths": []}]},
                "diseases": {"label": "d", "groups": [{"label": "t", "sample_paths": []}]},
                "comparisons": [{"control_group": "h", "disease_group": "t"}],
            }
        ),
        encoding="utf-8",
    )
    assert infer_monte_carlo_layout(p, 2) == "binary"


def test_infer_monte_carlo_layout_hierarchical_multiclass(tmp_path: Path):
    """K>=3 resolved groups under controls/diseases → hierarchical_multiclass layout."""
    paths = []
    for name in ("a.csv", "b.csv", "c.csv"):
        fp = tmp_path / name
        fp.write_text("sample\ns1\n", encoding="utf-8")
        paths.append(str(fp.resolve()))
    p = tmp_path / "proj.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "x",
                "output_base": str((tmp_path / "out").resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "c",
                    "groups": [
                        {"label": "h1", "sample_paths": [paths[0]]},
                        {"label": "h2", "sample_paths": [paths[1]]},
                    ],
                },
                "diseases": {
                    "label": "d",
                    "groups": [{"label": "d1", "sample_paths": [paths[2]]}],
                },
                "comparisons": "all_pairs",
            }
        ),
        encoding="utf-8",
    )
    assert infer_monte_carlo_layout(p, 3) == "hierarchical_multiclass"
    with pytest.raises(ValueError, match="does not match control/disease resolved groups count"):
        infer_monte_carlo_layout(p, 4)


def test_infer_monte_carlo_layout_rejects_two_cohorts_when_disease_has_stages(tmp_path: Path):
    """Legacy binary MC (2 cohorts) is invalid when the project resolves to >2 centroid leaves."""
    csv_paths = []
    for name in ("h.csv", "p1.csv", "p2.csv", "p3.csv", "p4.csv"):
        fp = tmp_path / name
        fp.write_text("sample\ns1\n", encoding="utf-8")
        csv_paths.append(str(fp.resolve()))
    p = tmp_path / "proj.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "staged",
                "output_base": str((tmp_path / "out").resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": [csv_paths[0]]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "pca1", "sample_paths": [csv_paths[1]]},
                                {"label": "pca2", "sample_paths": [csv_paths[2]]},
                                {"label": "pca3", "sample_paths": [csv_paths[3]]},
                                {"label": "pca4", "sample_paths": [csv_paths[4]]},
                            ],
                        }
                    ],
                },
                "comparisons": [{"control_group": "all", "disease_group": "pca"}],
            }
        ),
        encoding="utf-8",
    )
    assert infer_monte_carlo_layout(p, 5) == "hierarchical_multiclass"
    with pytest.raises(ValueError, match="2 cohorts"):
        infer_monte_carlo_layout(p, 2)


def test_infer_monte_carlo_layout_multiclass(tmp_path: Path):
    p = tmp_path / "proj.json"
    p.write_text(
        json.dumps(
            {
                "groups": [
                    {"label": "c0", "sample_paths": []},
                    {"label": "c1", "sample_paths": []},
                    {"label": "c2", "sample_paths": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert infer_monte_carlo_layout(p, 3) == "multiclass"


def test_predictor_policy_blind():
    assert monte_carlo_rejects_blind_predictor({"blind": {"groups": [{"label": "x"}]}})
    assert not monte_carlo_rejects_blind_predictor({})
    with pytest.raises(ValueError, match="blind"):
        assert_monte_carlo_predictor_allowed({"blind": {"groups": [{"label": "x"}]}})
    with pytest.raises(ValueError, match="labeled test/holdout cohorts"):
        assert_validation_predictor_accuracy_mode({"blind": {"groups": [{"label": "x"}]}})


def test_hierarchical_mc_run_project_predictor_points_at_testing_csvs(tmp_path: Path):
    """Per-run project.json must not leave base template paths under step_config.predictor."""
    list_files = []
    for name in ("h.csv", "p1.csv", "p2.csv", "p3.csv", "p4.csv"):
        fp = tmp_path / name
        fp.write_text("sample\ns0\ns1\ns2\n", encoding="utf-8")
        list_files.append(str(fp.resolve()))

    def three_samples(prefix: str) -> list[str]:
        out = []
        for i in range(3):
            d = tmp_path / f"{prefix}_{i}"
            d.mkdir()
            out.append(str(d.resolve()))
        return out

    cohort_labels = ["all", "pca_pca1", "pca_pca2", "pca_pca3", "pca_pca4"]
    train_by_label = {}
    val_by_label = {}
    for lbl, prefix in zip(
        cohort_labels,
        ["ctrl", "d1", "d2", "d3", "d4"],
        strict=True,
    ):
        paths = three_samples(prefix)
        train_by_label[lbl] = paths[:2]
        val_by_label[lbl] = paths[2:]

    out_base = tmp_path / "out"
    base = tmp_path / "proj.json"
    base.write_text(
        json.dumps(
            {
                "project_name": "template",
                "output_base": str(out_base.resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": [list_files[0]]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "pca1", "sample_paths": [list_files[1]]},
                                {"label": "pca2", "sample_paths": [list_files[2]]},
                                {"label": "pca3", "sample_paths": [list_files[3]]},
                                {"label": "pca4", "sample_paths": [list_files[4]]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            }
        ),
        encoding="utf-8",
    )

    run_dir = tmp_path / "monte_carlo_runs" / "run_0001"
    mc_root = str((tmp_path / "monte_carlo_runs").resolve())
    project_path, _, _ = generate_run_project_hierarchical_multiclass(
        base,
        run_dir,
        "run_0001",
        mc_root,
        train_by_label,
        val_by_label,
        cohort_labels,
        str(tmp_path.resolve()),
    )

    run_proj = json.loads(project_path.read_text(encoding="utf-8"))
    assert "step_config" not in run_proj
    val_groups = json.loads((run_dir / "test_groups.json").read_text(encoding="utf-8"))
    assert len(val_groups) == len(cohort_labels)
    assert [row["label"] for row in val_groups] == cohort_labels
    assert len(list(run_dir.glob("test_*.csv"))) == len(cohort_labels)
    assert not list(run_dir.glob("testing_*.csv"))
    assert not (run_dir / "val_test_groups.json").exists()


def test_hierarchical_mc_run_project_without_predictor_nested_sides(tmp_path: Path):
    """Monte Carlo can omit step_config.predictor.controls/diseases; sides are copied from the project root."""
    list_files = []
    for name in ("h.csv", "p1.csv", "p2.csv", "p3.csv", "p4.csv"):
        fp = tmp_path / name
        fp.write_text("sample\ns0\ns1\ns2\n", encoding="utf-8")
        list_files.append(str(fp.resolve()))

    def three_samples(prefix: str) -> list[str]:
        out = []
        for i in range(3):
            d = tmp_path / f"{prefix}_{i}"
            d.mkdir()
            out.append(str(d.resolve()))
        return out

    cohort_labels = ["all", "pca_pca1", "pca_pca2", "pca_pca3", "pca_pca4"]
    train_by_label = {}
    val_by_label = {}
    for lbl, prefix in zip(
        cohort_labels,
        ["ctrl", "d1", "d2", "d3", "d4"],
        strict=True,
    ):
        paths = three_samples(prefix)
        train_by_label[lbl] = paths[:2]
        val_by_label[lbl] = paths[2:]

    out_base = tmp_path / "out"
    base = tmp_path / "proj.json"
    base.write_text(
        json.dumps(
            {
                "project_name": "template",
                "output_base": str(out_base.resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": [list_files[0]]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "pca1", "sample_paths": [list_files[1]]},
                                {"label": "pca2", "sample_paths": [list_files[2]]},
                                {"label": "pca3", "sample_paths": [list_files[3]]},
                                {"label": "pca4", "sample_paths": [list_files[4]]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            }
        ),
        encoding="utf-8",
    )

    run_dir = tmp_path / "monte_carlo_runs" / "run_0002"
    mc_root = str((tmp_path / "monte_carlo_runs").resolve())
    project_path, _, _ = generate_run_project_hierarchical_multiclass(
        base,
        run_dir,
        "run_0002",
        mc_root,
        train_by_label,
        val_by_label,
        cohort_labels,
        str(tmp_path.resolve()),
    )

    run_proj = json.loads(project_path.read_text(encoding="utf-8"))
    assert "step_config" not in run_proj
    val_groups = json.loads((run_dir / "test_groups.json").read_text(encoding="utf-8"))
    assert len(val_groups) == len(cohort_labels)
    assert run_proj["controls"]["groups"][0]["label"] == "all"
    assert len(run_proj["diseases"]["groups"][0]["stages"]) == 4
    assert len(list(run_dir.glob("test_*.csv"))) == len(cohort_labels)
    assert not list(run_dir.glob("testing_*.csv"))
    assert not (run_dir / "val_test_groups.json").exists()
