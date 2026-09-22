"""Alignment-layer metrics and guardrails derived from Picard dedup and GC bias."""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from ..models.config import AlignmentGuardrailsConfig


def _guardrail_metric(
    *,
    value: Any,
    normal_range: str,
    passed: bool,
    message: str,
) -> Dict[str, Any]:
    return {
        "value": value,
        "normal_range": normal_range,
        "pass": passed,
        "message": message,
    }


def _first_duplication_row(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rows = payload.get("duplication_metrics")
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _gc_coverage_uniformity(payload: Dict[str, Any]) -> Optional[float]:
    details = payload.get("gc_bias_details") or {}
    windows = details.get("WINDOWS") or []
    coverages = details.get("NORMALIZED_COVERAGE") or []
    if not windows or not coverages:
        return None
    values: List[float] = []
    for i, win in enumerate(windows):
        try:
            if int(win) <= 0:
                continue
            values.append(float(coverages[i]))
        except (IndexError, TypeError, ValueError):
            continue
    if len(values) < 2:
        return None
    med = float(median(values))
    if med <= 0:
        return None
    return float(min(values)) / med


def compute_alignment_stats(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive alignment_stats from duplication_metrics and gc_bias_details."""
    row = _first_duplication_row(payload)
    if row is None:
        return None

    read_pairs = int(row.get("READ_PAIRS_EXAMINED", 0) or 0)
    unpaired = int(row.get("UNPAIRED_READS_EXAMINED", 0) or 0)
    unmapped = int(row.get("UNMAPPED_READS", 0) or 0)
    secondary_supp = int(row.get("SECONDARY_OR_SUPPLEMENTARY_RDS", 0) or 0)
    reads_examined = 2 * read_pairs + unpaired
    if reads_examined <= 0:
        return None

    mapping_rate = 1.0 - (unmapped / reads_examined)
    secondary_supplementary_rate = secondary_supp / reads_examined
    gc_uniformity = _gc_coverage_uniformity(payload)

    stats: Dict[str, Any] = {
        "reads_examined": reads_examined,
        "unmapped_reads": unmapped,
        "secondary_supplementary_reads": secondary_supp,
        "mapping_rate": round(mapping_rate, 6),
        "secondary_supplementary_rate": round(secondary_supplementary_rate, 6),
    }
    if gc_uniformity is not None:
        stats["gc_coverage_uniformity"] = round(gc_uniformity, 6)
    return stats


def apply_alignment_derived_guardrails(
    report: Dict[str, Any],
    payload: Dict[str, Any],
    cfg: Optional[AlignmentGuardrailsConfig],
) -> None:
    """Append Phase-1 alignment guardrails when cfg.enabled (mutates report in place)."""
    if cfg is None or not cfg.enabled:
        return

    details = report.setdefault("details", {})
    stats = compute_alignment_stats(payload)
    if stats is not None:
        payload["alignment_stats"] = stats

    if cfg.min_mapping_rate is not None:
        if stats is None:
            details["mapping_rate"] = _guardrail_metric(
                value=None,
                normal_range=f">= {cfg.min_mapping_rate}",
                passed=False,
                message="Missing duplication_metrics; cannot compute mapping rate.",
            )
            report["overall_pass"] = False
        else:
            rate = float(stats["mapping_rate"])
            passed = rate >= cfg.min_mapping_rate
            details["mapping_rate"] = _guardrail_metric(
                value=round(rate, 6),
                normal_range=f">= {cfg.min_mapping_rate}",
                passed=passed,
                message=(
                    "Fraction of examined reads that mapped. Low values suggest reference "
                    "mismatch, contamination, or severe library quality issues."
                ),
            )
            if not passed:
                report["overall_pass"] = False

    if cfg.max_secondary_supplementary_rate is not None:
        if stats is None:
            details["secondary_supplementary_rate"] = _guardrail_metric(
                value=None,
                normal_range=f"<= {cfg.max_secondary_supplementary_rate}",
                passed=False,
                message="Missing duplication_metrics; cannot compute secondary/supplementary rate.",
            )
            report["overall_pass"] = False
        else:
            rate = float(stats["secondary_supplementary_rate"])
            passed = rate <= cfg.max_secondary_supplementary_rate
            details["secondary_supplementary_rate"] = _guardrail_metric(
                value=round(rate, 6),
                normal_range=f"<= {cfg.max_secondary_supplementary_rate}",
                passed=passed,
                message=(
                    "Secondary and supplementary alignments as a fraction of examined reads. "
                    "Elevated rates can indicate chimeras, split reads, or mapping artifacts."
                ),
            )
            if not passed:
                report["overall_pass"] = False

    if cfg.min_gc_coverage_uniformity is not None:
        uniformity = stats.get("gc_coverage_uniformity") if stats else None
        if uniformity is None:
            details["gc_coverage_uniformity"] = _guardrail_metric(
                value=None,
                normal_range=f">= {cfg.min_gc_coverage_uniformity}",
                passed=False,
                message="Missing gc_bias_details with sufficient bins; cannot compute coverage uniformity.",
            )
            report["overall_pass"] = False
        else:
            passed = float(uniformity) >= cfg.min_gc_coverage_uniformity
            details["gc_coverage_uniformity"] = _guardrail_metric(
                value=round(float(uniformity), 6),
                normal_range=f">= {cfg.min_gc_coverage_uniformity}",
                passed=passed,
                message=(
                    "Min/median normalized coverage across GC bins (Picard GC bias). "
                    "Low uniformity suggests localized dropout or coverage bias before extraction."
                ),
            )
            if not passed:
                report["overall_pass"] = False

    if not report.get("overall_pass") and str(report.get("recommendation", "")).startswith("PASS"):
        report["recommendation"] = (
            "FAIL: Do NOT proceed. Investigate alignment mapping, coverage uniformity, "
            "or library prep before methylation extraction."
        )
