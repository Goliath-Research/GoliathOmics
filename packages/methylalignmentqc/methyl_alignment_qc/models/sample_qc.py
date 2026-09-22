"""Pydantic models for sample QC JSON payloads."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class QualityYield(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_reads: int
    pf_reads: int
    total_bases: int
    pf_bases: int
    q20_bases: int
    pf_q20_bases: int
    q30_bases: int
    pf_q30_bases: int
    q20_equivalent_yield: int
    pf_q20_equivalent_yield: int


class MeanQualityByCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle: List[int]
    mean_quality: List[float]


class QualityScoreDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    Q: List[int]
    COUNT_OF_Q: List[int]


class BaseDistributionByCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cycle: List[int]
    PCT_A: List[float]
    PCT_C: List[float]
    PCT_G: List[float]
    PCT_T: List[float]
    PCT_N: List[float]


class GCBiasSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_dropout: float
    gc_dropout: float


class GCBiasDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    GC: List[int]
    WINDOWS: List[int]
    READ_STARTS: List[int]
    MEAN_BASE_QUALITY: List[float]
    NORMALIZED_COVERAGE: List[float]
    ERROR_BAR: List[float]


class InsertSizeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    median_insert_size: int
    mode_insert_size: int
    mean_insert_size: float
    standard_deviation: float
    read_pairs: int
    width_of_10_percent: int
    width_of_20_percent: int
    width_of_30_percent: int
    width_of_40_percent: int
    width_of_50_percent: int
    width_of_60_percent: int
    width_of_70_percent: int
    width_of_80_percent: int
    width_of_90_percent: int
    width_of_95_percent: int
    width_of_99_percent: int


class InsertSizeHistogram(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    insert_size: List[int]
    pair_orientation: List[str]
    all_reads_fr_count: List[int] = Field(alias="All_Reads.fr_count")
    VALUE: List[float] = Field(default_factory=list)
    all_sets: List[int] = Field(default_factory=list)
    optical_sets: List[int] = Field(default_factory=list)
    non_optical_sets: List[int] = Field(default_factory=list)


class ErrorSummaries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    REF: List[str]
    ALT: List[str]
    COUNT: List[int]
    RATE: List[float]
    QSCORE: List[int]


class ArtifactSummaries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ARTIFACT_NAME: List[str]
    TOTAL_QSCORE: List[int]
    WORST_CXT: List[str]
    WORST_CXT_QSCORE: List[int]


class DuplicationMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    LIBRARY: str
    UNPAIRED_READS_EXAMINED: int
    READ_PAIRS_EXAMINED: int
    SECONDARY_OR_SUPPLEMENTARY_RDS: int
    UNMAPPED_READS: int
    UNPAIRED_READ_DUPLICATES: int
    READ_PAIR_DUPLICATES: int
    READ_PAIR_OPTICAL_DUPLICATES: int
    PERCENT_DUPLICATION: float
    ESTIMATED_LIBRARY_SIZE: int


class DuplicationHistogram(BaseModel):
    model_config = ConfigDict(extra="forbid")
    BIN: List[float]
    VALUE: List[float]
    all_sets: List[int] = Field(default_factory=list)
    optical_sets: List[int] = Field(default_factory=list)
    non_optical_sets: List[int] = Field(default_factory=list)


class ConversionLog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program: str
    version: str
    start_time: str
    end_time: str
    total_time: str


class SummaryStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_reads: int
    duplication_rate: float
    estimated_library_size: int
    duplicate_reads: int
    optical_duplicates: int


class AlignmentStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads_examined: int
    unmapped_reads: int
    secondary_supplementary_reads: int
    mapping_rate: float
    secondary_supplementary_rate: float
    gc_coverage_uniformity: Optional[float] = None


class AlignmentFlagstat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_reads: Optional[int] = None
    mapped_reads: Optional[int] = None
    properly_paired_reads: Optional[int] = None
    supplementary_reads: Optional[int] = None
    secondary_reads: Optional[int] = None
    duplicate_reads: Optional[int] = None
    properly_paired_rate: Optional[float] = None
    supplementary_rate: Optional[float] = None
    mapped_rate: Optional[float] = None


class GuardrailMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Optional[float] = None
    normal_range: str
    passed: bool = Field(alias="pass")
    message: str


class BisulfiteConversionMetrics(BaseModel):
    """Provenance for conversion sidecar thresholds. Deamination lives only on
    ``guardrails.details.deamination_qscore`` — do not restate it here."""

    model_config = ConfigDict(extra="forbid")
    measurement_source: str
    conversion_rate_pct: Optional[float] = None
    non_cpg_methylation_pct: Optional[float] = None
    min_conversion_rate_pct: float
    max_non_cpg_methylation_pct: float
    notes: Optional[str] = None


class BisulfiteConversionGuardrailDetails(BaseModel):
    """Sidecar conversion checks only. Deamination is not nested here."""

    model_config = ConfigDict(extra="forbid")
    conversion_rate_pct: Optional[GuardrailMetric] = None
    non_cpg_methylation_pct: Optional[GuardrailMetric] = None


class FragmentomicsMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    median_insert_size: int
    short_fragment_fraction: float
    long_fragment_fraction: float
    nucleosome_peak_bp: Optional[int] = None
    short_fragment_max_bp: int
    nucleosome_peak_bp_min: int
    nucleosome_peak_bp_max: int


class FragmentomicsGuardrailDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    median_insert_bp: GuardrailMetric
    nucleosome_peak_bp: GuardrailMetric
    short_fragment_fraction: GuardrailMetric


class TrimSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: int
    end: str
    bases: int


class QcScreeningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: str
    quality_pattern: Optional[str] = None
    read_length: int
    r2_start_cycle: int
    trim_front1: int = 0
    trim_tail1: int = 0
    trim_front2: int = 0
    trim_tail2: int = 0
    trim_spec: Optional[TrimSpec] = None
    r2_start_mean_quality: Optional[float] = None
    r2_recovery_mean_quality: Optional[float] = None
    dip_regions: List[Dict[str, int]] = Field(default_factory=list)
    message: str
    bad_cycle_start: Optional[int] = None
    bad_cycle_end: Optional[int] = None
    n_cycles: Optional[int] = None


class QcAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int
    evaluated_at_utc: str
    alignment_pass: str
    reason: str
    trigger_disposition: Optional[str] = None
    trigger_action: Optional[str] = None
    trim_front1: Optional[int] = None
    trim_tail1: Optional[int] = None
    trim_front2: Optional[int] = None
    trim_tail2: Optional[int] = None
    overall_pass: bool
    disposition: str
    failed_guardrails: List[str] = Field(default_factory=list)
    workflow_node_key: Optional[str] = None


class WgbsAlignMetrics(BaseModel):
    """Provenance block from methylGrapher ``{sample}.alignment_metrics.json``."""

    model_config = ConfigDict(extra="allow")
    tool: str
    action: Optional[str] = None
    sample_id: Optional[str] = None
    index_prefix: Optional[str] = None
    directional: Optional[bool] = None
    asset_fingerprints: Optional[Dict[str, str]] = None
    gaf: Optional[str] = None
    bam: Optional[str] = None


class GuardrailDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Parabricks CollectMultipleMetrics core (required for linear/pangenome; absent on WGBS)
    pf_percent: Optional[GuardrailMetric] = None
    q30_percent: Optional[GuardrailMetric] = None
    mean_quality: Optional[GuardrailMetric] = None
    min_quality_post20: Optional[GuardrailMetric] = None
    at_dropout: Optional[GuardrailMetric] = None
    gc_dropout: Optional[GuardrailMetric] = None
    median_insert_bp: Optional[GuardrailMetric] = None
    deamination_qscore: Optional[GuardrailMetric] = None
    oxog_qscore: Optional[GuardrailMetric] = None
    duplication_rate: Optional[GuardrailMetric] = None
    min_pf_reads: Optional[GuardrailMetric] = None
    mapping_rate: Optional[GuardrailMetric] = None
    secondary_supplementary_rate: Optional[GuardrailMetric] = None
    gc_coverage_uniformity: Optional[GuardrailMetric] = None
    properly_paired_rate: Optional[GuardrailMetric] = None
    supplementary_rate_flagstat: Optional[GuardrailMetric] = None
    fragmentomics: Optional[FragmentomicsGuardrailDetails] = None
    bisulfite_conversion: Optional[BisulfiteConversionGuardrailDetails] = None
    # pangenome_wgbs (methylGrapher) specific
    wgbs_provenance: Optional[GuardrailMetric] = None
    wgbs_gaf_present: Optional[GuardrailMetric] = None
    wgbs_bam_present: Optional[GuardrailMetric] = None
    wgbs_bam_mapped_rate: Optional[GuardrailMetric] = None
    # MojoFq2bamMeth linear (samtools + placeholders)
    mojo_placeholder_fields_skipped: Optional[GuardrailMetric] = None


class GuardrailReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_id: str
    overall_pass: bool
    details: GuardrailDetails
    recommendation: str
    next_steps: str
    screening: Optional[QcScreeningReport] = None
    metrics_family: Optional[str] = None
    picard_enrichment: Optional[bool] = Field(
        default=None,
        description=(
            "True when pangenome_wgbs Align collected Picard tables and core "
            "Parabricks guardrails were merged into details."
        ),
    )
    picard_enrichment_note: Optional[str] = Field(
        default=None,
        description="Operator note for Picard enrichment / BS chemistry skew caveat.",
    )
    mojo_linear_note: Optional[str] = Field(
        default=None,
        description=(
            "Present when metrics_family=mojo_linear: placeholder Picard-shaped "
            "fields did not vote on overall_pass."
        ),
    )


class ParabricksMetricsPayload(BaseModel):
    """Picard/Parabricks tables used as QC *inputs*. Histograms stay here (and in
    ``{id}.qc-metrics.tar``); they are not copied into the published V2.1 export."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    quality_yield: Optional[QualityYield] = None
    mean_quality_by_cycle: Optional[MeanQualityByCycle] = None
    quality_score_distribution: Optional[QualityScoreDistribution] = None
    base_distribution_by_cycle: Optional[BaseDistributionByCycle] = None
    gc_bias_summary: Optional[GCBiasSummary] = None
    gc_bias_details: Optional[GCBiasDetails] = None
    insert_size_metrics: Optional[InsertSizeMetrics] = None
    insert_size_histogram: Optional[InsertSizeHistogram] = None
    error_summaries: Optional[ErrorSummaries] = None
    pre_adapter_summaries: Optional[ArtifactSummaries] = None
    bait_bias_summaries: Optional[ArtifactSummaries] = None
    conversion_log: Optional[ConversionLog] = None
    duplication_metrics: Optional[List[DuplicationMetric]] = None
    duplication_histogram: Optional[DuplicationHistogram] = None
    wgbs_align_metrics: Optional[WgbsAlignMetrics] = None


class ExportedSampleQCPayload(ParabricksMetricsPayload):
    """Internal V1-shaped assembly (includes Picard tables). Not action I/O.
    ``methyl-qc`` validates this in memory, then writes slim V2.1."""

    model_config = ConfigDict(extra="forbid")

    duplication_metrics: List[DuplicationMetric]
    duplication_histogram: DuplicationHistogram
    summary_stats: SummaryStats
    guardrails: GuardrailReport
    alignment_stats: Optional[AlignmentStats] = None
    alignment_flagstat: Optional[AlignmentFlagstat] = None
    fragmentomics_metrics: Optional[FragmentomicsMetrics] = None
    bisulfite_conversion_metrics: Optional[BisulfiteConversionMetrics] = None
    qc_history: Optional[List[QcAttemptRecord]] = None
    sample_prep_log_path: Optional[str] = None
