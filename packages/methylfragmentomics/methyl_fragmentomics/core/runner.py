"""Run fragmentomics modes for all project samples."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ..config import FragmentomicsStepConfig
from .bam_resolve import missing_bam_message, resolve_sample_bams
from .end_motifs import compute_end_motifs, summarize_end_motifs, write_end_motifs_tsv
from .wps import compute_wps_bins, summarize_wps, write_wps_tsv


def _chrom_set(cfg: FragmentomicsStepConfig, project_chromosomes: Optional[List[str]]) -> Optional[Set[str]]:
    chroms = cfg.chromosomes or project_chromosomes
    if not chroms:
        return None
    out: Set[str] = set()
    for c in chroms:
        s = str(c).strip()
        if not s:
            continue
        if s.startswith("chr"):
            out.add(s)
            out.add(s[3:])
        else:
            out.add(s)
            out.add(f"chr{s}")
    return out or None


def run_fragmentomics_for_samples(
    sample_dirs: List[str],
    output_dir: Path,
    cfg: FragmentomicsStepConfig,
    *,
    project_chromosomes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Process all resolved BAMs; write per-sample artifacts under output_dir."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    miss = missing_bam_message(sample_dirs, cfg)
    if miss:
        raise FileNotFoundError(miss)

    chromosomes = _chrom_set(cfg, project_chromosomes)
    max_reads = cfg.max_reads_per_sample
    modes = {str(m).strip().lower() for m in (cfg.modes or [])}
    k = int(cfg.end_motif_k)
    bin_bp = int(cfg.wps_bin_bp)

    summary: Dict[str, Any] = {
        "schema_version": "fragmentomics_run_v1",
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "modes": sorted(modes),
        "samples": {},
    }

    for sample_id, bam_path in resolve_sample_bams(sample_dirs, cfg):
        sample_out = output_dir / sample_id
        sample_out.mkdir(parents=True, exist_ok=True)
        features: Dict[str, Any] = {
            "sample_id": sample_id,
            "bam_path": str(bam_path),
            "modes": [],
        }

        if "end_motifs" in modes:
            counts = compute_end_motifs(
                bam_path,
                k=k,
                chromosomes=chromosomes,
                max_reads=max_reads,
            )
            write_end_motifs_tsv(counts, sample_out / "end_motifs.tsv")
            features["end_motifs"] = summarize_end_motifs(counts, k)
            features["modes"].append("end_motifs")

        if "wps" in modes:
            bins = compute_wps_bins(
                bam_path,
                bin_bp=bin_bp,
                chromosomes=chromosomes,
                max_reads=max_reads,
            )
            write_wps_tsv(bins, sample_out / "wps_bins.tsv", bin_bp=bin_bp)
            features["wps"] = summarize_wps(bins, bin_bp)
            features["modes"].append("wps")

        features_path = sample_out / "sample_features.json"
        features_path.write_text(json.dumps(features, indent=2), encoding="utf-8")
        summary["samples"][sample_id] = {
            "sample_features": str(features_path.name),
            "modes": features["modes"],
        }

    summary_path = output_dir / "fragmentomics_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
