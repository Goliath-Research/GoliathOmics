"""Run and parse samtools flagstat for BAM alignment QC."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..models.config import AlignmentGuardrailsConfig
from ..models.sample_qc import AlignmentFlagstat

_FLAGSTAT_LINE = re.compile(
    r"^(\d+)\s+\+\s+(\d+)\s+(.+)$",
)


def parse_flagstat_text(text: str) -> Dict[str, int]:
    """Parse samtools flagstat output into named counters."""
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _FLAGSTAT_LINE.match(line)
        if not match:
            continue
        primary = int(match.group(1))
        secondary = int(match.group(2))
        label = match.group(3).strip().lower()
        total = primary + secondary
        if "in total" in label:
            counts["total_reads"] = total
        elif label == "secondary":
            counts["secondary_reads"] = total
        elif label == "supplementary":
            counts["supplementary_reads"] = total
        elif label == "duplicates":
            counts["duplicate_reads"] = total
        # Exact "mapped" / "mapped (...%)" only — do not match
        # "with itself and mate mapped" (endswith "mapped").
        elif label == "mapped" or label.startswith("mapped ("):
            counts["mapped_reads"] = total
        elif "properly paired" in label:
            counts["properly_paired_reads"] = total
        elif "read1" in label and "read2" not in label:
            counts["read1_reads"] = total
        elif "read2" in label:
            counts["read2_reads"] = total
        elif "singletons" in label:
            counts["singleton_reads"] = total
        elif "with itself and mate mapped" in label:
            counts["mate_mapped_reads"] = total
    return counts


def _build_flagstat_metrics(counts: Dict[str, int]) -> AlignmentFlagstat:
    total = int(counts.get("total_reads", 0) or 0)
    properly_paired = int(counts.get("properly_paired_reads", 0) or 0)
    supplementary = int(counts.get("supplementary_reads", 0) or 0)
    mapped = int(counts.get("mapped_reads", 0) or 0)

    return AlignmentFlagstat(
        total_reads=counts.get("total_reads"),
        mapped_reads=counts.get("mapped_reads"),
        properly_paired_reads=counts.get("properly_paired_reads"),
        supplementary_reads=counts.get("supplementary_reads"),
        secondary_reads=counts.get("secondary_reads"),
        duplicate_reads=counts.get("duplicate_reads"),
        properly_paired_rate=round(properly_paired / total, 6) if total > 0 else None,
        supplementary_rate=round(supplementary / total, 6) if total > 0 else None,
        mapped_rate=round(mapped / total, 6) if total > 0 else None,
    )


def _validate_bam_for_flagstat(bam_path: Path) -> None:
    """Reject missing/empty/trivial files before invoking samtools."""
    bam_size = bam_path.stat().st_size
    if bam_size == 0:
        raise RuntimeError(
            f"BAM is empty (0 bytes); cannot run flagstat: {bam_path}. "
            "Re-run alignment or restore the BAM from archive."
        )
    if bam_size < 18:
        raise RuntimeError(
            f"BAM is too small to be valid ({bam_size} bytes): {bam_path}"
        )
    with open(bam_path, "rb") as fh:
        magic = fh.read(2)
    # On-disk BAM is BGZF (gzip) blocks; the BAM\\x01 magic is inside the first block,
    # not at file offset 0. Reject only obvious non-BAM prefixes here.
    if magic != b"\x1f\x8b":
        raise RuntimeError(
            f"File does not look like a BGZF-compressed BAM (missing gzip magic): {bam_path}"
        )


def flagstat_bam_preflight_error(bam_path: Path) -> Optional[str]:
    """Return a flagstat failure reason for ``bam_path``, or None when it looks runnable."""
    if not bam_path.is_file():
        return f"BAM not found for flagstat: {bam_path}"
    try:
        _validate_bam_for_flagstat(bam_path)
    except RuntimeError as exc:
        return str(exc)
    return None


def run_flagstat(
    sample_dir: Path,
    sample_id: str,
    *,
    force: bool = False,
) -> AlignmentFlagstat:
    """
    Run samtools flagstat on {sample_id}.bam; cache as {sample_id}.flagstat.txt.

    Returns typed alignment flagstat metrics. Raises RuntimeError when BAM missing
    or samtools/flagstat fails.
    """
    sample_dir = Path(sample_dir)
    bam_path = sample_dir / f"{sample_id}.bam"
    cache_path = sample_dir / f"{sample_id}.flagstat.txt"

    if not bam_path.is_file():
        raise RuntimeError(f"BAM not found for flagstat: {bam_path}")

    _validate_bam_for_flagstat(bam_path)

    samtools = shutil.which("samtools")
    if samtools is None:
        raise RuntimeError("samtools not found on PATH; required for alignment flagstat QC")

    if cache_path.is_file() and not force:
        bam_mtime = bam_path.stat().st_mtime
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime >= bam_mtime:
            text = cache_path.read_text(encoding="utf-8")
            return _build_flagstat_metrics(parse_flagstat_text(text))

    proc = subprocess.run(
        [samtools, "flagstat", str(bam_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"samtools flagstat failed for {bam_path}: {err}")

    text = proc.stdout or ""
    cache_path.write_text(text, encoding="utf-8")
    return _build_flagstat_metrics(parse_flagstat_text(text))


def apply_flagstat_guardrails(
    report: Dict[str, Any],
    flagstat: Optional[AlignmentFlagstat],
    cfg: AlignmentGuardrailsConfig,
    *,
    error: Optional[str] = None,
) -> None:
    """Append Phase-2 flagstat guardrails when cfg.flagstat_enabled."""
    if not cfg.flagstat_enabled:
        return

    details = report.setdefault("details", {})

    def _fail_metric(key: str, normal_range: str, message: str) -> None:
        details[key] = {
            "value": None,
            "normal_range": normal_range,
            "pass": False,
            "message": message,
        }
        report["overall_pass"] = False

    if error is not None:
        if cfg.min_properly_paired_rate is not None:
            _fail_metric(
                "properly_paired_rate",
                f">= {cfg.min_properly_paired_rate}",
                error,
            )
        if cfg.max_supplementary_rate_flagstat is not None:
            _fail_metric(
                "supplementary_rate_flagstat",
                f"<= {cfg.max_supplementary_rate_flagstat}",
                error,
            )
        return

    if flagstat is None:
        return

    if cfg.min_properly_paired_rate is not None:
        rate = flagstat.properly_paired_rate
        if rate is None:
            _fail_metric(
                "properly_paired_rate",
                f">= {cfg.min_properly_paired_rate}",
                "flagstat output missing properly paired count.",
            )
        else:
            passed = float(rate) >= cfg.min_properly_paired_rate
            details["properly_paired_rate"] = {
                "value": float(rate),
                "normal_range": f">= {cfg.min_properly_paired_rate}",
                "pass": passed,
                "message": (
                    "Fraction of reads in proper pairs (samtools flagstat). "
                    "Low values indicate pairing or mapping problems."
                ),
            }
            if not passed:
                report["overall_pass"] = False

    if cfg.max_supplementary_rate_flagstat is not None:
        rate = flagstat.supplementary_rate
        if rate is None:
            _fail_metric(
                "supplementary_rate_flagstat",
                f"<= {cfg.max_supplementary_rate_flagstat}",
                "flagstat output missing supplementary count.",
            )
        else:
            passed = float(rate) <= cfg.max_supplementary_rate_flagstat
            details["supplementary_rate_flagstat"] = {
                "value": float(rate),
                "normal_range": f"<= {cfg.max_supplementary_rate_flagstat}",
                "pass": passed,
                "message": (
                    "Supplementary alignments as a fraction of total reads (samtools flagstat)."
                ),
            }
            if not passed:
                report["overall_pass"] = False

    if not report.get("overall_pass") and str(report.get("recommendation", "")).startswith("PASS"):
        report["recommendation"] = (
            "FAIL: Do NOT proceed. Investigate BAM pairing/supplementary alignment "
            "before methylation extraction."
        )
