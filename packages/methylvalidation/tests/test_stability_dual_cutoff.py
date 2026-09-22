import pandas as pd

from methyl_validation.stability import (
    _detect_log_score_elbow,
    _score_stable_dmps,
    _select_dual_cutoff_dmps,
    evaluate_dmp_stability_convergence,
    run_stability_analysis,
)


def _write_discovery_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_score_stable_dmps_uses_effect_size_times_sqrt_frequency():
    df = pd.DataFrame(
        {
            "chromosome": [1, 1],
            "position": [100, 200],
            "frequency": [1.0, 0.25],
            "effect_size": [0.8, 0.8],
            "count": [4, 1],
            "n_runs": [4, 4],
        }
    )
    scored = _score_stable_dmps(df)
    assert "combined_score" in scored.columns
    score_100 = float(scored.loc[scored["position"] == 100, "combined_score"].iloc[0])
    score_200 = float(scored.loc[scored["position"] == 200, "combined_score"].iloc[0])
    assert score_100 == 0.8
    assert score_200 == 0.4


def test_detect_log_score_elbow_returns_internal_index_on_heavy_tail():
    scores = pd.Series([1.0, 0.75, 0.52, 0.36, 0.24, 0.08, 0.03, 0.01, 0.009, 0.008, 0.007])
    idx, threshold = _detect_log_score_elbow(scores)
    assert 1 <= idx <= len(scores) - 2
    assert threshold == float(scores.iloc[idx])


def test_select_dual_cutoff_dmps_ensures_strict_subset_relaxed():
    dmp_freq = pd.DataFrame(
        {
            "chromosome": [1] * 8,
            "position": [100, 110, 120, 130, 140, 150, 160, 170],
            "frequency": [0.95, 0.92, 0.90, 0.88, 0.85, 0.82, 0.80, 0.78],
            "effect_size": [0.95, 0.85, 0.78, 0.70, 0.35, 0.22, 0.10, 0.08],
            "count": [19, 18, 18, 17, 17, 16, 16, 15],
            "n_runs": [20] * 8,
        }
    )
    strict, relaxed, scored, diagnostics = _select_dual_cutoff_dmps(
        dmp_freq_df=dmp_freq,
        min_frequency=0.75,
        relaxed_cutoff_mode="strict_multiplier",
        relaxed_multiplier=0.5,
    )
    strict_keys = set(zip(strict["chromosome"], strict["position"]))
    relaxed_keys = set(zip(relaxed["chromosome"], relaxed["position"]))
    assert strict_keys.issubset(relaxed_keys)
    assert len(scored) == 8
    assert diagnostics["n_strict_selected"] <= diagnostics["n_relaxed_selected"]


def test_select_dual_cutoff_dmps_deterministic_on_ties():
    dmp_freq = pd.DataFrame(
        {
            "chromosome": [1, 1, 1, 1],
            "position": [100, 101, 102, 103],
            "frequency": [0.8, 0.8, 0.8, 0.8],
            "effect_size": [0.5, 0.5, 0.5, 0.5],
            "count": [8, 8, 8, 8],
            "n_runs": [10, 10, 10, 10],
        }
    )
    strict_a, relaxed_a, _, diagnostics_a = _select_dual_cutoff_dmps(dmp_freq, min_frequency=0.7)
    strict_b, relaxed_b, _, diagnostics_b = _select_dual_cutoff_dmps(dmp_freq, min_frequency=0.7)
    assert strict_a["position"].tolist() == strict_b["position"].tolist()
    assert relaxed_a["position"].tolist() == relaxed_b["position"].tolist()
    assert diagnostics_a["strict_cutoff_index"] == diagnostics_b["strict_cutoff_index"]


def test_run_stability_analysis_dual_cutoff_writes_expected_artifacts(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    run_rows = {
        "run_0001": [100, 200, 300, 400],
        "run_0002": [100, 200, 300],
        "run_0003": [100, 200],
        "run_0004": [100],
    }
    for run_id, positions in run_rows.items():
        _write_discovery_csv(
            monte_root / run_id / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
            [
                {
                    "chromosome": 1,
                    "position": p,
                    "effect_size": 1.0 - (p / 1000.0),
                }
                for p in positions
            ],
        )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        dmp_min_freq=0.25,
        dual_cutoff_enabled=True,
        relaxed_cutoff_mode="strict_multiplier",
        relaxed_multiplier=0.6,
    )

    assert summary["dual_cutoff_enabled"] is True
    assert summary["stable_dmp_csv"] is not None
    assert summary["stable_dmp_csv_strict"] is not None
    assert summary["stable_dmp_csv_relaxed"] is not None
    assert summary["stable_dmp_scored_csv"] is not None
    assert summary["stable_dmp_score_diagnostics_json"] is not None
    assert summary["stable_dmp_score_diagnostics_csv"] is not None

    strict_df = pd.read_csv(summary["stable_dmp_csv_strict"])
    relaxed_df = pd.read_csv(summary["stable_dmp_csv_relaxed"])
    assert len(strict_df) <= len(relaxed_df)


def test_run_stability_analysis_tiered_outputs_and_extended_alias(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    run_rows = {
        "run_0001": [100, 200, 300, 400],
        "run_0002": [100, 200, 300],
        "run_0003": [100, 200],
        "run_0004": [100],
    }
    for run_id, positions in run_rows.items():
        _write_discovery_csv(
            monte_root / run_id / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
            [
                {
                    "chromosome": 1,
                    "position": p,
                    "effect_size": 1.0 - (p / 1000.0),
                }
                for p in positions
            ],
        )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        tiered_stability_enabled=True,
        tier_core_frequency=0.85,
        tier_extended_frequency=0.80,
        tier_exploratory_frequency=0.70,
        default_freeze_tier="extended",
        relaxed_cutoff_mode="strict_multiplier",
        relaxed_multiplier=0.6,
    )

    assert summary["tiered_stability_enabled"] is True
    assert summary["stability_default_freeze_tier"] == "extended"
    tiers = summary.get("stability_tiers") or {}
    assert set(tiers.keys()) == {"core", "extended", "exploratory"}

    for tier_name in ("core", "extended", "exploratory"):
        tier = tiers[tier_name]
        assert pd.notna(tier["stable_dmp_csv"])
        assert pd.notna(tier["stable_dmp_csv_strict"])
        assert pd.notna(tier["stable_dmp_csv_relaxed"])
        assert pd.notna(tier["stable_dmp_scored_csv"])

    root_df = pd.read_csv(summary["stable_dmp_csv"])
    ext_df = pd.read_csv(tiers["extended"]["stable_dmp_csv"])
    assert len(root_df) == len(ext_df)
    assert root_df.equals(ext_df)


def test_run_stability_analysis_legacy_mode_has_no_tier_outputs(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    _write_discovery_csv(
        monte_root / "run_0001" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
        [{"chromosome": 1, "position": 100, "effect_size": 0.9}],
    )

    summary = run_stability_analysis(
        monte_carlo_runs_root=monte_root,
        output_dir=monte_root / "stability",
        dmp_min_freq=0.7,
    )
    assert summary["tiered_stability_enabled"] is False
    assert summary["stability_tiers"] == {}
    assert summary["stable_dmp_csv"] is not None


def test_evaluate_dmp_stability_convergence_detects_converged_panel(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    shared_positions = [100, 200, 300]
    for idx in range(1, 7):
        _write_discovery_csv(
            monte_root / f"run_{idx:04d}" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
            [
                {"chromosome": 1, "position": p, "effect_size": 0.9}
                for p in shared_positions
            ],
        )

    result = evaluate_dmp_stability_convergence(
        monte_carlo_runs_root=monte_root,
        min_frequency=0.8,
        min_iterations=5,
        convergence_window=2,
        convergence_jaccard=0.99,
        convergence_max_size_delta=0.0,
    )
    assert result["eligible_for_check"] is True
    assert result["converged_checkpoint"] is True
    assert result["jaccard"] == 1.0
    assert result["relative_size_delta"] == 0.0


def test_evaluate_dmp_stability_convergence_reports_insufficient_runs(tmp_path):
    monte_root = tmp_path / "monte_carlo_runs"
    for idx, pos in enumerate([100, 200, 300], start=1):
        _write_discovery_csv(
            monte_root / f"run_{idx:04d}" / "detections" / "all" / "pca_pca1" / "dmps-1-discovery.csv",
            [{"chromosome": 1, "position": pos, "effect_size": 0.9}],
        )

    result = evaluate_dmp_stability_convergence(
        monte_carlo_runs_root=monte_root,
        min_frequency=0.8,
        min_iterations=5,
        convergence_window=2,
    )
    assert result["eligible_for_check"] is False
    assert result["reason"] == "insufficient_qualifying_runs"
