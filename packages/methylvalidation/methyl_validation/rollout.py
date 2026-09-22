"""
Dual-run rollout utilities for probabilistic-v2 promotion decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple


def _load_summary(path: str | Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _metric_value(summary: Dict[str, Any], metric: str, stat: str) -> float:
    # Support both legacy top-level layout and v2 nested metrics layout.
    node = summary.get(metric)
    if node is None and isinstance(summary.get("metrics"), dict):
        node = summary["metrics"].get(metric)
    if not isinstance(node, dict):
        raise KeyError(f"Metric {metric!r} missing from summary")
    if stat == "median":
        p = (node.get("percentiles") or {}).get("p50")
        if p is None:
            raise KeyError(f"Metric {metric!r} missing p50 for median")
        return float(p)
    if stat not in node:
        raise KeyError(f"Metric {metric!r} missing stat {stat!r}")
    return float(node[stat])


def evaluate_dual_run(
    *,
    baseline_summary_path: str | Path,
    candidate_summary_path: str | Path,
    tolerances: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """
    Compare baseline and candidate metrics and return promotion recommendation.

    `tolerances` keys:
      - balanced_accuracy_drop_max (default 0.005)
      - macro_f1_drop_max (default 0.005)
      - nll_improvement_min_frac (default 0.02)
      - brier_improvement_min_frac (default 0.02)
      - ece_improvement_min_frac (default 0.05)
    """
    tol = {
        "balanced_accuracy_drop_max": 0.005,
        "macro_f1_drop_max": 0.005,
        "nll_improvement_min_frac": 0.02,
        "brier_improvement_min_frac": 0.02,
        "ece_improvement_min_frac": 0.05,
    }
    if tolerances:
        tol.update({k: float(v) for k, v in tolerances.items()})

    b = _load_summary(baseline_summary_path)
    c = _load_summary(candidate_summary_path)

    ba_b = _metric_value(b, "balanced_accuracy", "mean")
    ba_c = _metric_value(c, "balanced_accuracy", "mean")
    f1_b = _metric_value(b, "macro_f1", "mean")
    f1_c = _metric_value(c, "macro_f1", "mean")
    nll_b = _metric_value(b, "nll", "mean")
    nll_c = _metric_value(c, "nll", "mean")
    brier_b = _metric_value(b, "brier_score", "mean")
    brier_c = _metric_value(c, "brier_score", "mean")
    ece_b = _metric_value(b, "ece", "mean")
    ece_c = _metric_value(c, "ece", "mean")

    checks = {
        "balanced_accuracy_guard": ba_c >= (ba_b - tol["balanced_accuracy_drop_max"]),
        "macro_f1_guard": f1_c >= (f1_b - tol["macro_f1_drop_max"]),
        "nll_improved": nll_c <= (nll_b * (1.0 - tol["nll_improvement_min_frac"])),
        "brier_improved": brier_c <= (brier_b * (1.0 - tol["brier_improvement_min_frac"])),
        "ece_improved": ece_c <= (ece_b * (1.0 - tol["ece_improvement_min_frac"])),
    }
    promote = bool(
        checks["balanced_accuracy_guard"]
        and checks["macro_f1_guard"]
        and checks["nll_improved"]
        and checks["brier_improved"]
        and checks["ece_improved"]
    )

    return {
        "baseline_summary_path": str(Path(baseline_summary_path)),
        "candidate_summary_path": str(Path(candidate_summary_path)),
        "tolerances": tol,
        "metrics": {
            "baseline": {
                "balanced_accuracy_mean": ba_b,
                "macro_f1_mean": f1_b,
                "nll_mean": nll_b,
                "brier_score_mean": brier_b,
                "ece_mean": ece_b,
            },
            "candidate": {
                "balanced_accuracy_mean": ba_c,
                "macro_f1_mean": f1_c,
                "nll_mean": nll_c,
                "brier_score_mean": brier_c,
                "ece_mean": ece_c,
            },
        },
        "checks": checks,
        "recommendation": "promote" if promote else "hold_or_rollback",
    }


def write_rollout_report(report: Dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return out

