"""Tests for held-out batch resolution, training exclusion, and prediction reading."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from methyl_validation.holdout_eval import (
    apply_holdout_exclusion_to_project_dict,
    filter_cohort_paths_excluding,
    read_predictions_for_bootstrap,
    resolve_holdout_from_class_map,
    resolve_holdout_groups,
    stratified_holdout_by_fraction,
    _is_control_label,
    _preflight_exclusion,
    write_holdout_eval_artifacts,
    write_holdout_manifest,
    HOLDOUT_MANIFEST_NAME,
)


def _write_csv(path: Path, names: list[str]) -> None:
    path.write_text("sample\n" + "\n".join(names) + "\n", encoding="utf-8")


def _binary_project(tmp_path: Path) -> dict:
    base = tmp_path / "samples"
    base.mkdir()
    healthy_csv = tmp_path / "healthy.csv"
    cancer_csv = tmp_path / "cancer.csv"
    _write_csv(healthy_csv, ["H1", "H2", "H3", "H4"])
    _write_csv(cancer_csv, ["C1", "C2", "C3", "C4"])
    return {
        "samples_base_path": str(base),
        "controls": {"groups": [{"label": "all", "sample_paths": [str(healthy_csv)]}]},
        "diseases": {"groups": [{"label": "cancer", "sample_paths": [str(cancer_csv)]}]},
    }


def test_resolve_holdout_groups_binary(tmp_path):
    project = _binary_project(tmp_path)
    base = project["samples_base_path"]
    holdout = [str(Path(base) / "H3"), str(Path(base) / "H4"), str(Path(base) / "C3")]
    resolved = resolve_holdout_groups(project, holdout)
    assert resolved["binary"] is True
    assert sorted(Path(p).name for p in resolved["control_paths"]) == ["H3", "H4"]
    assert sorted(Path(p).name for p in resolved["disease_paths"]) == ["C3"]
    assert resolved["unresolved"] == []


def test_resolve_holdout_reports_unresolved(tmp_path):
    project = _binary_project(tmp_path)
    resolved = resolve_holdout_groups(project, ["/nowhere/UNKNOWN_X"])
    assert resolved["unresolved"] == ["UNKNOWN_X"]


def test_apply_holdout_exclusion_removes_from_cohorts(tmp_path):
    project = _binary_project(tmp_path)
    out = tmp_path / "prod"
    out.mkdir()
    excluded, class_map = apply_holdout_exclusion_to_project_dict(project, {"H3", "H4", "C4"}, out)
    assert sorted(excluded) == ["C4", "H3", "H4"]
    # Class map captures side/label so labels survive the cohort filtering (Bug 2 fix).
    assert class_map["H3"]["side"] == "control"
    assert class_map["C4"]["side"] == "disease"
    # Project now points at filtered CSVs; verify hold-out names are gone.
    healthy_csv = project["controls"]["groups"][0]["sample_paths"][0]
    kept = [ln.strip() for ln in Path(healthy_csv).read_text().splitlines()[1:] if ln.strip()]
    assert kept == ["H1", "H2"]
    cancer_csv = project["diseases"]["groups"][0]["sample_paths"][0]
    kept_c = [ln.strip() for ln in Path(cancer_csv).read_text().splitlines()[1:] if ln.strip()]
    assert kept_c == ["C1", "C2", "C3"]


def test_is_control_label_all_substring_false_positive_guard():
    # Bug 1: "all" must match only by exact equality, never as a substring.
    assert _is_control_label("all") is True
    assert _is_control_label("healthy") is True
    assert _is_control_label("Normal_Controls") is True
    assert _is_control_label("small_cell") is False  # would be True under naive "all in label"
    assert _is_control_label("gallbladder") is False
    assert _is_control_label("cancer") is False


def test_holdout_eval_end_to_end_label_resolution_after_exclusion(tmp_path):
    # Bug 2 regression: after freeze filters cohorts, class labels are recovered from
    # the manifest class map, not from the (now-filtered) cohort CSVs.
    project = _binary_project(tmp_path)
    base = project["samples_base_path"]
    holdout_paths = [str(Path(base) / n) for n in ("H3", "H4", "C4")]
    holdout_basenames = {Path(p).name for p in holdout_paths}

    prod = tmp_path / "production"
    prod.mkdir()
    excluded, class_map = apply_holdout_exclusion_to_project_dict(project, holdout_basenames, prod)
    write_holdout_manifest(
        prod, partition="locked_test", holdout_paths=holdout_paths, excluded=excluded, class_map=class_map
    )

    # The filtered project can no longer resolve labels from CSVs...
    resolved_from_csv = resolve_holdout_groups(project, holdout_paths)
    assert set(resolved_from_csv["unresolved"]) == holdout_basenames

    # ...but the manifest class map resolves them correctly.
    resolved = resolve_holdout_from_class_map(class_map, holdout_paths, base)
    assert resolved["unresolved"] == []
    assert sorted(Path(p).name for p in resolved["control_paths"]) == ["H3", "H4"]
    assert sorted(Path(p).name for p in resolved["disease_paths"]) == ["C4"]
    assert resolved["binary"] is True


def test_write_holdout_eval_artifacts_caas_symlink_and_mc_root(tmp_path):
    """Eval sidecars land in production/, CAAS product dir, and monte_carlo_runs/."""
    samples = tmp_path / "samples"
    samples.mkdir()
    for name in ("H3", "H4", "C4"):
        (samples / name).mkdir()
    prod = tmp_path / "monte_carlo_runs" / "production"
    prod.mkdir(parents=True)
    caas = tmp_path / ".caas" / "validation_prepare_freeze_project" / "hash123"
    caas.mkdir(parents=True)
    # CAAS owns the real project.json; production path is a symlink (production layout).
    real_project = caas / "project.json"
    real_project.write_text("{}", encoding="utf-8")
    (prod / "project.json").symlink_to(real_project)

    holdout_paths = [str(samples / n) for n in ("H3", "H4", "C4")]
    class_map = {
        "H3": {"side": "control", "label": "all"},
        "H4": {"side": "control", "label": "all"},
        "C4": {"side": "disease", "label": "PCa"},
    }
    mc_root = tmp_path / "monte_carlo_runs"
    written = write_holdout_eval_artifacts(
        production_dir=prod,
        holdout_paths=holdout_paths,
        class_map=class_map,
        samples_base_path=str(samples),
        project_json=prod / "project.json",
        monte_carlo_runs_root=mc_root,
    )
    assert written
    for dest in (prod, caas, mc_root):
        tg = json.loads((dest / "test_groups.json").read_text(encoding="utf-8"))
        assert [e["label"] for e in tg] == ["all", "PCa"]
        assert (dest / "test_control.csv").is_file()
        assert (dest / "test_disease.csv").is_file()
        assert not (dest / "val_control.csv").exists()
        assert not (dest / "val_disease.csv").exists()
        ctrl = (dest / "test_control.csv").read_text(encoding="utf-8")
        assert "H3" in ctrl and "H4" in ctrl
        assert "C4" in (dest / "test_disease.csv").read_text(encoding="utf-8")


def test_preflight_exclusion_pass_and_fail(tmp_path):
    manifest_dir = tmp_path / "production"
    manifest_dir.mkdir()
    write_holdout_manifest(
        manifest_dir, partition="locked_test", holdout_paths=["/x/H3", "/x/H4"], excluded=["H3", "H4"]
    )
    ok, msg = _preflight_exclusion(manifest_dir / HOLDOUT_MANIFEST_NAME, {"H3", "H4"})
    assert ok, msg
    ok2, msg2 = _preflight_exclusion(manifest_dir / HOLDOUT_MANIFEST_NAME, {"H3", "H4", "H5"})
    assert not ok2
    assert "H5" in msg2


def test_preflight_missing_manifest(tmp_path):
    ok, msg = _preflight_exclusion(tmp_path / "nope.json", {"H1"})
    assert not ok
    assert "Re-run" in msg


def test_read_predictions_for_bootstrap(tmp_path):
    csv_path = tmp_path / "predictions.csv"
    pd.DataFrame(
        {
            "sample": ["H1", "C1"],
            "expected_class": [0, 1],
            "prediction": [0, 1],
            "prob_class0": [0.8, 0.3],
            "prob_class1": [0.2, 0.7],
        }
    ).to_csv(csv_path, index=False)
    arrays = read_predictions_for_bootstrap(csv_path)
    assert arrays["y_true"].tolist() == [0, 1]
    assert arrays["y_pred"].tolist() == [0, 1]
    assert arrays["y_proba"].shape == (2, 2)


def test_read_predictions_requires_labels(tmp_path):
    csv_path = tmp_path / "predictions.csv"
    pd.DataFrame({"sample": ["H1"], "prediction": [0]}).to_csv(csv_path, index=False)
    with pytest.raises(ValueError):
        read_predictions_for_bootstrap(csv_path)


def test_stratified_holdout_by_fraction_per_class():
    cohorts = [
        ("healthy", [f"H{i}" for i in range(10)]),
        ("cancer", [f"C{i}" for i in range(20)]),
    ]
    split = stratified_holdout_by_fraction(cohorts, 0.10, seed=1)
    # 10% per class, independently: 1 of 10 healthy, 2 of 20 cancer.
    assert len(split["healthy"]["holdout"]) == 1
    assert len(split["healthy"]["active"]) == 9
    assert len(split["cancer"]["holdout"]) == 2
    assert len(split["cancer"]["active"]) == 18
    # Hold-out and active are disjoint and cover the class.
    for label, n in (("healthy", 10), ("cancer", 20)):
        h = set(split[label]["holdout"])
        a = set(split[label]["active"])
        assert h.isdisjoint(a)
        assert len(h | a) == n


def test_stratified_holdout_is_deterministic_with_seed():
    cohorts = [("a", [f"A{i}" for i in range(50)])]
    s1 = stratified_holdout_by_fraction(cohorts, 0.2, seed=7)
    s2 = stratified_holdout_by_fraction(cohorts, 0.2, seed=7)
    assert s1["a"]["holdout"] == s2["a"]["holdout"]


def test_stratified_holdout_never_empties_active():
    cohorts = [("tiny", ["X1", "X2"])]
    split = stratified_holdout_by_fraction(cohorts, 1.0, seed=0)
    assert len(split["tiny"]["active"]) == 1
    assert len(split["tiny"]["holdout"]) == 1


def test_filter_cohort_paths_excluding_removes_holdout():
    cohorts = [
        ("healthy", ["/data/H1", "/data/H2", "/data/H3"]),
        ("cancer", ["/data/C1", "/data/C2"]),
    ]
    filtered, removed = filter_cohort_paths_excluding(cohorts, {"H2", "C1"})
    assert removed == ["C1", "H2"]
    kept = {label: [Path(p).name for p in paths] for label, paths in filtered}
    assert kept["healthy"] == ["H1", "H3"]
    assert kept["cancer"] == ["C2"]
