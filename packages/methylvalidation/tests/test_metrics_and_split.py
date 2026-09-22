"""Regression tests for validation split and metrics summaries."""

import math

from methyl_validation.split import load_and_resolve_sample_paths, stratified_split
from methyl_validation.validator_metrics import (
    build_metrics_table,
    compute_resource_summary,
    compute_summary,
    iteration_scalar_metrics_from_run_dir,
    write_metrics_distribution_plotly,
)


def test_stratified_split_is_reproducible_and_exhaustive():
    control_paths = [f"/samples/control_{i}" for i in range(4)]
    disease_paths = [f"/samples/disease_{i}" for i in range(6)]

    split_a = stratified_split(control_paths, disease_paths, train_fraction=0.5, seed=7)
    split_b = stratified_split(control_paths, disease_paths, train_fraction=0.5, seed=7)

    assert split_a == split_b

    train_control, train_disease, val_control, val_disease = split_a
    assert len(train_control) == 2
    assert len(val_control) == 2
    assert len(train_disease) == 3
    assert len(val_disease) == 3
    assert sorted(train_control + val_control) == sorted(control_paths)
    assert sorted(train_disease + val_disease) == sorted(disease_paths)


def test_load_and_resolve_sample_paths_skips_blank_csv_lines(tmp_path):
    csv_path = tmp_path / "samples.csv"
    csv_path.write_text(
        "sample\n"
        "S001\n"
        "\n"
        "  \n"
        "S002\n",
        encoding="utf-8",
    )
    base = tmp_path / "data"
    base.mkdir()
    (base / "S001").mkdir()
    (base / "S002").mkdir()

    resolved = load_and_resolve_sample_paths(csv_path, base)
    assert resolved == [str(base / "S001"), str(base / "S002")]


def test_validation_metric_summaries_aggregate_runs():
    records = [
        {"iteration": 1, "accuracy": 1.0, "balanced_accuracy": 0.90, "sensitivity": 0.80, "specificity": 1.00, "nll": 0.20, "brier_score": 0.08, "ece": 0.03},
        {"iteration": 2, "accuracy": 0.8, "balanced_accuracy": 0.70, "sensitivity": 0.60, "specificity": 0.80, "nll": 0.35, "brier_score": 0.15, "ece": 0.06},
        {"iteration": 3, "accuracy": 0.9, "balanced_accuracy": 0.80, "sensitivity": 0.70, "specificity": 0.90, "nll": 0.27, "brier_score": 0.11, "ece": 0.04},
    ]
    metrics_df = build_metrics_table(records)
    summary = compute_summary(metrics_df)

    assert summary["metrics_schema_version"] == "probabilistic_v3_mc_v1"
    assert math.isclose(summary["accuracy"]["mean"], 0.9)
    assert math.isclose(summary["balanced_accuracy"]["percentiles"]["p50"], 0.8)
    assert math.isclose(summary["sensitivity"]["max"], 0.8)
    assert math.isclose(summary["specificity"]["min"], 0.8)
    assert math.isclose(summary["nll"]["mean"], (0.20 + 0.35 + 0.27) / 3.0)

    resource_summary = compute_resource_summary(
        [
            {
                "run_id": "run-1",
                "step_name": "methyl-centroid-group1",
                "duration_seconds": 4.0,
                "n_train_samples": 10,
                "n_val_samples": 4,
                "n_processed_samples": 9,
                "max_rss_mb": 512.0,
            },
            {
                "run_id": "run-2",
                "step_name": "methyl-centroid-group2",
                "duration_seconds": 6.0,
                "n_train_samples": 12,
                "n_val_samples": 4,
                "n_processed_samples": 11,
                "max_rss_mb": 768.0,
            },
        ]
    )

    assert resource_summary["per_step_duration_seconds"]["methyl-centroid-group1"]["count"] == 1
    assert resource_summary["per_step_duration_seconds"]["methyl-centroid-group2"]["count"] == 1
    assert resource_summary["per_iteration_total_seconds"]["n_iterations"] == 2
    assert math.isclose(resource_summary["sample_sizes"]["n_train_samples"]["mean"], 11.0)
    assert math.isclose(resource_summary["sample_sizes"]["n_val_samples"]["mean"], 4.0)
    assert math.isclose(resource_summary["sample_sizes"]["n_processed_samples"]["mean"], 10.0)


def test_iteration_scalar_metrics_flattens_per_class_recall(tmp_path):
    import json

    from methyl_validation.validator_metrics import _scalar_metrics_from_dict

    metrics = {
        "balanced_accuracy": 0.5,
        "macro_recall": 0.5,
        "per_class": [
            {"class_index": 0, "class_name": "all", "recall": 1.0},
            {"class_index": 1, "class_name": "PCa1", "recall": 0.25},
            {"class_index": 2, "class_name": "PCa2", "recall": 0.5},
        ],
        "training_metrics": {
            "per_class": [
                {"class_index": 0, "class_name": "all", "recall": 0.9},
                {"class_index": 1, "class_name": "PCa1", "recall": 0.1},
            ]
        },
        "holdout_metrics": {
            "per_class": [
                {"class_index": 0, "class_name": "all", "recall": 0.8},
                {"class_index": 1, "class_name": "PCa1", "recall": 0.2},
            ]
        },
        "evaluation_semantics": "train_holdout",
    }
    flat = _scalar_metrics_from_dict(metrics)
    assert flat["recall_all"] == 1.0
    assert flat["recall_PCa1"] == 0.25
    assert flat["recall_PCa2"] == 0.5
    assert flat["training_recall_all"] == 0.9
    assert flat["training_recall_PCa1"] == 0.1
    assert flat["holdout_recall_all"] == 0.8
    assert flat["holdout_recall_PCa1"] == 0.2


def test_iteration_scalar_metrics_prefers_predictor_then_detector(tmp_path):
    import json
    run_dir = tmp_path / "run_0001"
    pred = run_dir / "predictors" / "x"
    pred.mkdir(parents=True)
    vm = pred / "validation_metrics.json"
    vm.write_text(
        json.dumps(
            {
                "balanced_accuracy": 0.88,
                "accuracy": 0.9,
                "per_class": [
                    {"class_index": 0, "class_name": "healthy", "recall": 0.95},
                    {"class_index": 1, "class_name": "cancer", "recall": 0.81},
                ],
            }
        ),
        encoding="utf-8",
    )
    m = iteration_scalar_metrics_from_run_dir(run_dir)
    assert m.get("balanced_accuracy") == 0.88
    assert m.get("recall_healthy") == 0.95
    assert m.get("recall_cancer") == 0.81
    assert m.get("metrics_source") == "predictor"

    (pred / "test_metrics.json").write_text(
        json.dumps({"balanced_accuracy": 0.71, "evaluation_partition": "test"}),
        encoding="utf-8",
    )
    test_metrics = iteration_scalar_metrics_from_run_dir(run_dir)
    assert test_metrics["balanced_accuracy"] == 0.71
    assert test_metrics["metrics_source"] == "model_test"

    run2 = tmp_path / "run_0002"
    det = run2 / "detections" / "healthy" / "cancer"
    det.mkdir(parents=True)
    (det / "result-1-CG.json").write_text(json.dumps({"balanced_accuracy": 0.77}), encoding="utf-8")
    (det / "result-2-CG.json").write_text(json.dumps({"balanced_accuracy": 0.73}), encoding="utf-8")
    m2 = iteration_scalar_metrics_from_run_dir(run2)
    assert abs(m2.get("balanced_accuracy", 0) - 0.75) < 1e-9
    assert m2.get("metrics_source") == "detector"


def test_write_metrics_distribution_plotly(tmp_path):
    import pytest

    pytest.importorskip("plotly")
    pytest.importorskip("scipy")
    df = build_metrics_table(
        [
            {"iteration": 1, "balanced_accuracy": 0.8, "sensitivity": 0.7, "specificity": 0.9},
            {"iteration": 2, "balanced_accuracy": 0.85, "sensitivity": 0.75, "specificity": 0.88},
            {"iteration": 3, "balanced_accuracy": 0.82, "sensitivity": 0.72, "specificity": 0.91},
        ]
    )
    out = tmp_path / "metrics_distributions_plotly.html"
    write_metrics_distribution_plotly(df, out)
    assert out.is_file()
