"""Guardrails for pangenome_wgbs (methylGrapher) alignment QC."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .wgbs_parabricks_qc import _make_guardrail_metric


def _path_exists_nonempty(path: Optional[Path]) -> bool:
    return path is not None and path.is_file() and path.stat().st_size > 0


def build_wgbs_pangenome_guardrail_report(
    *,
    sample_id: str,
    sample_dir: Path,
    provenance: Dict[str, Any],
    flagstat: Optional[Dict[str, Any]] = None,
    min_mapped_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build guardrail report from methylGrapher artifacts (no Parabricks tables).

    Applied metrics vote on ``overall_pass``. Missing optional flagstat mapping
    threshold skips that key rather than failing closed.
    """
    sample_dir = Path(sample_dir)
    details: Dict[str, Any] = {}

    tool_ok = "methylgrapher" in str(provenance.get("tool") or "").lower()
    fingerprints = provenance.get("asset_fingerprints") or {}
    fp_ok = isinstance(fingerprints, dict) and len(fingerprints) > 0
    gaf_field = provenance.get("gaf")
    bam_field = provenance.get("bam")
    provenance_pass = bool(tool_ok and fp_ok and gaf_field and bam_field)
    details["wgbs_provenance"] = _make_guardrail_metric(
        value=1.0 if provenance_pass else 0.0,
        normal_range="tool=methylGrapher + fingerprints + gaf/bam paths",
        passed=provenance_pass,
        message=(
            "methylGrapher Align provenance must record tool, asset fingerprints, "
            "and paths to GAF/BAM outputs."
        ),
    )

    gaf_path = Path(str(gaf_field)) if gaf_field else sample_dir / f"{sample_id}.alignment.gaf"
    if not gaf_path.is_absolute():
        gaf_path = sample_dir / gaf_path.name
    gaf_ok = _path_exists_nonempty(gaf_path)
    details["wgbs_gaf_present"] = _make_guardrail_metric(
        value=float(gaf_path.stat().st_size) if gaf_ok else 0.0,
        normal_range="GAF file size > 0",
        passed=gaf_ok,
        message="Dual-graph Align must produce a non-empty alignment.gaf.",
    )

    bam_path = Path(str(bam_field)) if bam_field else sample_dir / f"{sample_id}.bam"
    if not bam_path.is_absolute():
        bam_path = sample_dir / bam_path.name
    bam_ok = _path_exists_nonempty(bam_path)
    details["wgbs_bam_present"] = _make_guardrail_metric(
        value=float(bam_path.stat().st_size) if bam_ok else 0.0,
        normal_range="BAM file size > 0",
        passed=bam_ok,
        message="QC BAM from methylGrapher Align must be present and non-empty.",
    )

    if flagstat is not None and min_mapped_rate is not None:
        mapped_rate = flagstat.get("mapped_rate")
        if mapped_rate is not None:
            rate = float(mapped_rate)
            mapped_pass = rate >= float(min_mapped_rate)
            details["wgbs_bam_mapped_rate"] = _make_guardrail_metric(
                value=round(rate, 4),
                normal_range=f">= {min_mapped_rate}",
                passed=mapped_pass,
                message=(
                    "Flagstat mapped rate on methylGrapher QC BAM "
                    "(C2T-surjected; expect lower than linear)."
                ),
            )

    overall_pass = all(bool(m.get("pass")) for m in details.values())
    return {
        "sample_id": sample_id,
        "overall_pass": overall_pass,
        "metrics_family": "methylgrapher_wgbs",
        "details": details,
        "recommendation": (
            "PASS: Safe to proceed to methylation extraction (methylGrapher MethylCall)."
            if overall_pass
            else "FAIL: Do NOT proceed. Investigate methylGrapher Align / QC BAM / GAF outputs."
        ),
        "next_steps": (
            "Run sample.methylgrapher_wgbs_extract when overall_pass is true."
            if overall_pass
            else "Re-run Align or inspect methylgrapher_work logs; do not extract."
        ),
    }
