"""
Automated bisulfite conversion QC from per-sample sidecars or deamination proxy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..models.config import BisulfiteConversionConfig
from ..models.sample_qc import BisulfiteConversionMetrics, GuardrailMetric


def _guardrail_metric(
    *,
    value: float,
    normal_range: str,
    passed: bool,
    message: str,
) -> GuardrailMetric:
    return GuardrailMetric(
        value=value,
        normal_range=normal_range,
        **{"pass": passed},
        message=message,
    )


def _read_sidecar(sample_dir: Path, filename: str) -> Optional[Dict[str, Any]]:
    path = sample_dir / filename
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _deamination_from_payload(payload: Dict[str, Any]) -> Optional[int]:
    guard = payload.get("guardrails") or {}
    details = guard.get("details") or {}
    deam = details.get("deamination_qscore")
    if isinstance(deam, dict):
        val = deam.get("value")
        if val is not None:
            return int(val)
    pre = payload.get("pre_adapter_summaries") or {}
    names = pre.get("ARTIFACT_NAME") or []
    scores = pre.get("TOTAL_QSCORE") or []
    for i, name in enumerate(names):
        if name == "Deamination" and i < len(scores):
            return int(scores[i])
    return None


def resolve_bisulfite_metrics(
    payload: Dict[str, Any],
    sample_dir: Path,
    cfg: BisulfiteConversionConfig,
) -> BisulfiteConversionMetrics:
    """Load or infer bisulfite conversion metrics for one sample."""
    source = str(cfg.source or "sidecar").strip().lower()
    conversion_rate_pct: Optional[float] = None
    non_cpg_methylation_pct: Optional[float] = None
    measurement_source = source
    notes: Optional[str] = None

    if source in {"sidecar", "auto"}:
        sidecar = _read_sidecar(sample_dir, cfg.sidecar_filename)
        if sidecar:
            if sidecar.get("conversion_rate_pct") is not None:
                conversion_rate_pct = float(sidecar["conversion_rate_pct"])
            elif sidecar.get("conversion_rate") is not None:
                conversion_rate_pct = float(sidecar["conversion_rate"]) * (
                    100.0 if float(sidecar["conversion_rate"]) <= 1.0 else 1.0
                )
            if sidecar.get("non_cpg_methylation_pct") is not None:
                non_cpg_methylation_pct = float(sidecar["non_cpg_methylation_pct"])
            measurement_source = str(sidecar.get("source") or "sidecar")
            notes = sidecar.get("notes")

    deam = _deamination_from_payload(payload)

    if source in {"deamination_proxy", "auto"} and conversion_rate_pct is None:
        if deam is not None:
            measurement_source = "deamination_proxy"
            notes = (
                "Quantitative conversion rate not supplied; using "
                "guardrails.details.deamination_qscore as a qualitative conversion "
                "proxy only (not a second metric)."
            )

    return BisulfiteConversionMetrics(
        measurement_source=measurement_source,
        conversion_rate_pct=conversion_rate_pct,
        non_cpg_methylation_pct=non_cpg_methylation_pct,
        min_conversion_rate_pct=float(cfg.min_conversion_rate_pct),
        max_non_cpg_methylation_pct=float(cfg.max_non_cpg_methylation_pct),
        notes=notes,
    )


def build_bisulfite_guardrails(
    metrics: BisulfiteConversionMetrics,
    cfg: BisulfiteConversionConfig,
) -> Dict[str, GuardrailMetric]:
    """Build guardrail metrics; quantitative rules apply only when values are present."""
    out: Dict[str, GuardrailMetric] = {}
    min_conv = float(cfg.min_conversion_rate_pct)
    max_non_cpg = float(cfg.max_non_cpg_methylation_pct)
    if metrics.conversion_rate_pct is not None:
        val = float(metrics.conversion_rate_pct)
        passed = val >= min_conv
        out["conversion_rate_pct"] = _guardrail_metric(
            value=val,
            normal_range=f">= {min_conv:.1f}%",
            passed=passed,
            message=(
                f"Lambda/spike-in conversion rate {val:.2f}% meets threshold."
                if passed
                else f"Conversion rate {val:.2f}% below minimum {min_conv:.1f}%."
            ),
        )

    if metrics.non_cpg_methylation_pct is not None:
        val = float(metrics.non_cpg_methylation_pct)
        passed = val <= max_non_cpg
        out["non_cpg_methylation_pct"] = _guardrail_metric(
            value=val,
            normal_range=f"<= {max_non_cpg:.2f}%",
            passed=passed,
            message=(
                f"Non-CpG methylation {val:.3f}% within limit."
                if passed
                else f"Non-CpG methylation {val:.3f}% exceeds {max_non_cpg:.2f}%."
            ),
        )

    return out


def nested_guardrail_group_failed(details: Dict[str, Any], key: str) -> bool:
    """True when a nested heading (e.g. bisulfite_conversion) has any failed metric."""
    node = details.get(key)
    if not isinstance(node, dict):
        return False
    if "pass" in node:
        return node.get("pass") is False
    return any(isinstance(v, dict) and v.get("pass") is False for v in node.values())


def apply_bisulfite_conversion_to_payload(
    payload: Dict[str, Any],
    sample_dir: Path,
    cfg: Optional[BisulfiteConversionConfig],
) -> None:
    if cfg is None or not cfg.enabled:
        return

    metrics = resolve_bisulfite_metrics(payload, sample_dir, cfg)
    payload["bisulfite_conversion_metrics"] = metrics.model_dump(
        mode="python", by_alias=True, exclude_none=True
    )

    frag_guard = build_bisulfite_guardrails(metrics, cfg)
    if not frag_guard:
        return

    guardrails = payload.setdefault("guardrails", {})
    details = guardrails.setdefault("details", {})
    dumped = {
        k: m.model_dump(mode="python", by_alias=True) for k, m in frag_guard.items()
    }
    details["bisulfite_conversion"] = dumped

    bis_pass = all(bool(m.get("pass")) for m in dumped.values())
    if not bis_pass:
        guardrails["overall_pass"] = False
        rec = str(guardrails.get("recommendation") or "")
        if "bisulfite" not in rec.lower():
            guardrails["recommendation"] = (
                f"{rec} Bisulfite conversion guardrails failed.".strip()
            )
        next_steps = str(guardrails.get("next_steps") or "")
        if metrics.conversion_rate_pct is not None:
            guardrails["next_steps"] = (
                "Quantitative bisulfite conversion recorded from sidecar. "
                "Proceed only if all conversion guardrails pass."
            )
        elif "sidecar" not in next_steps.lower():
            guardrails["next_steps"] = (
                f"{next_steps} Provide {cfg.sidecar_filename} with conversion_rate_pct "
                f"for quantitative bisulfite QC (recommended ≥{cfg.min_conversion_rate_pct}%)."
            ).strip()
