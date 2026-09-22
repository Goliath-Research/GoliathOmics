"""Published alignment QC summary (schema 2.1).

This is a **disk export**, not a workflow action I/O model.

Workers bind ``MethylQcTaskInput`` / ``MethylQcTaskOutput``. Picard cycle, GC,
insert-size, and duplication histograms stay in the sample-dir Parabricks
``{id}.json`` or ``{id}.qc-metrics.tar``. Analysis must read
``guardrails.details`` (treat nested ``bisulfite_conversion`` as a heading, not
a second copy of ``deamination_qscore``).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .sample_qc import (
    AlignmentFlagstat,
    AlignmentStats,
    BisulfiteConversionMetrics,
    ConversionLog,
    DuplicationMetric,
    FragmentomicsMetrics,
    GCBiasSummary,
    GuardrailReport,
    InsertSizeMetrics,
    QcAttemptRecord,
    QualityYield,
    SummaryStats,
    WgbsAlignMetrics,
)

SCHEMA_NAME = "methylalignmentqc.sample_qc"
SCHEMA_VERSION = "2.1.0"
EXPORT_KIND = "guardrail_summary"

# Tables computed from at QC time, then dropped from the published file.
PICARD_TABLE_KEYS = (
    "mean_quality_by_cycle",
    "quality_score_distribution",
    "base_distribution_by_cycle",
    "gc_bias_details",
    "insert_size_histogram",
    "error_summaries",
    "pre_adapter_summaries",
    "bait_bias_summaries",
    "duplication_histogram",
)


class QCV2Producer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package: str = Field(description="Producer package name")
    version: str = Field(description="Producer package version")


class QCV2Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str = Field(default=SCHEMA_NAME)
    schema_version: str = Field(default=SCHEMA_VERSION)
    export_kind: Literal["guardrail_summary"] = Field(
        default="guardrail_summary",
        description=(
            "Guardrail summary written to disk. Not a sample.methyl_qc task "
            "input or output model; histograms remain in Picard native files."
        ),
    )
    exported_at_utc: str = Field(description="ISO-8601 UTC timestamp when the export was produced")
    producer: QCV2Producer
    qc_attempt: Optional[int] = Field(default=None, description="Current QC evaluation attempt number")


class ExportedSampleQCV2Payload(BaseModel):
    """Slim per-sample QC JSON (V2.1) for operators and ``qcPath``.

    Do not use this model as ``MethylQcTaskInput`` / catalog ``resolvedConfig``.
    """

    model_config = ConfigDict(extra="forbid")

    metadata: QCV2Metadata
    sample_id: str
    quality_yield: Optional[QualityYield] = None
    gc_bias_summary: Optional[GCBiasSummary] = None
    insert_size_metrics: Optional[InsertSizeMetrics] = None
    conversion_log: Optional[ConversionLog] = None
    duplication_metrics: List[DuplicationMetric]
    summary_stats: SummaryStats
    guardrails: GuardrailReport
    alignment_stats: Optional[AlignmentStats] = None
    alignment_flagstat: Optional[AlignmentFlagstat] = None
    fragmentomics_metrics: Optional[FragmentomicsMetrics] = None
    bisulfite_conversion_metrics: Optional[BisulfiteConversionMetrics] = None
    qc_history: Optional[List[QcAttemptRecord]] = None
    sample_prep_log_path: Optional[str] = None
    wgbs_align_metrics: Optional[WgbsAlignMetrics] = None
