"""Pass/Fail guardrails for MethylExtractor extraction manifests."""

from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Optional

from .manifest import contexts_extracted
from .models.config import ExtractionQCGuardrailConfig

AUTOSOMAL_CHROMOSOMES = {str(i) for i in range(1, 23)}


def _metric(
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


def _cg_mean_coverage(chrom_entry: Dict[str, Any]) -> Optional[float]:
    cg = chrom_entry.get("CG")
    if not isinstance(cg, dict):
        return None
    value = cg.get("mean_coverage")
    if value is None:
        return None
    return float(value)


def _read_retention_rate(read_filtering: Dict[str, Any]) -> Optional[float]:
    """Retention rate (reads_used / reads_seen) from a manifest read_filtering block.

    Prefers the exporter-provided ``read_retention_rate`` and falls back to
    computing it from ``reads_used`` / ``reads_seen``. Returns None when the
    block predates read-filtering stats or reports no reads seen.
    """

    rate = read_filtering.get("read_retention_rate")
    if rate is not None:
        return float(rate)
    reads_seen = read_filtering.get("reads_seen")
    reads_used = read_filtering.get("reads_used")
    if reads_seen is None or reads_used is None:
        return None
    reads_seen_f = float(reads_seen)
    if reads_seen_f <= 0:
        return None
    return float(reads_used) / reads_seen_f


def evaluate_guardrails(
    manifest: Dict[str, Any],
    *,
    config: ExtractionQCGuardrailConfig,
    expected_chromosomes: Iterable[str],
    panel_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    summary = manifest.get("summary") or {}
    per_chromosome = manifest.get("per_chromosome") or {}
    extracted_contexts = set(contexts_extracted(manifest))
    expected = [str(chrom) for chrom in expected_chromosomes]

    results: Dict[str, Any] = {}

    cpg_cov = summary.get("cpg_weighted_mean_coverage")
    if cpg_cov is None:
        results["cpg_weighted_mean_coverage"] = _metric(
            value=None,
            normal_range=f">= {config.min_cpg_weighted_mean_coverage}",
            passed=False,
            message="Missing summary.cpg_weighted_mean_coverage in extraction manifest.",
        )
    else:
        cpg_cov_f = float(cpg_cov)
        passed = cpg_cov_f >= config.min_cpg_weighted_mean_coverage
        results["cpg_weighted_mean_coverage"] = _metric(
            value=round(cpg_cov_f, 4),
            normal_range=f">= {config.min_cpg_weighted_mean_coverage}",
            passed=passed,
            message=(
                "Genome-wide CpG weighted mean coverage on sites passing min_cov. "
                "Low values indicate insufficient read depth for methylation calling."
            ),
        )

    if "CHH" in extracted_contexts:
        chh = summary.get("chh_methylation_level")
        if chh is None:
            results["chh_methylation_level"] = _metric(
                value=None,
                normal_range=f"<= {config.max_chh_methylation_level}",
                passed=False,
                message="CHH context was extracted but summary.chh_methylation_level is missing.",
            )
        else:
            chh_f = float(chh)
            passed = chh_f <= config.max_chh_methylation_level
            results["chh_methylation_level"] = _metric(
                value=round(chh_f, 6),
                normal_range=f"<= {config.max_chh_methylation_level}",
                passed=passed,
                message=(
                    "Non-CpG CHH methylation should stay low in bisulfite-converted WGBS. "
                    "Elevated CHH suggests incomplete conversion or contamination."
                ),
            )

    if "CHG" in extracted_contexts:
        chg = summary.get("chg_methylation_level")
        if chg is None:
            results["chg_methylation_level"] = _metric(
                value=None,
                normal_range=f"<= {config.max_chg_methylation_level}",
                passed=False,
                message="CHG context was extracted but summary.chg_methylation_level is missing.",
            )
        else:
            chg_f = float(chg)
            passed = chg_f <= config.max_chg_methylation_level
            results["chg_methylation_level"] = _metric(
                value=round(chg_f, 6),
                normal_range=f"<= {config.max_chg_methylation_level}",
                passed=passed,
                message=(
                    "Non-CpG CHG methylation should stay low in bisulfite-converted WGBS. "
                    "Elevated CHG suggests incomplete conversion or contamination."
                ),
            )

    missing: List[str] = []
    for chrom in expected:
        chrom_entry = per_chromosome.get(chrom)
        if not isinstance(chrom_entry, dict) or not isinstance(chrom_entry.get("CG"), dict):
            missing.append(chrom)
    completeness_pass = not missing
    results["chromosome_completeness"] = _metric(
        value={
            "expected": expected,
            "missing": missing,
            "present_count": len(expected) - len(missing),
        },
        normal_range="all expected chromosomes present with CG metrics",
        passed=completeness_pass,
        message=(
            "Each expected chromosome must appear in manifest.per_chromosome with a CG block."
        ),
    )

    autosomal_coverages: List[float] = []
    for chrom in expected:
        if chrom not in AUTOSOMAL_CHROMOSOMES:
            continue
        chrom_entry = per_chromosome.get(chrom)
        if not isinstance(chrom_entry, dict):
            continue
        mean_cov = _cg_mean_coverage(chrom_entry)
        if mean_cov is not None:
            autosomal_coverages.append(mean_cov)

    median_coverage: Optional[float] = None
    if len(autosomal_coverages) < 2:
        uniformity_ratio = None
        uniformity_pass = True
        uniformity_message = (
            "Skipped autosomal uniformity check because fewer than two autosomal "
            "chromosomes were evaluated."
        )
    else:
        median_coverage = statistics.median(autosomal_coverages)
        minimum = min(autosomal_coverages)
        uniformity_ratio = minimum / median_coverage if median_coverage > 0 else 0.0
        uniformity_pass = uniformity_ratio >= config.min_autosomal_coverage_uniformity_ratio
        uniformity_message = (
            "Autosomal CpG mean coverage should be uniform across chromosomes. "
            "Low min/median ratio flags localized dropout or mapping bias."
        )

    results["chromosome_uniformity"] = _metric(
        value={
            "min_autosomal_mean_coverage": round(min(autosomal_coverages), 4)
            if autosomal_coverages
            else None,
            "median_autosomal_mean_coverage": (
                round(median_coverage, 4) if median_coverage is not None else None
            ),
            "min_over_median": round(uniformity_ratio, 4) if uniformity_ratio is not None else None,
        },
        normal_range=f">= {config.min_autosomal_coverage_uniformity_ratio}",
        passed=uniformity_pass,
        message=uniformity_message,
    )

    read_filtering = manifest.get("read_filtering")
    retention_rate = (
        _read_retention_rate(read_filtering) if isinstance(read_filtering, dict) else None
    )
    if retention_rate is None or not isinstance(read_filtering, dict):
        results["read_discard_fraction"] = _metric(
            value=None,
            normal_range=f"<= {config.max_discard_fraction}",
            passed=True,
            message=(
                "Skipped read-discard check: extraction manifest read_filtering block is "
                "absent or reports no reads seen (manifest predates read-filtering stats)."
            ),
        )
    else:
        discard_fraction = 1.0 - retention_rate
        discard_pass = discard_fraction <= config.max_discard_fraction
        results["read_discard_fraction"] = _metric(
            value={
                "discard_fraction": round(discard_fraction, 6),
                "read_retention_rate": round(retention_rate, 6),
                "reads_seen": read_filtering.get("reads_seen"),
                "reads_used": read_filtering.get("reads_used"),
            },
            normal_range=f"<= {config.max_discard_fraction}",
            passed=discard_pass,
            message=(
                "Fraction of reads dropped by extraction-time filters (unmapped, "
                "secondary/supplementary, duplicate, low-MAPQ, multimap, no-strand). "
                "A very high discard fraction with acceptable coverage signals a systematic "
                "problem (wrong reference, contamination, or mis-set filters)."
            ),
        )

    panel = panel_metrics or {}
    if config.min_on_target_fraction is not None:
        frac = panel.get("on_target_fraction")
        passed = frac is not None and float(frac) >= config.min_on_target_fraction
        results["on_target_fraction"] = _metric(
            value=frac,
            normal_range=f">= {config.min_on_target_fraction}",
            passed=passed,
            message="Fraction of extracted CG sites inside target_panel_bed.",
        )
    if config.min_on_target_mean_coverage is not None:
        cov = panel.get("on_target_mean_coverage")
        passed = cov is not None and float(cov) >= config.min_on_target_mean_coverage
        results["on_target_mean_coverage"] = _metric(
            value=cov,
            normal_range=f">= {config.min_on_target_mean_coverage}",
            passed=passed,
            message="Mean coverage of CG sites inside the capture panel.",
        )
    if config.min_pos_control_methylation is not None:
        val = panel.get("pos_control_mean_methylation")
        passed = val is not None and float(val) >= config.min_pos_control_methylation
        results["pos_control_mean_methylation"] = _metric(
            value=val,
            normal_range=f">= {config.min_pos_control_methylation}",
            passed=passed,
            message="Positive-control interval methylation (EM-Seq conversion/capture sanity).",
        )
    if config.max_neg_control_methylation is not None:
        val = panel.get("neg_control_mean_methylation")
        passed = val is not None and float(val) <= config.max_neg_control_methylation
        results["neg_control_mean_methylation"] = _metric(
            value=val,
            normal_range=f"<= {config.max_neg_control_methylation}",
            passed=passed,
            message="Negative-control interval methylation (EM-Seq conversion/capture sanity).",
        )

    overall_pass = all(bool(item.get("pass")) for item in results.values())
    return {
        "metrics": results,
        "overall_pass": overall_pass,
    }
