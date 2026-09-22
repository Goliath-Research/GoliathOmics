"""Tests for reusing train/val partitions from existing Monte Carlo run directories."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from methyl_validation.project_gen import generate_run_project, generate_run_project_multiclass
from methyl_validation.reuse_splits import (
    _hash_split_csv_part,
    fingerprint_run_split_csvs,
    resolve_iteration_split,
    try_load_binary_split_from_run_dir,
    try_load_multiclass_split_from_run_dir,
)
from methyl_validation.split import stratified_split, stratified_split_multiclass


def _write_minimal_binary_base_project(path: Path, samples_base: Path) -> None:
    samples_base.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
{
  "controls": {"groups": [{"label": "healthy", "sample_paths": []}]},
  "diseases": {"groups": [{"label": "cancer", "sample_paths": []}]},
  "comparisons": [{"control_group": "healthy", "disease_group": "cancer"}],
  "step_config": {}
}
""".strip(),
        encoding="utf-8",
    )


def test_fingerprint_run_split_csvs_skips_binary_test_glob_overlap(tmp_path: Path) -> None:
    """test_*.csv must not re-hash test_control/test_disease already in the binary list."""
    run_dir = tmp_path / "run_0001"
    run_dir.mkdir()
    (run_dir / "test_control.csv").write_text("control\n", encoding="utf-8")
    (run_dir / "test_disease.csv").write_text("disease\n", encoding="utf-8")
    (run_dir / "test_cohortA.csv").write_text("multi\n", encoding="utf-8")

    fp = fingerprint_run_split_csvs(run_dir)
    assert fp is not None

    unique_parts = [
        _hash_split_csv_part(run_dir / "test_control.csv"),
        _hash_split_csv_part(run_dir / "test_disease.csv"),
        _hash_split_csv_part(run_dir / "test_cohortA.csv"),
    ]
    expected = hashlib.sha256(
        "\n".join(p for p in unique_parts if p is not None).encode("utf-8")
    ).hexdigest()[:16]
    assert fp == expected

    # Duplicating binary test_* entries would change the digest — prove we are not doing that.
    dup_parts = unique_parts + [
        _hash_split_csv_part(run_dir / "test_control.csv"),
        _hash_split_csv_part(run_dir / "test_disease.csv"),
    ]
    dup_fp = hashlib.sha256(
        "\n".join(p for p in dup_parts if p is not None).encode("utf-8")
    ).hexdigest()[:16]
    assert fp != dup_fp


def test_try_load_binary_matches_generated_run(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    for i in range(4):
        (samples / f"c{i}").mkdir(parents=True)
    for i in range(6):
        (samples / f"d{i}").mkdir(parents=True)

    control_paths = [str(samples / f"c{i}") for i in range(4)]
    disease_paths = [str(samples / f"d{i}") for i in range(6)]
    train_c, train_d, val_c, val_d = stratified_split(
        control_paths, disease_paths, train_fraction=0.5, seed=42
    )

    base = tmp_path / "base.json"
    _write_minimal_binary_base_project(base, samples)
    run_dir = tmp_path / "run_0001"
    generate_run_project(
        base,
        run_dir,
        "run_0001",
        str(tmp_path / "mc_root"),
        train_c,
        train_d,
        val_c,
        val_d,
        str(samples),
    )
    assert (run_dir / "test_control.csv").is_file()
    assert (run_dir / "test_disease.csv").is_file()
    assert (run_dir / "test_groups.json").is_file()
    assert not (run_dir / "val_control.csv").exists()
    assert not (run_dir / "val_disease.csv").exists()

    loaded = try_load_binary_split_from_run_dir(
        run_dir, control_paths, disease_paths, str(samples)
    )
    assert loaded is not None
    a, b, c, d = loaded
    assert sorted(a + c) == sorted(control_paths)
    assert sorted(b + d) == sorted(disease_paths)


def test_try_load_binary_returns_none_when_partition_incomplete(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    (samples / "c0").mkdir(parents=True)
    (samples / "d0").mkdir(parents=True)
    control_paths = [str(samples / "c0")]
    disease_paths = [str(samples / "d0")]

    run_dir = tmp_path / "run_bad"
    run_dir.mkdir(parents=True)
    # train lists empty / impossible exhaustive partition
    (run_dir / "train_control.csv").write_text("sample\n", encoding="utf-8")
    (run_dir / "train_disease.csv").write_text("sample\n", encoding="utf-8")
    (run_dir / "val_control.csv").write_text("path\n", encoding="utf-8")
    (run_dir / "val_disease.csv").write_text("path\n", encoding="utf-8")

    assert try_load_binary_split_from_run_dir(run_dir, control_paths, disease_paths, str(samples)) is None


def test_resolve_iteration_split_falls_back_when_run_missing(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    for i in range(4):
        (samples / f"c{i}").mkdir(parents=True)
    for i in range(6):
        (samples / f"d{i}").mkdir(parents=True)
    control_paths = [str(samples / f"c{i}") for i in range(4)]
    disease_paths = [str(samples / f"d{i}") for i in range(6)]
    cohort_paths_list = [("healthy", control_paths), ("cancer", disease_paths)]

    payload, src = resolve_iteration_split(
        layout="binary",
        iteration_index=0,
        split_source_root=tmp_path / "no_runs",
        cohort_paths_list=cohort_paths_list,
        cohort_labels=["healthy", "cancer"],
        control_paths=control_paths,
        disease_paths=disease_paths,
        train_fraction=0.5,
        seed_i=7,
        samples_base_path=str(samples),
    )
    assert src == "generated"
    assert len(payload) == 4

    expected = stratified_split(control_paths, disease_paths, 0.5, seed=7)
    assert payload == expected


def test_resolve_iteration_split_recovers_planner_metadata_from_provenance(
    tmp_path: Path,
) -> None:
    samples = tmp_path / "samples"
    for name in ("c0", "c1", "d0", "d1"):
        (samples / name).mkdir(parents=True)
    control_paths = [str(samples / "c0"), str(samples / "c1")]
    disease_paths = [str(samples / "d0"), str(samples / "d1")]
    split = stratified_split(control_paths, disease_paths, train_fraction=0.5, seed=42)

    study_root = tmp_path / "study"
    mc_root = study_root / "monte_carlo_runs"
    source_run = mc_root / "run_0001"
    source_run.mkdir(parents=True)
    (source_run / "project.json").symlink_to("missing-project.json")

    cache_key = "planner-cache-key"
    cached_run = (
        study_root
        / ".caas"
        / "validation_plan_iterations"
        / cache_key
        / "run_0001"
    )
    base = tmp_path / "base.json"
    _write_minimal_binary_base_project(base, samples)
    generate_run_project(
        base,
        cached_run,
        "run_0001",
        str(mc_root),
        *split,
        str(samples),
    )
    provenance_path = (
        f"/legacy/site/study/.caas/validation_plan_iterations/{cache_key}/"
        "run_0001/.action_results/pipeline_detector.json"
    )
    (mc_root / "action_run_log.jsonl").write_text(
        json.dumps({"outputs": {"manifest_path": provenance_path}}) + "\n",
        encoding="utf-8",
    )

    payload, source = resolve_iteration_split(
        layout="binary",
        iteration_index=0,
        split_source_root=mc_root,
        cohort_paths_list=[("healthy", control_paths), ("cancer", disease_paths)],
        cohort_labels=["healthy", "cancer"],
        control_paths=control_paths,
        disease_paths=disease_paths,
        train_fraction=0.5,
        seed_i=999,
        samples_base_path=str(samples),
    )

    assert source == "reused"
    assert payload == split


def test_try_load_multiclass_matches_generated_run(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    labels = ["A", "B"]
    cohort_paths_list: list[tuple[str, list[str]]] = []
    for lab, n in [("A", 4), ("B", 6)]:
        paths = []
        for i in range(n):
            p = samples / f"{lab}_{i}"
            p.mkdir(parents=True)
            paths.append(str(p))
        cohort_paths_list.append((lab, paths))

    train_m, val_m = stratified_split_multiclass(cohort_paths_list, train_fraction=0.5, seed=99)

    base = tmp_path / "base.json"
    base.write_text(
        """
{
  "groups": [
    {"label": "A", "sample_paths": []},
    {"label": "B", "sample_paths": []}
  ],
  "step_config": {}
}
""".strip(),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run_0001"
    generate_run_project_multiclass(
        base,
        run_dir,
        "run_0001",
        str(tmp_path / "mc_root"),
        train_m,
        val_m,
        labels,
        str(samples),
    )

    loaded = try_load_multiclass_split_from_run_dir(
        run_dir, cohort_paths_list, labels, str(samples)
    )
    assert loaded is not None
    tr, va = loaded
    for lab in labels:
        assert sorted(tr[lab] + va[lab]) == sorted(dict(cohort_paths_list)[lab])


def test_resolve_iteration_split_multiclass_reuses(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    labels = ["A", "B"]
    cohort_paths_list: list[tuple[str, list[str]]] = []
    for lab, n in [("A", 4), ("B", 6)]:
        paths = []
        for i in range(n):
            p = samples / f"{lab}_{i}"
            p.mkdir(parents=True)
            paths.append(str(p))
        cohort_paths_list.append((lab, paths))

    train_m, val_m = stratified_split_multiclass(cohort_paths_list, train_fraction=0.5, seed=123)

    base = tmp_path / "base.json"
    base.write_text(
        """
{
  "groups": [
    {"label": "A", "sample_paths": []},
    {"label": "B", "sample_paths": []}
  ],
  "step_config": {}
}
""".strip(),
        encoding="utf-8",
    )
    mc_root = tmp_path / "monte_carlo_runs"
    run_dir = mc_root / "run_0001"
    generate_run_project_multiclass(
        base,
        run_dir,
        "run_0001",
        str(mc_root),
        train_m,
        val_m,
        labels,
        str(samples),
    )

    payload, src = resolve_iteration_split(
        layout="multiclass",
        iteration_index=0,
        split_source_root=mc_root,
        cohort_paths_list=cohort_paths_list,
        cohort_labels=labels,
        control_paths=[],
        disease_paths=[],
        train_fraction=0.5,
        seed_i=9999,
        samples_base_path=str(samples),
    )
    assert src == "reused"
    assert payload == (train_m, val_m)
