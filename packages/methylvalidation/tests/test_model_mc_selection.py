from __future__ import annotations

import json
from pathlib import Path

from methyl_validation.cli import _score_backend_from_outputs, _write_backend_ranking


def _write_summary(root: Path, metric: str, mean: float, p50: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        metric: {
            "mean": mean,
            "percentiles": {"p50": p50},
        }
    }
    (root / "metrics_summary.json").write_text(json.dumps(payload), encoding="utf-8")


def test_score_backend_from_outputs_mean_and_median(tmp_path: Path):
    backend_root = tmp_path / "ecdf"
    _write_summary(backend_root, "balanced_accuracy", mean=0.81, p50=0.84)
    assert abs(_score_backend_from_outputs(backend_root, "balanced_accuracy", "mean") - 0.81) < 1e-9
    assert abs(_score_backend_from_outputs(backend_root, "balanced_accuracy", "median") - 0.84) < 1e-9


def test_write_backend_ranking_orders_by_score(tmp_path: Path):
    model_mc_root = tmp_path / "model_mc"
    _write_summary(model_mc_root / "ecdf", "balanced_accuracy", mean=0.80, p50=0.79)
    _write_summary(model_mc_root / "tabular_sklearn", "balanced_accuracy", mean=0.86, p50=0.85)
    _write_summary(model_mc_root / "generative_hybrid", "balanced_accuracy", mean=0.83, p50=0.81)

    rows = _write_backend_ranking(
        model_mc_root=model_mc_root,
        backends=["ecdf", "tabular_sklearn", "generative_hybrid"],
        metric="balanced_accuracy",
        stat="mean",
    )
    assert rows[0]["backend"] == "tabular_sklearn"
    assert rows[0]["rank"] == 1
    assert (model_mc_root / "backend_ranking.csv").is_file()
    assert (model_mc_root / "backend_ranking.json").is_file()
