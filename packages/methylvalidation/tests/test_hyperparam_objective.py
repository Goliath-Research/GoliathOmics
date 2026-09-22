"""Tests for :mod:`methyl_validation.optimization` objective and constraints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_validation.optimization import (
    ConstraintSet,
    ObjectiveWeights,
    objective_from_monte_carlo_artifacts,
)
from methyl_validation.rollout import evaluate_dual_run


def _summary(
    ba: float = 0.8, f1: float = 0.75, nll: float = 0.5, brier: float = 0.2, ece: float = 0.1
) -> dict:
    m = {
        "balanced_accuracy": {
            "mean": ba,
            "percentiles": {"p50": ba},
        },
        "macro_f1": {"mean": f1, "percentiles": {"p50": f1}},
        "nll": {"mean": nll, "percentiles": {"p50": nll}},
        "brier_score": {"mean": brier, "percentiles": {"p50": brier}},
        "ece": {"mean": ece, "percentiles": {"p50": ece}},
    }
    return {
        "metrics": m,
        **m,
    }


def test_objective_median_ba_f1(tmp_path: Path) -> None:
    mcr = tmp_path / "mcr"
    mcr.mkdir()
    (mcr / "metrics_summary.json").write_text(
        json.dumps(_summary(0.9, 0.85)), encoding="utf-8"
    )
    w = ObjectiveWeights(stat="median", w_balanced_accuracy=1.0, w_macro_f1=1.0)
    r = objective_from_monte_carlo_artifacts(mcr, w, None)
    assert r.feasible
    assert r.value == pytest.approx(1.75, rel=1e-6)
    assert "metric_terms" in r.details


def test_objective_uses_single_model_mc_backend_summary(tmp_path: Path) -> None:
    mcr = tmp_path / "mcr"
    backend = mcr / "model_mc" / "ecdf"
    backend.mkdir(parents=True)
    metrics = backend / "metrics_summary.json"
    metrics.write_text(json.dumps(_summary(0.91, 0.86)), encoding="utf-8")

    w = ObjectiveWeights(stat="median", w_balanced_accuracy=1.0, w_macro_f1=0.0)
    result = objective_from_monte_carlo_artifacts(mcr, w, None)

    assert result.feasible
    assert result.value == pytest.approx(0.91)
    assert result.details["metrics_summary_path"] == str(metrics)


def test_objective_stability_panel(tmp_path: Path) -> None:
    mcr = tmp_path / "mcr"
    mcr.mkdir()
    (mcr / "metrics_summary.json").write_text(json.dumps(_summary()), encoding="utf-8")
    (mcr / "stability").mkdir()
    (mcr / "stability" / "stability_summary.json").write_text(
        json.dumps(
            {
                "dmp_stability": {"stable_dmps_at_threshold": 1000},
                "gene_stability": {"stable_genes_at_threshold": 0},
            }
        ),
        encoding="utf-8",
    )
    w = ObjectiveWeights(
        w_balanced_accuracy=0.0,
        w_macro_f1=0.0,
        w_stability_panel=1.0,
        stability_panel_cap=2000.0,
    )
    r = objective_from_monte_carlo_artifacts(mcr, w, None)
    assert r.feasible
    assert r.value == pytest.approx(1000 / 2000.0)


def test_objective_constraint_rollout(tmp_path: Path) -> None:
    base = _summary(0.8, 0.7, 0.5, 0.2, 0.1)
    cand = _summary(0.81, 0.71, 0.4, 0.15, 0.05)
    p_b = tmp_path / "base.json"
    p_c = tmp_path / "cand.json"
    p_b.write_text(json.dumps(base), encoding="utf-8")
    p_c.write_text(json.dumps(cand), encoding="utf-8")
    r_ev = evaluate_dual_run(baseline_summary_path=p_b, candidate_summary_path=p_c)
    assert r_ev["recommendation"] in ("promote", "hold_or_rollback")

    mcr = tmp_path / "mcr"
    mcr.mkdir()
    (mcr / "metrics_summary.json").write_text(json.dumps(cand), encoding="utf-8")
    c = ConstraintSet(baseline_metrics_summary_path=p_b)
    w = ObjectiveWeights(w_balanced_accuracy=1.0, w_macro_f1=0.0)
    r = objective_from_monte_carlo_artifacts(mcr, w, c)
    if r_ev["recommendation"] == "promote":
        assert r.feasible
    else:
        assert not r.feasible


def test_objective_missing_metrics_file(tmp_path: Path) -> None:
    w = ObjectiveWeights()
    r = objective_from_monte_carlo_artifacts(tmp_path, w, None)
    assert not r.feasible
    assert r.reason == "missing_metrics_summary"


def test_objective_aggregates_gene_featurecuts_ba_when_metrics_summary_missing(
    tmp_path: Path,
) -> None:
    mcr = tmp_path / "mcr"
    for i, ba in enumerate((0.70, 0.80, 0.90), start=1):
        run = mcr / f"run_{i:04d}" / "gene_stability"
        run.mkdir(parents=True)
        (run / "gene_featurecuts_metrics.json").write_text(
            json.dumps(
                {
                    "balanced_accuracy": ba,
                    "validation_metrics": {"balanced_accuracy": ba, "macro_f1": ba - 0.05},
                }
            ),
            encoding="utf-8",
        )
    (mcr / "stability").mkdir()
    (mcr / "stability" / "stability_summary.json").write_text(
        json.dumps({"gene_stability": {"stable_genes_at_threshold": 40}}),
        encoding="utf-8",
    )
    w = ObjectiveWeights(
        stat="mean",
        w_balanced_accuracy=1.0,
        w_macro_f1=0.0,
        w_stable_genes=0.05,
        stability_gene_cap=200.0,
        min_stable_genes=30,
    )
    r = objective_from_monte_carlo_artifacts(mcr, w, None)
    assert r.feasible
    assert r.details["metrics_source"] == "gene_featurecuts_metrics"
    assert r.details["metric_terms"]["balanced_accuracy"] == pytest.approx(0.80)
    assert r.value == pytest.approx(0.80 + 0.05 * (40 / 200.0))
