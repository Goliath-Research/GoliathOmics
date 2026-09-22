"""Resolve per-sample BAM paths from project layout."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import FragmentomicsStepConfig


def resolve_sample_bams(
    sample_dirs: List[str],
    cfg: FragmentomicsStepConfig,
) -> List[Tuple[str, Path]]:
    """
    Return (sample_id, bam_path) pairs.

    Priority: explicit sample_bam_paths, then {sample_dir}/{basename}{bam_suffix},
    then single *.bam in sample_dir.
    """
    if cfg.sample_bam_paths:
        out: List[Tuple[str, Path]] = []
        for sid, raw in cfg.sample_bam_paths.items():
            p = Path(raw)
            if p.is_file():
                out.append((str(sid), p))
        return out

    pairs: List[Tuple[str, Path]] = []
    suffix = cfg.bam_suffix or ".bam"
    for raw_dir in sample_dirs:
        sample_dir = Path(raw_dir)
        sample_id = sample_dir.name
        direct = sample_dir / f"{sample_id}{suffix}"
        if direct.is_file():
            pairs.append((sample_id, direct))
            continue
        bams = sorted(sample_dir.glob(f"*{suffix}"))
        if len(bams) == 1:
            pairs.append((sample_id, bams[0]))
    return pairs


def missing_bam_message(sample_dirs: List[str], cfg: FragmentomicsStepConfig) -> Optional[str]:
    resolved = resolve_sample_bams(sample_dirs, cfg)
    if resolved:
        return None
    return (
        "No BAM files resolved. Set step_config.fragmentomics.sample_bam_paths or place "
        f"{{sample_dir}}/{{sample_name}}{cfg.bam_suffix} beside alignment QC inputs."
    )
