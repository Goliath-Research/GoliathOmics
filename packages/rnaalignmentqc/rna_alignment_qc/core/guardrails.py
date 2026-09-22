"""Pass/Fail guardrails for RNA-Seq alignment/quantification metrics."""

from __future__ import annotations

from typing import Any, Dict

from ..models.config import RnaQcGuardrailConfig


def _metric(*, value: Any, normal_range: str, passed: bool, message: str) -> Dict[str, Any]:
    return {"value": value, "normal_range": normal_range, "pass": passed, "message": message}


def evaluate_rna_guardrails(
    metrics: Dict[str, Any],
    *,
    config: RnaQcGuardrailConfig,
) -> Dict[str, Any]:
    quant_mode = metrics.get("quant_mode")
    results: Dict[str, Any] = {}

    input_reads = metrics.get("input_reads")
    if input_reads is not None:
        results["input_reads"] = _metric(
            value=int(input_reads),
            normal_range=f">= {config.min_input_reads}",
            passed=int(input_reads) >= config.min_input_reads,
            message="Total input read pairs presented to the quantifier.",
        )

    if quant_mode == "star":
        mapping_rate = metrics.get("mapping_rate")
        if mapping_rate is not None:
            results["mapping_rate"] = _metric(
                value=round(float(mapping_rate), 6),
                normal_range=f">= {config.min_mapping_rate}",
                passed=float(mapping_rate) >= config.min_mapping_rate,
                message="Fraction of reads STAR mapped (unique + multimapping).",
            )
        unique_rate = metrics.get("uniquely_mapped_rate")
        if unique_rate is not None:
            results["uniquely_mapped_rate"] = _metric(
                value=round(float(unique_rate), 6),
                normal_range=f">= {config.min_uniquely_mapped_rate}",
                passed=float(unique_rate) >= config.min_uniquely_mapped_rate,
                message="Fraction of reads uniquely mapped by STAR.",
            )
    elif quant_mode == "kallisto":
        pseudo = metrics.get("pseudoalignment_rate")
        if pseudo is not None:
            results["pseudoalignment_rate"] = _metric(
                value=round(float(pseudo), 6),
                normal_range=f">= {config.min_pseudoalignment_rate}",
                passed=float(pseudo) >= config.min_pseudoalignment_rate,
                message="Fraction of reads kallisto pseudoaligned to the transcriptome.",
            )

    genes_detected = metrics.get("genes_detected")
    if genes_detected is not None:
        results["genes_detected"] = _metric(
            value=int(genes_detected),
            normal_range=f">= {config.min_genes_detected}",
            passed=int(genes_detected) >= config.min_genes_detected,
            message="Number of genes/transcripts with non-zero quantification.",
        )

    if not results:
        results["metrics_present"] = _metric(
            value=None,
            normal_range="STAR Log.final.out or kallisto run_info.json present",
            passed=False,
            message="No RNA quantifier metrics found under the sample directory.",
        )

    overall_pass = all(bool(item.get("pass")) for item in results.values())
    return {"metrics": results, "overall_pass": overall_pass, "quant_mode": quant_mode}
