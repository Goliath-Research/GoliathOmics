"""Pydantic models for step_config.fragmentomics in project JSON."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FragmentomicsStepConfig(BaseModel):
    """Settings under step_config.fragmentomics."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Run methyl-fragmentomics on project samples.")
    output_dir: Optional[str] = Field(
        default=None,
        description="Output root; default {project_root}/fragmentomics.",
    )
    modes: List[Literal["wps", "end_motifs"]] = Field(
        default_factory=lambda: ["wps", "end_motifs"],
        description="Fragmentomics modes to compute.",
    )
    sample_bam_paths: Optional[Dict[str, str]] = Field(
        default=None,
        description="Explicit map sample_id -> BAM path.",
    )
    bam_suffix: str = Field(
        default=".bam",
        description="When resolving BAM from sample dir, try {sample_dir}/{basename}{bam_suffix}.",
    )
    genome_fasta: Optional[str] = Field(
        default=None,
        description="Reference FASTA; defaults to step_config.alignment_qc.genome_fasta.",
    )
    wps_bin_bp: int = Field(default=1000, ge=100, description="Genomic bin size for WPS aggregation.")
    wps_window_bp: int = Field(
        default=2000,
        ge=100,
        description="Documented smoothing window (reserved for future smoothing).",
    )
    end_motif_k: int = Field(default=4, ge=2, le=6, description="K-mer length at fragment 5' ends.")
    max_reads_per_sample: Optional[int] = Field(
        default=2_000_000,
        ge=1000,
        description="Cap aligned reads scanned per sample (None = no cap).",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Restrict scanning to these chromosomes; default from project chromosomes.",
    )
