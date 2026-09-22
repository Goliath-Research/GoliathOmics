#!/usr/bin/env python3
"""
WGBS Parabricks JSON QC Guardrail Checker (Updated with Pre-Adapter Artifacts)

Enforces sequencing + alignment + deamination/artifact guardrails for WGBS.
Run this BEFORE methylation extraction (MethylExtractor on linear/stock-pangenome BAMs).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..models.config import CoreGuardrailsConfig

def load_parabricks_json(json_path: str) -> Dict[str, Any]:
    """Load the Parabricks metrics JSON."""
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def _make_guardrail_metric(
    *,
    value: float,
    normal_range: str,
    passed: bool,
    message: str,
) -> Dict[str, Any]:
    """Build one metric payload with user-facing guidance."""
    return {
        "value": value,
        "normal_range": normal_range,
        "pass": passed,
        "message": message,
    }


def _build_wgbs_guardrail_report(
    data: Dict[str, Any],
    q30_threshold: Optional[float] = None,
    core_guardrails: Optional["CoreGuardrailsConfig"] = None,
) -> Dict[str, Any]:
    """Build guardrail report from already-loaded Parabricks JSON payload.

    ``core_guardrails`` carries operator-set thresholds; ``q30_threshold``, when given,
    overrides its Q30 value so the CLI flag keeps working.
    """
    from ..models.config import CoreGuardrailsConfig

    cfg = core_guardrails or CoreGuardrailsConfig()
    if q30_threshold is None:
        q30_threshold = cfg.min_q30_percent

    qy = data["quality_yield"]
    mq = data["mean_quality_by_cycle"]["mean_quality"]
    gc = data["gc_bias_summary"]
    ins = data["insert_size_metrics"]
    pre = data.get("pre_adapter_summaries", {})

    # Core sequencing metrics
    pf_pct = (qy["pf_reads"] / qy["total_reads"]) * 100
    q30_pct = (qy["pf_q30_bases"] / qy["pf_bases"]) * 100
    mean_qual = sum(mq) / len(mq)
    min_qual_post20 = min(mq[20:]) if len(mq) > 20 else min(mq)

    # GC bias
    at_dropout = gc["at_dropout"]
    gc_dropout = gc["gc_dropout"]

    # Insert size
    median_insert = ins["median_insert_size"]

    # Pre-adapter artifact metrics (Deamination & OxoG)
    artifact_names: List[str] = pre.get("ARTIFACT_NAME", [])
    qscores: List[int] = pre.get("TOTAL_QSCORE", [])

    deam_idx = next((i for i, name in enumerate(artifact_names) if name == "Deamination"), None)
    oxog_idx = next((i for i, name in enumerate(artifact_names) if name == "OxoG"), None)

    deam_score = qscores[deam_idx] if deam_idx is not None else 100
    oxog_score = qscores[oxog_idx] if oxog_idx is not None else 100

    # Compile results against operator-set thresholds
    pf_pass = pf_pct >= cfg.min_pf_percent
    q30_pass = q30_pct >= q30_threshold
    mean_qual_pass = mean_qual >= cfg.min_mean_quality
    min_qual_post20_pass = min_qual_post20 >= cfg.min_quality_post20
    at_dropout_pass = at_dropout < cfg.max_at_dropout
    gc_dropout_pass = gc_dropout < cfg.max_gc_dropout
    median_insert_pass = cfg.median_insert_min_bp <= median_insert <= cfg.median_insert_max_bp
    deam_pass = deam_score <= cfg.max_deamination_qscore
    oxog_pass = oxog_score >= cfg.min_oxog_qscore

    results = {
        "pf_percent": _make_guardrail_metric(
            value=round(pf_pct, 2),
            normal_range=f">= {cfg.min_pf_percent}",
            passed=pf_pass,
            message="Percentage of reads passing Illumina PF filtering. Low values can indicate run-level quality issues.",
        ),
        "q30_percent": _make_guardrail_metric(
            value=round(q30_pct, 2),
            normal_range=f">= {q30_threshold}",
            passed=q30_pass,
            message="Percentage of PF bases with Q30 or better. This reflects confidence in base calls for downstream analysis.",
        ),
        "mean_quality": _make_guardrail_metric(
            value=round(mean_qual, 2),
            normal_range=f">= {cfg.min_mean_quality}",
            passed=mean_qual_pass,
            message="Average Phred quality across cycles. Lower values indicate noisier reads and weaker sequencing confidence.",
        ),
        "min_quality_post20": _make_guardrail_metric(
            value=round(min_qual_post20, 2),
            normal_range=f">= {cfg.min_quality_post20}",
            passed=min_qual_post20_pass,
            message="Minimum quality after cycle 20. Captures late-cycle degradation that can hurt alignment and methylation calls.",
        ),
        "at_dropout": _make_guardrail_metric(
            value=round(at_dropout, 3),
            normal_range=f"< {cfg.max_at_dropout}",
            passed=at_dropout_pass,
            message="AT-rich region dropout. High values suggest uneven representation of AT-rich genomic content.",
        ),
        "gc_dropout": _make_guardrail_metric(
            value=round(gc_dropout, 3),
            normal_range=f"< {cfg.max_gc_dropout}",
            passed=gc_dropout_pass,
            message="GC-rich region dropout. High values suggest uneven representation of GC-rich genomic content.",
        ),
        "median_insert_bp": _make_guardrail_metric(
            value=median_insert,
            normal_range=f"{cfg.median_insert_min_bp}-{cfg.median_insert_max_bp}",
            passed=median_insert_pass,
            message="Median insert size (bp). Out-of-range values can indicate library preparation or fragmentation issues.",
        ),
        "deamination_qscore": _make_guardrail_metric(
            value=deam_score,
            normal_range=f"<= {cfg.max_deamination_qscore}",
            passed=deam_pass,
            message="Parabricks deamination qscore. In WGBS, lower scores are expected and reflect bisulfite conversion signal.",
        ),
        "oxog_qscore": _make_guardrail_metric(
            value=oxog_score,
            normal_range=f">= {cfg.min_oxog_qscore}",
            passed=oxog_pass,
            message="Parabricks OxoG qscore. Lower values indicate higher oxidative G>T damage risk.",
        ),
    }

    overall_pass = all(r["pass"] for r in results.values())

    return {
        "sample_id": data.get("sample_id", "unknown"),
        "overall_pass": overall_pass,
        "details": results,
        "recommendation": (
            "PASS: Safe to proceed to methylation extraction (MethylExtractor)."
            if overall_pass
            else "FAIL: Do NOT proceed. Investigate library prep, sequencing, or bisulfite conversion."
        ),
        "next_steps": (
            "Run MethylExtractor (sample.methyl_extract), then sample.extraction_qc. "
            "Still verify quantitative conversion rate with lambda spike-in (≥99%) or non-CpG methylation (≤1–2%)."
        ),
    }


def apply_optional_guardrails(
    report: Dict[str, Any],
    payload: Dict[str, Any],
    *,
    duplication_rate_max: Optional[float] = None,
    min_pf_reads: Optional[int] = None,
) -> None:
    """Append config-gated guardrails to an existing report dict (mutates in place)."""
    details = report.setdefault("details", {})

    if duplication_rate_max is not None:
        summary = payload.get("summary_stats") or {}
        dup_rate = float(summary.get("duplication_rate", 0.0))
        passed = dup_rate <= duplication_rate_max
        details["duplication_rate"] = _make_guardrail_metric(
            value=round(dup_rate, 4),
            normal_range=f"<= {duplication_rate_max}",
            passed=passed,
            message="Library duplication rate from Picard dedup metrics.",
        )
        if not passed:
            report["overall_pass"] = False

    if min_pf_reads is not None:
        qy = payload.get("quality_yield") or {}
        pf_reads = int(qy.get("pf_reads", 0))
        passed = pf_reads >= min_pf_reads
        details["min_pf_reads"] = _make_guardrail_metric(
            value=float(pf_reads),
            normal_range=f">= {min_pf_reads}",
            passed=passed,
            message="Pass-filter read count; critically low depth may invalidate analysis.",
        )
        if not passed:
            report["overall_pass"] = False

    if not report.get("overall_pass"):
        if report.get("recommendation", "").startswith("PASS"):
            report["recommendation"] = (
                "FAIL: Do NOT proceed. Investigate library prep, sequencing, or bisulfite conversion."
            )


def _print_wgbs_guardrail_report(report: Dict[str, Any]) -> None:
    """Pretty print WGBS guardrail report."""
    sample_id = report.get("sample_id", "unknown")
    results = report.get("details", {})
    overall_pass = report.get("overall_pass", False)
    print(f"WGBS Parabricks QC Guardrail Report – Sample: {sample_id}")
    print("=" * 80)
    for metric, res in results.items():
        status = "PASS" if res["pass"] else "FAIL"
        print(f"{metric:22} {res['value']:>8}   {res['normal_range']:>15}   {status}")
        print(f"   - {res['message']}")
    print("=" * 80)
    print(report["recommendation"])
    print(f"Overall QC Status: {'PASS' if overall_pass else 'FAIL'}")
    print("\nNext steps:", report["next_steps"])


def check_wgbs_guardrails(
    json_path: str,
    q30_threshold: Optional[float] = None,
    print_report: bool = True,
    core_guardrails: Optional["CoreGuardrailsConfig"] = None,
) -> Dict[str, Any]:
    """
    Run all guardrails and return a detailed report.
    q30_threshold: 85.0 (strict) or 80.0 (relaxed) for % Q30 bases.
    """
    data = load_parabricks_json(json_path)
    report = _build_wgbs_guardrail_report(
        data, q30_threshold=q30_threshold, core_guardrails=core_guardrails
    )
    if print_report:
        _print_wgbs_guardrail_report(report)
    return report


def write_guardrails_to_json(
    json_path: str,
    guardrail_report: Dict[str, Any],
    output_path: Optional[str] = None,
) -> Path:
    """Write guardrail report into JSON under key `guardrails`."""
    source_path = Path(json_path)
    target_path = Path(output_path) if output_path else source_path
    data = load_parabricks_json(str(source_path))
    data["guardrails"] = guardrail_report
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return target_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate WGBS Parabricks guardrails and store them in JSON.",
    )
    parser.add_argument("json_path", help="Path to Parabricks metrics JSON file")
    parser.add_argument(
        "--q30-threshold",
        type=float,
        default=None,
        help="Override the q30_percent guardrail threshold (default: from alignment_qc config)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional output JSON path. If omitted, update input file in place.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    json_file = args.json_path
    if not Path(json_file).is_file():
        print(f"Error: File not found → {json_file}")
        sys.exit(1)

    report = check_wgbs_guardrails(json_file, q30_threshold=args.q30_threshold)
    written_path = write_guardrails_to_json(json_file, report, output_path=args.output)
    print(f"\nGuardrails written to: {written_path}")