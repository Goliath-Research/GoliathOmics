"""Operator-tunable knobs for actionConfig.methyl_extract (linear BAM extract)."""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class MethylExtractReadLevelConfig(BaseModel):
    """Read-level pattern sidecar knobs under actionConfig.methyl_extract.read_level."""

    model_config = ConfigDict(extra="allow")

    enabled: Optional[bool] = Field(
        default=None,
        description=(
            "Emit {chrom}-{ctx}.patterns.h5 tile histograms. Operator-set per site/profile."
        ),
    )
    tile_size: Optional[int] = Field(
        default=None,
        ge=2,
        le=8,
        description="CpG sites per read-level tile. Operator-set per site/profile.",
    )


class MethylExtractMhapConfig(BaseModel):
    """Per-read haplotype sidecar knobs under actionConfig.methyl_extract.mhap."""

    model_config = ConfigDict(extra="allow")

    enabled: Optional[bool] = Field(
        default=None,
        description="Emit {chrom}-CG.mhap.h5 haplotype sidecars. Operator-set per site/profile.",
    )


class MethylExtractStepConfig(BaseModel):
    """Settings under actionConfig.methyl_extract / resolvedConfig for sample.methyl_extract.

    Tunable knobs use default=None (config-not-code). extra=allow keeps chrom_mapping,
    extractor_bin, contig_naming, and other operational keys.
    """

    model_config = ConfigDict(extra="allow")

    extract_contexts: Optional[List[str]] = Field(
        default=None,
        description="Contexts to extract (CG always implicit). Operator-set per site/profile.",
    )
    threads: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Total MethylExtractor worker budget (--threads). Split across in-flight "
            "chromosomes. Operator-set per site/profile."
        ),
    )
    chrom_parallel: Optional[int] = Field(
        default=None,
        ge=1,
        le=24,
        description=(
            "Max chromosomes processed concurrently (--chrom-parallel). Memory-gated by "
            "max_rss_gb. Operator-set per site/profile."
        ),
    )
    max_rss_gb: Optional[int] = Field(
        default=None,
        ge=1,
        le=512,
        description=(
            "Peak RSS gate in GiB for chrom-parallel (--max-rss-gb). Operator-set per site."
        ),
    )
    min_mapq: Optional[int] = Field(
        default=None,
        ge=0,
        description="Minimum mapping quality. Operator-set per site/profile.",
    )
    min_phred: Optional[int] = Field(
        default=None,
        ge=0,
        description="Minimum base Phred. Operator-set per site/profile.",
    )
    min_cov: Optional[int] = Field(
        default=None,
        ge=0,
        description="Minimum coverage to write a site. Operator-set per site/profile.",
    )
    cap_cov: Optional[int] = Field(
        default=None,
        ge=0,
        description="Optional coverage cap. Operator-set per site/profile.",
    )
    compression: Optional[int] = Field(
        default=None,
        ge=0,
        le=19,
        description="HDF5 Zstd (gzip fallback) level. Operator-set per site/profile.",
    )
    chunk_size: Optional[int] = Field(
        default=None,
        ge=1,
        description="HDF5 chunk size. Operator-set per site/profile.",
    )
    output_format: Optional[str] = Field(
        default=None,
        description="hdf5, txt, or both. Operator-set per site/profile.",
    )
    split: Optional[bool] = Field(
        default=None,
        description="Split output by context ({chrom}-{ctx}.h5). Operator-set per site/profile.",
    )
    read_level: Optional[Union[bool, MethylExtractReadLevelConfig]] = Field(
        default=None,
        description="Read-level pattern sidecars. Operator-set per site/profile.",
    )
    tile_size: Optional[int] = Field(
        default=None,
        ge=2,
        le=8,
        description="Alias for read_level.tile_size. Operator-set per site/profile.",
    )
    reference_fasta: Optional[str] = Field(
        default=None,
        description="Linear FASTA path. Usually from site reference_genome.fasta.",
    )
    target_panel_bed: Optional[str] = Field(
        default=None,
        description="Optional BED to restrict extract (EM-Seq / hybrid-capture).",
    )
    mhap: Optional[Union[bool, MethylExtractMhapConfig]] = Field(
        default=None,
        description=(
            "Emit {chrom}-CG.mhap.h5 per-read haplotype sidecars in the same extract pass. "
            "Operator-set per site/profile/procedure."
        ),
    )
