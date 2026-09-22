"""External stub handlers for dry-run workers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel

logger = logging.getLogger(__name__)

def _stub_external_enabled() -> bool:
    return os.environ.get("WORKER_STUB_EXTERNAL", "").lower() in {"1", "true", "yes"}


def _write_stub_extract_artifacts(sample_path: Path, sample_id: str) -> list[str]:
    h5_name = "21-CG.h5"
    h5_path = sample_path / h5_name
    if not h5_path.is_file():
        h5_path.write_bytes(b"stub-h5")
    manifest_path = sample_path / f"{sample_id}.extraction_manifest.json"
    if not manifest_path.is_file():
        manifest_path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "schema_name": "methylextractor.extraction_manifest",
                        "schema_version": "1.0.0",
                        "contexts_extracted": ["CG"],
                    },
                    "summary": {"cpg_weighted_mean_coverage": 20.0},
                    "per_chromosome": {"21": {"CG": {"mean_coverage": 18.0}}},
                }
            ),
            encoding="utf-8",
        )
    return [h5_name]


def _handle_stub_external(capability: str, _action_name: str, input: BaseModel) -> BaseModel:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    if not _stub_external_enabled():
        raise RuntimeError(
            f"No local handler for capability {capability!r}. "
            "Implement a domain worker or set WORKER_STUB_EXTERNAL=1 for dry-run."
        )

    from ..task_models.sample_prep_models import (
        ArchiveSampleTaskOutput,
        DeleteTaskOutput,
        DownloadFastqTaskOutput,
        ExtractionQcTaskOutput,
        FragmentomicsTaskOutput,
        MarkFailedTaskOutput,
        MethylExtractTaskOutput,
        MethylQcTaskOutput,
        ParabricksTaskOutput,
        TrimFastqTaskOutput,
    )

    logger.warning("WORKER_STUB_EXTERNAL: faking success for %s", capability)
    sample_id = str(input_json.get("sampleId", "unknown"))
    sample_dir = str(input_json.get("sampleDir", ""))
    sample_path = Path(sample_dir) if sample_dir else None

    if capability == "sample.download-fastq":
        if sample_path is not None:
            sample_path.mkdir(parents=True, exist_ok=True)
            for suffix in ("_1.fastq.gz", "_2.fastq.gz"):
                fq = sample_path / f"{sample_id}{suffix}"
                if not fq.is_file():
                    fq.touch()
        files = [f"{sample_id}_1.fastq.gz", f"{sample_id}_2.fastq.gz"]
        return DownloadFastqTaskOutput(
            status="ok",
            sampleId=sample_id,
            fastqFiles=files,
            n_files=len(files),
        )
    if capability == "parabricks.fq2bam":
        bam = f"{sample_dir}/{sample_id}.bam" if sample_dir else f"{sample_id}.bam"
        if sample_path is not None:
            sample_path.mkdir(parents=True, exist_ok=True)
            Path(bam).touch(exist_ok=True)
            metrics = sample_path / f"{sample_id}.json"
            if not metrics.is_file():
                metrics.write_text('{"guardrails": {"overall_pass": true}}', encoding="utf-8")
        return ParabricksTaskOutput(
            status="ok",
            sampleId=sample_id,
            bamPath=bam,
            metricsJson=f"{sample_dir}/{sample_id}.json" if sample_dir else f"{sample_id}.json",
            qcMetricsTar=(
                f"{sample_dir}/{sample_id}.qc-metrics.tar" if sample_dir else f"{sample_id}.qc-metrics.tar"
            ),
        )
    if capability == "sample.delete-fastqs":
        return DeleteTaskOutput(status="ok", sampleId=sample_id, deleted=True, n_files_removed=0)
    if capability == "sample.trim-fastq":
        return TrimFastqTaskOutput(
            status="ok",
            sampleId=sample_id,
            trimFront2=str(input_json.get("trimFront2", 5)),
            trimmedR1=f"{sample_dir}/{sample_id}_1.trimmed.fastq.gz",
            trimmedR2=f"{sample_dir}/{sample_id}_2.trimmed.fastq.gz",
        )
    if capability == "sample.delete-bam":
        return DeleteTaskOutput(status="ok", sampleId=sample_id, deleted=True, n_files_removed=0)
    if capability == "methyl-extract":
        h5_files = ["21-CG.h5"]
        if sample_path is not None:
            h5_files = _write_stub_extract_artifacts(sample_path, sample_id)
        return MethylExtractTaskOutput(
            status="ok",
            sampleId=sample_id,
            h5Files=h5_files,
            n_h5_files=len(h5_files),
        )
    if capability == "sample.archive-sample":
        if not (input_json.get("sampleDestination") or input_json.get("h5Destination")):
            return ArchiveSampleTaskOutput(
                status="skipped",
                sampleId=sample_id,
                archiveMode=str(input_json.get("mode") or "full"),
                uploadedFiles=[],
                skippedFiles=[],
                remotePrefix="",
                uploadedCount=0,
                skippedCount=0,
                sampleArchived=False,
                archiveSkipped=True,
                skipReason="sample_destination_not_configured",
                missingConfiguration=["sampleDestination"],
            )
        return ArchiveSampleTaskOutput(
            status="ok",
            sampleId=sample_id,
            archiveMode=str(input_json.get("mode") or "full"),
            uploadedFiles=["21-CG.h5"],
            skippedFiles=[],
            remotePrefix="studies/test/",
            uploadedCount=1,
            skippedCount=0,
            sampleArchived=True,
        )
    if capability == "methyl-qc":
        from ..handler_helpers import guardrails_from_payload, screening_from_payload

        qc_path = f"{sample_dir}/{sample_id}.qc.json" if sample_dir else f"{sample_id}.qc.json"
        guardrails = guardrails_from_payload({"overall_pass": True})
        screening = screening_from_payload(
            {
                "disposition": "PASS",
                "trim_front1": 0,
                "trim_tail1": 0,
                "trim_front2": 0,
                "trim_tail2": 0,
                "message": "stub pass",
            }
        )
        return MethylQcTaskOutput(
            status="ok",
            result_code=0,
            sampleId=sample_id,
            qcPath=qc_path,
            guardrails=guardrails,
            screening=screening,
            qcHistory=[],
            remediateAlignment=False,
            remediateR2Trim=False,
        )
    if capability == "methyl-fragmentomics":
        return FragmentomicsTaskOutput(
            status="ok",
            sampleId=sample_id,
            outputDir=sample_dir or "/tmp",
        )
    if capability == "methyl-extraction-qc":
        from ..handler_helpers import guardrails_from_payload

        return ExtractionQcTaskOutput(
            status="ok",
            sampleId=sample_id,
            qcPath=f"{sample_dir}/{sample_id}.extraction_qc.json" if sample_dir else f"{sample_id}.extraction_qc.json",
            guardrails=guardrails_from_payload({"overall_pass": True}),
            extraction_pass=True,
        )
    if capability == "sample.mark-failed":
        return MarkFailedTaskOutput(
            sampleId=sample_id,
            status="QC_FAILED",
            reason=str(input_json.get("reason") or "alignment_qc_failed"),
        )
    raise RuntimeError(
        f"WORKER_STUB_EXTERNAL=1 has no stub for capability {capability!r}."
    )


_SAMPLE_PREP_DOMAIN_ACTIONS = frozenset({
    "sample.download_fastq",
    "sample.parabricks_fq2bam",
    "sample.trim_fastq",
    "sample.methyl_qc",
    "sample.fragmentomics",
    "sample.methyl_extract",
    "sample.extraction_qc",
    "sample.archive_sample",
    "sample.qc_failed",
})


_STUB_EXTERNAL_CAPABILITIES = frozenset({
    "sample.download-fastq",
    "parabricks.fq2bam",
    "sample.delete-fastqs",
    "sample.trim-fastq",
    "sample.delete-bam",
    "methyl-extract",
    "sample.archive-sample",
    "methyl-qc",
    "methyl-fragmentomics",
    "methyl-extraction-qc",
    "sample.mark-failed",
})



