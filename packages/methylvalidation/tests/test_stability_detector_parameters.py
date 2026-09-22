import json
from pathlib import Path

from methyl_validation.stability import (
    run_balanced_accuracy,
    extract_detector_parameters_for_run,
    run_stability_analysis,
)


def _write_results_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_discovery_csv(path: Path, rows: list[dict]) -> None:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_dmp_export_meta(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_balanced_accuracy_reads_featurecuts_validation_summary(tmp_path):
    """FeatureCuts DMP-modeling runs report BA in dmp-export meta, not result*.json."""
    run_dir = tmp_path / "run_0001"
    det = run_dir / "detections" / "all" / "PCa"
    # Detector result*.json carries no usable balanced_accuracy for this mode.
    _write_results_json(
        det / "results-1.json",
        {"n_dmps_exported": 1000, "optimization_validation": None},
    )
    _write_dmp_export_meta(
        det / "dmp-export-1.meta.json",
        {"featurecuts_validation_summary": {"balanced_accuracy": 0.95}},
    )
    _write_dmp_export_meta(
        det / "dmp-export-2.meta.json",
        {"featurecuts_validation_summary": {"balanced_accuracy": 0.97}},
    )

    ba = run_balanced_accuracy(run_dir)
    assert ba is not None
    assert abs(ba - 0.96) < 1e-9


def test_run_balanced_accuracy_falls_back_to_classifier_panel_audit(tmp_path):
    """When featurecuts_validation_summary lacks BA, use classifier_panel_audit target."""
    run_dir = tmp_path / "run_0001"
    det = run_dir / "detections" / "all" / "PCa"
    _write_dmp_export_meta(
        det / "dmp-export-1.meta.json",
        {
            "featurecuts_validation_summary": None,
            "classifier_panel_audit": {"balanced_accuracy_at_k_target": 0.93},
        },
    )
    ba = run_balanced_accuracy(run_dir)
    assert ba is not None
    assert abs(ba - 0.93) < 1e-9


def test_extract_detector_parameters_for_run_summarizes_multichromosome_results(tmp_path):
    run_dir = tmp_path / "run_0001"
    base_cfg = {
        "effect_size_coverage": 0.95,
        "delta_mean_reduction": 0.15,
        "classifier_dmp_selection": "elbow",
        "dynamic_dmp_cutoff_enabled": True,
    }
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca1" / "results-1.json",
        {
            "n_dmps_exported": 1000,
            "total_statistical_dmps": 1500,
            "total_biological_dmps": 1200,
            "config": base_cfg,
        },
    )
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca1" / "results-2.json",
        {
            "n_dmps_exported": 1200,
            "total_statistical_dmps": 1800,
            "total_biological_dmps": 1400,
            "config": base_cfg,
        },
    )

    row = extract_detector_parameters_for_run(run_dir)
    assert row is not None
    assert row["run_id"] == "run_0001"
    assert row["n_result_files"] == 2
    assert row["n_dmps_exported"]["mean"] == 1100.0
    assert row["n_dmps_exported"]["min"] == 1000.0
    assert row["n_dmps_exported"]["max"] == 1200.0
    assert row["total_statistical_dmps"]["mean"] == 1650.0
    assert row["total_biological_dmps"]["mean"] == 1300.0
    assert row["effect_size_coverage"] == 0.95
    assert row["delta_mean_reduction"] == 0.15
    assert row["classifier_dmp_selection"] == "elbow"
    assert row["dynamic_dmp_cutoff_enabled"] is True
    assert row["inconsistent_fields"] == []


def test_extract_detector_parameters_marks_inconsistent_config_fields(tmp_path):
    run_dir = tmp_path / "run_0002"
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca1" / "results-1.json",
        {
            "n_dmps_exported": 1000,
            "total_statistical_dmps": 1500,
            "total_biological_dmps": 1200,
            "config": {
                "effect_size_coverage": 0.95,
                "delta_mean_reduction": 0.15,
                "classifier_dmp_selection": "elbow",
                "dynamic_dmp_cutoff_enabled": True,
            },
        },
    )
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca1" / "results-2.json",
        {
            "n_dmps_exported": 900,
            "total_statistical_dmps": 1400,
            "total_biological_dmps": 1100,
            "config": {
                "effect_size_coverage": 0.90,
                "delta_mean_reduction": 0.15,
                "classifier_dmp_selection": "featurecuts_validation",
                "dynamic_dmp_cutoff_enabled": False,
            },
        },
    )

    row = extract_detector_parameters_for_run(run_dir)
    assert row is not None
    assert row["effect_size_coverage"] is None
    assert row["classifier_dmp_selection"] is None
    assert row["dynamic_dmp_cutoff_enabled"] is None
    assert "effect_size_coverage" in row["inconsistent_fields"]
    assert "classifier_dmp_selection" in row["inconsistent_fields"]
    assert "dynamic_dmp_cutoff_enabled" in row["inconsistent_fields"]


def test_run_stability_analysis_includes_detector_parameters_and_missing_runs(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    run_with_results = monte_root / "run_0001"
    run_without_results = monte_root / "run_0002"
    run_without_results.mkdir(parents=True, exist_ok=True)

    _write_results_json(
        run_with_results / "detections" / "all" / "pca_pca1" / "results-1.json",
        {
            "n_dmps_exported": 1396,
            "total_statistical_dmps": 14957,
            "total_biological_dmps": 13015,
            "config": {
                "effect_size_coverage": 0.95,
                "delta_mean_reduction": 0.15,
                "classifier_dmp_selection": "elbow",
                "dynamic_dmp_cutoff_enabled": True,
            },
        },
    )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
    )
    detector = summary.get("detector_parameters")
    assert isinstance(detector, dict)
    assert len(detector.get("per_run", [])) == 1
    assert detector["aggregates"]["n_runs_with_results"] == 1
    assert detector["aggregates"]["n_runs_without_results"] == 1
    assert detector["aggregates"]["numeric"]["n_dmps_exported_mean"]["mean"] == 1396.0
    assert detector["aggregates"]["categorical"]["classifier_dmp_selection"]["elbow"] == 1

    summary_json = monte_root / "stability" / "stability_summary.json"
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    assert "detector_parameters" in payload


def test_run_stability_analysis_writes_frequency_plot_html(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    # Create 4 runs with discovery DMPs so we get varied frequencies:
    # (1,100) in 4/4, (1,200) in 3/4, (1,300) in 2/4, (1,400) in 1/4
    run_rows = {
        "run_0001": [100, 200, 300, 400],
        "run_0002": [100, 200, 300],
        "run_0003": [100, 200],
        "run_0004": [100],
    }
    for run_id, positions in run_rows.items():
        _write_discovery_csv(
            monte_root / run_id / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
            [{"chromosome": 1, "position": p} for p in positions],
        )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
    )
    assert "dmp_frequency_plot_html" in summary
    assert "dmp_frequency_charts_by_chromosome" in summary
    assert "dmp_frequency_counts_by_chromosome" in summary

    html_path = summary.get("dmp_frequency_plot_html")
    if html_path:
        plot_path = Path(html_path)
        assert plot_path.is_file()
        per_chrom = summary.get("dmp_frequency_charts_by_chromosome") or {}
        assert "1" in per_chrom
        assert Path(per_chrom["1"]).is_file()

        counts_by_chrom = summary.get("dmp_frequency_counts_by_chromosome") or {}
        assert counts_by_chrom["1"]["all_dmps"] == 4
        assert counts_by_chrom["1"]["selected_dmps"] >= 1


def test_run_stability_analysis_writes_empty_stable_panel_when_no_dmps(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    run_dir = monte_root / "run_0001" / "detections" / "all" / "pca_pca1"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Simulate a run with detector export present but no retained DMP rows.
    _write_discovery_csv(run_dir / "dmps-1-discovery.csv", [])

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        dmp_min_freq=0.7,
    )
    stable_csv = Path(summary["stable_dmp_csv"])
    assert stable_csv.is_file()

    import pandas as pd

    df = pd.read_csv(stable_csv)
    assert list(df.columns) == [
        "chromosome",
        "position",
        "frequency",
        "count",
        "n_runs",
        "effect_size",
        "p_value",
        "q_value",
    ]
    assert len(df) == 0


def test_run_stability_analysis_counts_each_dmp_once_per_run(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    # DMP at 100 appears multiple times in run_0001 but should count as one run hit.
    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [
            {"chromosome": 1, "position": 100, "effect_size": 0.4, "p_value": 0.01, "delta_mean": 0.2},
            {"chromosome": 1, "position": 100, "effect_size": 0.5, "p_value": 0.02, "delta_mean": 0.3},
        ],
    )
    _write_discovery_csv(
        monte_root / "run_0002" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.6, "p_value": 0.03, "delta_mean": 0.1}],
    )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        dmp_min_freq=0.0,
    )
    import pandas as pd

    dmp_freq = pd.read_csv(monte_root / "stability" / "dmp_frequency.csv")
    row = dmp_freq[(dmp_freq["chromosome"] == 1) & (dmp_freq["position"] == 100)].iloc[0]
    assert row["count"] == 2
    assert row["n_runs"] == 2
    assert row["frequency"] == 1.0
    assert 0.0 <= row["p_value"] <= 1.0
    assert 0.0 <= row["q_value"] <= 1.0
    assert summary["dmp_stability"]["n_runs_analyzed"] == 2


def test_run_stability_analysis_counts_each_dmp_once_per_run_across_detection_dirs(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "comparison_a" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 16, "position": 46428718, "effect_size": 0.1}],
    )
    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "comparison_b" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 16, "position": 46428718, "effect_size": 0.2}],
    )
    _write_discovery_csv(
        monte_root / "run_0002" / "detections" / "comparison_a" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 16, "position": 46428718, "effect_size": 0.3}],
    )

    run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        dmp_min_freq=0.0,
    )
    import pandas as pd

    dmp_freq = pd.read_csv(monte_root / "stability" / "dmp_frequency.csv")
    row = dmp_freq[(dmp_freq["chromosome"] == 16) & (dmp_freq["position"] == 46428718)].iloc[0]
    assert row["count"] == 2
    assert row["n_runs"] == 2
    assert row["frequency"] == 1.0


def test_run_stability_analysis_refreshes_root_strict_relaxed_in_legacy_mode(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    stability_dir = monte_root / "stability"
    stability_dir.mkdir(parents=True, exist_ok=True)
    # Seed stale dual-cutoff artifacts that should be replaced in legacy mode.
    stale = "chromosome,position,frequency,count,n_runs\n1,100,4.0,120,30\n"
    (stability_dir / "stable_dmps_strict.csv").write_text(stale, encoding="utf-8")
    (stability_dir / "stable_dmps_relaxed.csv").write_text(stale, encoding="utf-8")

    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.5}],
    )
    _write_discovery_csv(
        monte_root / "run_0002" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.4}],
    )

    run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=stability_dir,
        dmp_min_freq=0.0,
        dual_cutoff_enabled=False,
        tiered_stability_enabled=False,
    )

    import pandas as pd

    strict_df = pd.read_csv(stability_dir / "stable_dmps_strict.csv")
    relaxed_df = pd.read_csv(stability_dir / "stable_dmps_relaxed.csv")
    assert strict_df["frequency"].max() <= 1.0
    assert relaxed_df["frequency"].max() <= 1.0
    assert (strict_df["count"] <= strict_df["n_runs"]).all()
    assert (relaxed_df["count"] <= relaxed_df["n_runs"]).all()


def test_run_stability_analysis_removes_stale_root_diagnostics_in_legacy_mode(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    stability_dir = monte_root / "stability"
    stability_dir.mkdir(parents=True, exist_ok=True)
    stale_json = stability_dir / "stable_dmps_score_diagnostics.json"
    stale_csv = stability_dir / "stable_dmps_score_diagnostics.csv"
    stale_json.write_text('{"stale": true}', encoding="utf-8")
    stale_csv.write_text("stale\n1\n", encoding="utf-8")

    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.5}],
    )
    _write_discovery_csv(
        monte_root / "run_0002" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.4}],
    )

    run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=stability_dir,
        dmp_min_freq=0.0,
        dual_cutoff_enabled=False,
        tiered_stability_enabled=False,
    )

    assert not stale_json.exists()
    assert not stale_csv.exists()


def test_run_balanced_accuracy_falls_back_to_detector_results(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "methyl_validation.validator_metrics.iteration_scalar_metrics_from_run_dir",
        lambda _run_dir: {},
    )
    run_dir = tmp_path / "run_0001"
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca1" / "results-1.json",
        {
            "optimization_validation": {
                "performance": {
                    "balanced_accuracy": 0.88
                }
            }
        },
    )
    _write_results_json(
        run_dir / "detections" / "all" / "pca_pca2" / "results-1.json",
        {
            "optimization_validation": {
                "performance": {
                    "balanced_accuracy": 0.92
                }
            }
        },
    )
    ba = run_balanced_accuracy(run_dir)
    assert ba == 0.90
