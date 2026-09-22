"""
Clinical-performance reporting with confidence intervals and acceptance gates.

This module provides a lightweight FDA-facing metrics layer on top of existing
backend validation outputs. It intentionally works from already-emitted scalar
metrics/confusion matrices so it can be reused across ECDF, tabular, and
generative backends without changing backend internals.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _to_float(val: Any) -> Optional[float]:
    try:
        return float(val)
    except Exception:
        return None


def wilson_ci(successes: int, total: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total <= 0:
        return 0.0, 1.0
    p = max(0.0, min(1.0, successes / total))
    # z for 95% CI by default
    z = 1.959963984540054 if abs(alpha - 0.05) < 1e-12 else 1.959963984540054
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    radius = (z / denom) * math.sqrt((p * (1.0 - p) / total) + ((z * z) / (4.0 * total * total)))
    lo = max(0.0, center - radius)
    hi = min(1.0, center + radius)
    return lo, hi


def _binary_counts_from_confusion(cm: List[List[Any]]) -> Optional[Dict[str, int]]:
    if not isinstance(cm, list) or len(cm) != 2:
        return None
    try:
        tn = int(cm[0][0])
        fp = int(cm[0][1])
        fn = int(cm[1][0])
        tp = int(cm[1][1])
    except Exception:
        return None
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn}


def _confusion_matrix_for_clinical_ci(metrics: Dict[str, Any]) -> List[List[Any]]:
    """Prefer screening_binary 2x2 CM for multiclass; else top-level confusion_matrix."""
    n_classes = int(metrics.get("n_classes") or 0)
    screening = metrics.get("screening_binary")
    if n_classes > 2 and isinstance(screening, dict):
        cm = screening.get("confusion_matrix")
        if isinstance(cm, list):
            return cm
    cm = metrics.get("confusion_matrix")
    return cm if isinstance(cm, list) else []


def compute_binary_ci_summary(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute sensitivity/specificity/PPV/NPV confidence intervals when possible.

    For multiclass metrics, uses ``screening_binary`` (control vs pooled disease) when present.
    """
    out: Dict[str, Any] = {"available": False}
    counts = _binary_counts_from_confusion(_confusion_matrix_for_clinical_ci(metrics))
    if counts is None:
        return out
    tp, tn, fp, fn = counts["tp"], counts["tn"], counts["fp"], counts["fn"]
    sens_denom = tp + fn
    spec_denom = tn + fp
    ppv_denom = tp + fp
    npv_denom = tn + fn
    sens = tp / sens_denom if sens_denom > 0 else None
    spec = tn / spec_denom if spec_denom > 0 else None
    ppv = tp / ppv_denom if ppv_denom > 0 else None
    npv = tn / npv_denom if npv_denom > 0 else None
    sens_ci = wilson_ci(tp, sens_denom) if sens_denom > 0 else (None, None)
    spec_ci = wilson_ci(tn, spec_denom) if spec_denom > 0 else (None, None)
    ppv_ci = wilson_ci(tp, ppv_denom) if ppv_denom > 0 else (None, None)
    npv_ci = wilson_ci(tn, npv_denom) if npv_denom > 0 else (None, None)
    out.update(
        {
            "available": True,
            "counts": counts,
            "sensitivity": sens,
            "specificity": spec,
            "ppv": ppv,
            "npv": npv,
            "sensitivity_ci95": {"lower": sens_ci[0], "upper": sens_ci[1]},
            "specificity_ci95": {"lower": spec_ci[0], "upper": spec_ci[1]},
            "ppv_ci95": {"lower": ppv_ci[0], "upper": ppv_ci[1]},
            "npv_ci95": {"lower": npv_ci[0], "upper": npv_ci[1]},
        }
    )
    n_classes = int(metrics.get("n_classes") or 0)
    if n_classes > 2 and isinstance(metrics.get("screening_binary"), dict):
        out["ci_view"] = "screening_binary"
    elif n_classes == 2:
        out["ci_view"] = "binary"
    return out


def evaluate_acceptance_gates(ci_summary: Dict[str, Any], config: Any) -> Dict[str, Any]:
    """
    Evaluate lower-confidence-bound gates from MonteCarloConfig if configured.
    """
    gates: Dict[str, Any] = {"configured": False, "pass": True, "checks": []}
    min_sens_lcb = _to_float(getattr(config, "min_sensitivity_lcb", None))
    min_spec_lcb = _to_float(getattr(config, "min_specificity_lcb", None))
    if min_sens_lcb is None and min_spec_lcb is None:
        return gates
    gates["configured"] = True
    if not ci_summary.get("available"):
        gates["pass"] = False
        gates["checks"].append(
            {
                "metric": "binary_ci",
                "pass": False,
                "reason": "No 2x2 confusion matrix available (binary or screening_binary).",
            }
        )
        return gates
    if min_sens_lcb is not None:
        observed = _to_float(((ci_summary.get("sensitivity_ci95") or {}).get("lower")))
        passed = observed is not None and observed >= min_sens_lcb
        gates["checks"].append(
            {
                "metric": "sensitivity_lcb",
                "threshold": min_sens_lcb,
                "observed": observed,
                "pass": bool(passed),
            }
        )
        gates["pass"] = bool(gates["pass"] and passed)
    if min_spec_lcb is not None:
        observed = _to_float(((ci_summary.get("specificity_ci95") or {}).get("lower")))
        passed = observed is not None and observed >= min_spec_lcb
        gates["checks"].append(
            {
                "metric": "specificity_lcb",
                "threshold": min_spec_lcb,
                "observed": observed,
                "pass": bool(passed),
            }
        )
        gates["pass"] = bool(gates["pass"] and passed)
    return gates


def _render_markdown(payload: Dict[str, Any]) -> str:
    reg = payload.get("regulatory") or {}
    m = payload.get("metrics") or {}
    ci = payload.get("confidence_intervals") or {}
    gates = payload.get("acceptance_gates") or {}
    lines = [
        "# Clinical performance report",
        "",
        f"- **Lifecycle stage**: `{reg.get('stage', 'feasibility')}`",
        f"- **Claims allowed**: `{bool(reg.get('allow_clinical_performance_claims', False))}`",
        f"- **Claim boundary**: {reg.get('claim_boundary', '')}",
        f"- **Model backend**: `{payload.get('model_backend')}`",
        f"- **Evaluation source**: `{payload.get('source')}`",
        "",
        "## Core metrics",
        "",
        f"- balanced_accuracy: {m.get('balanced_accuracy')}",
        f"- accuracy: {m.get('accuracy')}",
        f"- sensitivity: {m.get('sensitivity')}",
        f"- specificity: {m.get('specificity')}",
        "",
    ]
    screening = m.get("screening_binary")
    if isinstance(screening, dict):
        lines.extend(
            [
                "## Screening binary (control vs pooled disease)",
                "",
                f"- sensitivity: {screening.get('sensitivity')}",
                f"- specificity: {screening.get('specificity')}",
                f"- precision: {screening.get('precision')}",
                f"- npv: {screening.get('npv')}",
                "",
            ]
        )
    per_class = m.get("per_class")
    if isinstance(per_class, list) and per_class:
        lines.extend(["## Per-class metrics", ""])
        for row in per_class:
            lines.append(
                f"- {row.get('class_name')}: recall={row.get('recall')}, "
                f"precision={row.get('precision')}, specificity_ovr={row.get('specificity_ovr')}"
            )
        lines.append("")
    if ci.get("available"):
        s_ci = ci.get("sensitivity_ci95") or {}
        p_ci = ci.get("specificity_ci95") or {}
        lines.extend(
            [
                "## 95% confidence intervals",
                "",
                f"- sensitivity CI95: [{s_ci.get('lower')}, {s_ci.get('upper')}]",
                f"- specificity CI95: [{p_ci.get('lower')}, {p_ci.get('upper')}]",
                "",
            ]
        )
    else:
        lines.extend(["## 95% confidence intervals", "", "- Not available (non-binary or missing confusion matrix).", ""])
    lines.extend(
        [
            "## Acceptance gates",
            "",
            f"- configured: `{bool(gates.get('configured', False))}`",
            f"- pass: `{bool(gates.get('pass', True))}`",
        ]
    )
    for check in gates.get("checks", []) or []:
        lines.append(f"- {check}")
    subgroups = payload.get("subgroup_analysis") or {}
    lines.extend(
        [
            "",
            "## Subgroup analysis hooks",
            "",
            f"- configured subgroup columns: {subgroups.get('configured_columns', [])}",
            f"- status: {subgroups.get('status', 'not_configured')}",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def write_clinical_performance_report(
    *,
    output_dir: Path,
    metrics: Dict[str, Any],
    config: Any,
    source: str,
) -> Dict[str, Any]:
    """
    Write `clinical_performance_report.json/md` under output_dir.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ci_summary = compute_binary_ci_summary(metrics or {})
    gates = evaluate_acceptance_gates(ci_summary, config)
    reg = getattr(config, "regulatory", None)
    payload: Dict[str, Any] = {
        "source": source,
        "model_backend": str(getattr(config, "model_backend", "unknown")),
        "regulatory": {
            "stage": getattr(reg, "stage", "feasibility"),
            "allow_clinical_performance_claims": bool(
                getattr(reg, "allow_clinical_performance_claims", False)
            ),
            "claim_boundary": getattr(
                reg,
                "claim_boundary",
                "Feasibility/development evidence only unless pivotal/submission stage is declared.",
            ),
        },
        "metrics": metrics,
        "confidence_intervals": ci_summary,
        "acceptance_gates": gates,
        "subgroup_analysis": {
            "configured_columns": list(getattr(config, "subgroup_columns", []) or []),
            "status": (
                "configured_no_metadata"
                if (getattr(config, "subgroup_columns", None) or [])
                else "not_configured"
            ),
        },
    }
    json_path = output_dir / "clinical_performance_report.json"
    md_path = output_dir / "clinical_performance_report.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "payload": payload}

