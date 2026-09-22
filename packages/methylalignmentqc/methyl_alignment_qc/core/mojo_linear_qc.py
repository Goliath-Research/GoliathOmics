"""Guardrails for MojoFq2bamMeth linear metrics (samtools + placeholders)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .wgbs_parabricks_qc import _make_guardrail_metric


def build_mojo_linear_guardrail_report(
    data: Dict[str, Any],
    *,
    core_guardrails: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Hard-fail only on metrics derived from real BAM/samtools fields.

    Placeholder Picard-shaped fields (GC bias, constant cycle qualities,
    synthetic deamination) are recorded as advisory skips so they cannot
    silently pass or fail Clara-equivalent thresholds.
    """
    from ..models.config import CoreGuardrailsConfig

    cfg = core_guardrails or CoreGuardrailsConfig()
    details: Dict[str, Any] = {}

    qy = data.get("quality_yield") or {}
    total_reads = float(qy.get("total_reads") or 0)
    pf_reads = float(qy.get("pf_reads") or 0)
    pf_bases = float(qy.get("pf_bases") or 0)
    pf_q30 = float(qy.get("pf_q30_bases") or 0)

    if total_reads > 0:
        pf_pct = (pf_reads / total_reads) * 100.0
        pf_pass = pf_pct >= float(cfg.min_pf_percent)
        details["pf_percent"] = _make_guardrail_metric(
            value=round(pf_pct, 2),
            normal_range=f">= {cfg.min_pf_percent}",
            passed=pf_pass,
            message="Pass-filter read fraction from MojoFq2bamMeth / samtools-derived yield.",
        )
    else:
        details["pf_percent"] = _make_guardrail_metric(
            value=0.0,
            normal_range=f">= {cfg.min_pf_percent}",
            passed=False,
            message="Missing quality_yield.total_reads in Mojo linear metrics JSON.",
        )

    if pf_bases > 0:
        q30_pct = (pf_q30 / pf_bases) * 100.0
        q30_pass = q30_pct >= float(cfg.min_q30_percent)
        details["q30_percent"] = _make_guardrail_metric(
            value=round(q30_pct, 2),
            normal_range=f">= {cfg.min_q30_percent}",
            passed=q30_pass,
            message="Q30 base fraction estimated from samtools average quality (not Clara Picard).",
        )
    else:
        details["q30_percent"] = _make_guardrail_metric(
            value=0.0,
            normal_range=f">= {cfg.min_q30_percent}",
            passed=False,
            message="Missing quality_yield.pf_bases in Mojo linear metrics JSON.",
        )

    align = data.get("alignment_summary") or {}
    mapped_rate = align.get("mapped_rate")
    if mapped_rate is not None:
        rate = float(mapped_rate)
        details["mapping_rate"] = _make_guardrail_metric(
            value=round(rate, 4),
            normal_range="reported from flagstat",
            passed=rate > 0.0,
            message="Mapped rate from MojoFq2bamMeth flagstat (hard-fail only if zero).",
        )

    skipped = list(data.get("placeholder_fields") or [])
    if not skipped:
        skipped = [
            "mean_quality_by_cycle (constant samtools average)",
            "gc_bias_summary",
            "pre_adapter_summaries.TOTAL_QSCORE",
        ]
    details["mojo_placeholder_fields_skipped"] = _make_guardrail_metric(
        value=float(len(skipped)),
        normal_range="advisory (not Clara Picard)",
        passed=True,
        message=(
            "Placeholder Picard-shaped fields are not used for hard fail/pass: "
            + ", ".join(str(s) for s in skipped)
        ),
    )

    hard_keys = ("pf_percent", "q30_percent", "mapping_rate")
    overall_pass = all(
        bool(details[k]["pass"]) for k in hard_keys if k in details
    )

    return {
        "sample_id": data.get("sample_id", "unknown"),
        "overall_pass": overall_pass,
        "metrics_family": "mojo_linear",
        "details": details,
        "recommendation": (
            "PASS: Mojo linear real metrics OK; proceed to MethylExtractor. "
            "Picard-equivalent GC/cycle/deamination were not scored "
            "(metrics_source=samtools+placeholders)."
            if overall_pass
            else "FAIL: Mojo linear samtools-derived yield/mapping failed. "
            "Do not treat placeholder Picard fields as diagnostic."
        ),
        "next_steps": (
            "Run sample.methyl_extract (MethylExtractor), then sample.extraction_qc. "
            "Optional: enrich with Clara collectmultiplemetrics for full Picard tables."
        ),
        "mojo_linear_note": (
            "metrics_source indicates synthetic Picard-shaped fields; "
            "only PF%/Q30/mapping_rate vote on overall_pass."
        ),
    }
