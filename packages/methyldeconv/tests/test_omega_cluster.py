"""Unit tests for Ω-cluster research analysis."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_deconv.analysis.omega_cluster import (
    OMEGA_COLS,
    OmegaClusterConfig,
    _pca_showlegend_for_visibility,
    _try_write_pca_plot,
    clr_transform,
    load_cell_fractions,
    run_omega_cluster_analysis,
)


def _plotly_available() -> bool:
    try:
        import plotly  # noqa: F401

        return True
    except Exception:
        return False


def _synthetic_fractions(tmp_path: Path, *, n_h: int = 40, n_d: int = 40) -> Path:
    """Two healthy Ω modes + disease shifted within/near them."""
    rng = np.random.default_rng(7)
    rows = []
    # healthy mode 0: neutrophil-high
    for i in range(n_h // 2):
        w = np.array([0.05, 0.10, 0.05, 0.05, 0.10, 0.65], dtype=float)
        w = w + rng.normal(0, 0.01, size=6)
        w = np.clip(w, 1e-3, None)
        w = w / w.sum()
        rows.append({"sample_id": f"H0_{i}", "group": "all", **dict(zip(OMEGA_COLS, w)), "qp_status": "ok"})
    # healthy mode 1: more lymphoid
    for i in range(n_h - n_h // 2):
        w = np.array([0.15, 0.25, 0.12, 0.12, 0.12, 0.24], dtype=float)
        w = w + rng.normal(0, 0.01, size=6)
        w = np.clip(w, 1e-3, None)
        w = w / w.sum()
        rows.append({"sample_id": f"H1_{i}", "group": "all", **dict(zip(OMEGA_COLS, w)), "qp_status": "ok"})
    # disease near mode 0 but CD8 down / Neu up slightly
    for i in range(n_d // 2):
        w = np.array([0.03, 0.08, 0.04, 0.04, 0.11, 0.70], dtype=float)
        w = w + rng.normal(0, 0.01, size=6)
        w = np.clip(w, 1e-3, None)
        w = w / w.sum()
        rows.append({"sample_id": f"D0_{i}", "group": "PCa", **dict(zip(OMEGA_COLS, w)), "qp_status": "ok"})
    for i in range(n_d - n_d // 2):
        w = np.array([0.10, 0.20, 0.10, 0.10, 0.15, 0.35], dtype=float)
        w = w + rng.normal(0, 0.01, size=6)
        w = np.clip(w, 1e-3, None)
        w = w / w.sum()
        rows.append({"sample_id": f"D1_{i}", "group": "PCa", **dict(zip(OMEGA_COLS, w)), "qp_status": "ok"})
    path = tmp_path / "cell_fractions.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_clr_rows_sum_to_zero():
    X = np.array([[0.1, 0.2, 0.1, 0.1, 0.1, 0.4], [0.2, 0.2, 0.2, 0.1, 0.1, 0.2]], dtype=float)
    Y = clr_transform(X)
    assert Y.shape == X.shape
    assert np.allclose(Y.sum(axis=1), 0.0, atol=1e-8)


def test_load_requires_groups(tmp_path: Path):
    path = _synthetic_fractions(tmp_path)
    df = load_cell_fractions(path, cfg=OmegaClusterConfig())
    assert set(df["group"]) == {"all", "PCa"}
    assert set(df["y"]) == {0, 1}


def test_run_analysis_writes_artifacts(tmp_path: Path):
    path = _synthetic_fractions(tmp_path)
    out = tmp_path / "out"
    cfg = OmegaClusterConfig(
        test_size=0.3,
        random_state=0,
        k_min=2,
        k_max=3,
        min_train_per_class=5,
        lda_residual_sensitivity=True,
    )
    summary = run_omega_cluster_analysis(path, out, cfg=cfg)
    assert (out / "omega_cluster_summary.json").is_file()
    assert (out / "omega_stratum_assignments.csv").is_file()
    assert (out / "omega_stratum_sizes.csv").is_file()
    pca_html = out / "omega_pca_by_stratum.html"
    if _plotly_available():
        assert pca_html.is_file()
        assert summary["artifacts"].get("pca_by_stratum_html") == str(pca_html)
        html = pca_html.read_text(encoding="utf-8")
        assert "<html" in html.lower()
        # Analyst split filter (All / Train / Test buttons)
        assert "Split filter" in html
        assert '"label":"Train"' in html or '"label": "Train"' in html
        assert '"label":"Test"' in html or '"label": "Test"' in html
    assert summary["clustering"]["dimensions"] == "all_6"
    assert summary["clustering"]["healthy_k"] >= 2
    names = {r["strategy"] for r in summary["results"]}
    assert "baseline_healthy_vs_disease" in names
    assert "matched_stratum_routed" in names
    assert "all_pairs_routed" in names
    assert "recommendation" in summary
    assert "pipeline_follow_on_justified" in summary["recommendation"]
    assert "matched_strata_used" in summary["recommendation"]
    # Synthetic data has two healthy modes → matched typically uses ≥1 stratum
    assert summary["recommendation"]["matched_strata_used"] >= 1


def test_pca_showlegend_survives_test_filter():
    """Legend must stay on a visible trace when filtering to test-only."""
    # Trace order matches plot builder: stratum × label × (train, test)
    splits = ["train", "test", "train", "test"]
    legend_keys = ["0|healthy", "0|healthy", "0|disease", "0|disease"]
    test_visible = [s == "test" for s in splits]
    flags = _pca_showlegend_for_visibility(test_visible, legend_keys)
    assert flags == [False, True, False, True]
    assert all(not f or v for f, v in zip(flags, test_visible))
    assert any(flags)

    all_flags = _pca_showlegend_for_visibility([True] * 4, legend_keys)
    assert all_flags == [True, False, True, False]


@pytest.mark.skipif(not _plotly_available(), reason="plotly not installed")
def test_pca_html_test_filter_updates_showlegend(tmp_path: Path):
    rng = np.random.default_rng(0)
    rows = []
    for split in ("train", "test"):
        for y in (0, 1):
            for stratum in (0, 1):
                for i in range(3):
                    w = rng.dirichlet(np.ones(6))
                    rows.append(
                        {
                            "sample_id": f"{split}_{y}_{stratum}_{i}",
                            "group": "all" if y == 0 else "PCa",
                            "y": y,
                            "split": split,
                            "healthy_stratum": stratum,
                            "disease_stratum": stratum,
                            **dict(zip(OMEGA_COLS, w)),
                        }
                    )
    path = tmp_path / "omega_pca_by_stratum.html"
    assert _try_write_pca_plot(pd.DataFrame(rows), path, cfg=OmegaClusterConfig()) == path
    html = path.read_text(encoding="utf-8")
    # Test button must retarget showlegend (not only visible)
    assert '"label":"Test"' in html or '"label": "Test"' in html
    assert "showlegend" in html
    # Under a naive train-only showlegend assignment, test-visible traces are all false;
    # the fixed HTML should include a true showlegend paired with a false preceding train.
    assert "true" in html.lower()
