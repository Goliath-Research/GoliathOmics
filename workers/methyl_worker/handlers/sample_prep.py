"""Sample-prep and alignment in-process handlers."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..handler_helpers import (
    guardrails_from_payload,
    methyl_qc_result_code,
    qc_history_from_payload,
    screening_from_payload,
)
from ..task_models.sample_prep_models import (
    ArchiveSampleTaskInput,
    ArchiveSampleTaskOutput,
    DeleteBamTaskInput,
    DeleteFastqsTaskInput,
    DeleteTaskOutput,
    DemultiplexTaskInput,
    DemultiplexTaskOutput,
    DockerAlignTaskInput,
    DownloadFastqTaskInput,
    DownloadFastqTaskOutput,
    ExtractionQcTaskInput,
    ExtractionQcTaskOutput,
    FragmentomicsTaskInput,
    FragmentomicsTaskOutput,
    MarkFailedTaskOutput,
    MethylExtractTaskInput,
    MethylExtractTaskOutput,
    MethylGrapherWgbsAlignTaskInput,
    MethylGrapherWgbsAlignTaskOutput,
    MethylGrapherWgbsExtractTaskInput,
    MethylQcTaskInput,
    MethylQcTaskOutput,
    ParabricksFq2bamTaskInput,
    ParabricksGiraffeTaskInput,
    ParabricksTaskOutput,
    QcFailedTaskInput,
    TrimFastqTaskInput,
    TrimFastqTaskOutput,
)
from ..work_share import ensure_work_writable, run_with_work_write, share_work_path
from .common import resolve_reference_fasta

logger = logging.getLogger(__name__)


def _handle_methyl_qc(
    _capability: str, _action_name: str, input: MethylQcTaskInput
) -> MethylQcTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId

    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    from methyl_alignment_qc.core import resolve_sample_artifact_id

    # sampleDir may be an experiment mode leaf (.../<sampleId>/linear); artifacts
    # remain named {sampleId}.bam — never use sample_path.name as the sample id.
    resolved_sample_id = resolve_sample_artifact_id(
        sample_path, str(sample_id) if sample_id else None
    )

    try:
        from methyl_domain.sample_content_store import restore_sample_align_products

        restore_sample_align_products(sample_path, resolved_sample_id)
    except Exception:
        logger.debug("CAAS product restore skipped for %s", resolved_sample_id, exc_info=True)

    project = input.project or input.projectPath
    from methyl_alignment_qc.core import process_samples_to_qc_jsons
    from methyl_alignment_qc.core.qc_write_context import QcWriteContext

    write_ctx = QcWriteContext.from_input_json(input_json)
    write_ctx.sample_prep_log_path = str(
        sample_path / f"{resolved_sample_id}.sample_prep_log.jsonl"
    )

    def _mirror_into_sample_dir(qc_path: Path) -> None:
        """Keep the QC JSON beside the BAM it describes.

        ``cfg.output_dir`` is keyed by sample id alone, so separate runs of one
        sample (e.g. the linear vs pangenome_wgbs comparison arms) overwrite each
        other's QC there. The per-run copy is also what artifact validators look
        for when they walk a sample directory.
        """
        dest = sample_path / f"{resolved_sample_id}.alignment_qc.json"
        if qc_path.resolve() == dest.resolve():
            return
        # copy2/copystat chmod on a root-owned NFS file raises EPERM even when
        # mode is 0666. Share first, copy contents only, then open the dest.
        ensure_work_writable(dest)
        try:
            shutil.copy(qc_path, dest)
        except OSError:
            ensure_work_writable(dest)
            if dest.exists():
                dest.unlink()
            shutil.copy(qc_path, dest)
        share_work_path(dest)

    def _build_result(qc_path: Path) -> MethylQcTaskOutput:
        from ..sample_prep_log import append_sample_prep_log

        payload = json.loads(qc_path.read_text(encoding="utf-8"))
        guardrails_raw = payload.get("guardrails") or {}
        guardrails = guardrails_from_payload(guardrails_raw if isinstance(guardrails_raw, dict) else {})
        screening_raw = guardrails_raw.get("screening") or {}
        screening = screening_from_payload(screening_raw if isinstance(screening_raw, dict) else {})
        qc_history = qc_history_from_payload(payload.get("qc_history") or [])
        tf1 = screening.trim_front1
        tt1 = screening.trim_tail1
        tf2 = screening.trim_front2
        tt2 = screening.trim_tail2
        disposition = str(screening.disposition or "")
        remediate = disposition == "REALIGN_TRIM" and any((tf1, tt1, tf2, tt2))
        remediate_r2 = bool(remediate and tf2 > 0 and tf1 == 0 and tt1 == 0 and tt2 == 0)
        rc = methyl_qc_result_code(remediate=remediate)
        append_sample_prep_log(
            sample_path,
            sample_id=resolved_sample_id,
            action="sample.methyl_qc",
            capability=_capability,
            attempt=write_ctx.attempt,
            reason=write_ctx.attempt_reason,
            inputs={
                "qcAttempt": write_ctx.attempt,
                "alignmentPass": write_ctx.alignment_pass,
            },
            outputs={
                "qcPath": str(qc_path),
                "overallPass": guardrails.overall_pass,
                "disposition": screening.disposition,
                "result_code": rc,
            },
            result_code=rc,
            workflow_node_key=write_ctx.workflow_node_key or input_json.get("workflowNodeKey"),
        )
        return MethylQcTaskOutput(
            status="ok",
            result_code=rc,
            sampleId=resolved_sample_id,
            qcPath=str(qc_path),
            guardrails=guardrails,
            screening=screening,
            qcHistory=qc_history,
            remediateAlignment=remediate,
            remediateR2Trim=remediate_r2,
        )

    if project:
        from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config

        cfg = resolve_alignment_qc_config(str(project))
        out_dir = cfg.output_dir
        qc_path = Path(out_dir) / f"{resolved_sample_id}.json"
        if qc_path.exists():
            ensure_work_writable(qc_path)
        if qc_path.is_file():
            # Use module-level json (do not re-import here — that makes `json` a
            # function-local name and breaks _build_result when this branch is skipped).
            prior = json.loads(qc_path.read_text(encoding="utf-8"))
            history = prior.get("qc_history")
            if isinstance(history, list):
                write_ctx.prior_qc_history = [h for h in history if isinstance(h, dict)]
        mirror = sample_path / f"{resolved_sample_id}.alignment_qc.json"

        def _write_project_qc() -> None:
            process_samples_to_qc_jsons(
                [str(sample_path)],
                out_dir,
                validate_schema=cfg.validate_schema,
                fragmentomics=cfg.fragmentomics,
                bisulfite_conversion=cfg.bisulfite_conversion,
                cycle_screening=cfg.cycle_screening,
                optional_guardrails=cfg.optional_guardrails,
                alignment_guardrails=cfg.alignment_guardrails,
                core_guardrails=cfg.core_guardrails,
                write_context=write_ctx,
                sample_id=resolved_sample_id,
                alignment_mode=input.alignmentMode,
            )

        run_with_work_write(_write_project_qc, Path(out_dir), qc_path, mirror)
        qc_path = Path(out_dir) / f"{resolved_sample_id}.json"
    else:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="methyl-qc-") as tmp:
            out_dir = tmp
            process_samples_to_qc_jsons(
                [str(sample_path)],
                out_dir,
                write_context=write_ctx,
                sample_id=resolved_sample_id,
                alignment_mode=input.alignmentMode,
            )
            qc_path = Path(out_dir) / f"{resolved_sample_id}.json"
            _mirror_into_sample_dir(qc_path)
            return _build_result(qc_path)

    if not qc_path.is_file():
        raise RuntimeError(f"QC JSON not written: {qc_path}")

    _mirror_into_sample_dir(qc_path)
    return _build_result(qc_path)


def _handle_methyl_extraction_qc(
    _capability: str, _action_name: str, input: ExtractionQcTaskInput
) -> ExtractionQcTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId

    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    from methyl_extraction_qc.core.writer import process_sample_extraction_qc
    from methyl_extraction_qc.models.config import ExtractionQCConfig
    from methyl_extraction_qc.project_resolver import resolve_extraction_qc_config

    project = input.project or input.projectPath
    if project:
        config = resolve_extraction_qc_config(project, sample_paths=[str(sample_path)])
    else:
        resolved = input.resolvedConfig
        if isinstance(resolved, dict) and resolved.get("expected_chromosomes"):
            config = ExtractionQCConfig(
                expected_chromosomes=[str(item) for item in resolved["expected_chromosomes"]]
            )
        else:
            manifest_path = sample_path / f"{sample_id}.extraction_manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                per_chr = manifest.get("per_chromosome") or {}
                if isinstance(per_chr, dict) and per_chr:
                    config = ExtractionQCConfig(expected_chromosomes=[str(k) for k in per_chr.keys()])
                else:
                    config = None
            else:
                config = None
    qc_path = run_with_work_write(
        lambda: process_sample_extraction_qc(
            sample_path,
            str(sample_id),
            config=config,
        ),
        sample_path / f"{sample_id}.extraction_qc.json",
    )

    from ..sample_prep_log import append_sample_prep_log

    from ..handler_helpers import guardrails_from_payload

    payload = json.loads(qc_path.read_text(encoding="utf-8"))
    guardrails_raw = payload.get("guardrails") or {}
    guardrails = guardrails_from_payload(guardrails_raw if isinstance(guardrails_raw, dict) else {})
    append_sample_prep_log(
        sample_path,
        sample_id=str(sample_id),
        action="sample.extraction_qc",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason="Post-extraction manifest guardrails",
        inputs={"manifestPath": str(sample_path / f"{sample_id}.extraction_manifest.json")},
        outputs={
            "qcPath": str(qc_path),
            "overallPass": guardrails.overall_pass,
        },
        workflow_node_key=str(input_json.get("workflowNodeKey") or "extraction_qc"),
    )
    return ExtractionQcTaskOutput(
        status="ok",
        sampleId=str(sample_id),
        qcPath=str(qc_path),
        guardrails=guardrails,
        extraction_pass=guardrails.overall_pass,
    )


def _handle_methyl_fragmentomics(
    _capability: str, _action_name: str, input: FragmentomicsTaskInput
) -> FragmentomicsTaskOutput:
    project = input.project or input.projectPath
    sample_dir = input.sampleDir
    sample_id = input.sampleId
    if not project:
        raise RuntimeError("methyl-fragmentomics task requires projectPath")

    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    from methyl_fragmentomics.project_resolver import resolve_fragmentomics_step_config
    from methyl_fragmentomics.core.runner import run_fragmentomics_for_samples
    from methyl_utils import load_project

    cfg, _sample_dirs, out_dir = resolve_fragmentomics_step_config(str(project))
    project_obj = load_project(str(project))
    summary = run_fragmentomics_for_samples(
        [str(sample_path)],
        Path(out_dir),
        cfg,
        project_chromosomes=project_obj.chromosomes,
    )

    sid = sample_id or sample_path.name
    sample_summary = summary.get("samples", {}).get(sid, {}) if isinstance(summary, dict) else {}
    sample_out = Path(out_dir) / sid
    summary_path = None
    n_fragments = None
    for candidate in ("fragmentomics_summary.json", "summary.json"):
        p = sample_out / candidate
        if p.is_file():
            summary_path = str(p)
            break
    if isinstance(sample_summary, dict):
        n_fragments = sample_summary.get("n_fragments")
    return FragmentomicsTaskOutput(
        status="ok",
        sampleId=sid,
        outputDir=str(sample_out),
        n_fragments=n_fragments,
        summary_path=summary_path,
    )


def _handle_mark_failed(
    _capability: str, _action_name: str, input: QcFailedTaskInput
) -> MarkFailedTaskOutput:
    return MarkFailedTaskOutput(
        sampleId=input.sampleId,
        sampleDir=input.sampleDir,
        status="QC_FAILED",
        reason=input.reason or "alignment_qc_failed",
    )


def _handle_download_fastq(
    _capability: str, _action_name: str, input: DownloadFastqTaskInput
) -> DownloadFastqTaskOutput:
    from methyl_utils.sample_arm_layout import (
        ensure_sample_dir_fastq_links,
        sample_root_from_sample_dir,
    )

    from ..fastq_source import download_from_source

    sample_dir = Path(input.sampleDir)
    root = (
        Path(input.sampleRoot)
        if input.sampleRoot
        else sample_root_from_sample_dir(sample_dir, input.sampleId)
    )
    root.mkdir(parents=True, exist_ok=True)
    fastq_files = download_from_source(
        input.fastqSource, root, resolved_config=input.resolvedConfig
    )
    sample_id = input.sampleId or root.name
    ensure_sample_dir_fastq_links(
        sample_dir,
        sample_id=str(sample_id),
        sample_root=root,
        sources=fastq_files,
    )
    return DownloadFastqTaskOutput(
        status="ok",
        sampleId=sample_id,
        fastqFiles=fastq_files,
        n_files=len(fastq_files),
    )


def _handle_trim_fastq(
    _capability: str, _action_name: str, input: TrimFastqTaskInput
) -> TrimFastqTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..fastq_trim_runner import run_fastp_trim
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    reason = str(input.remediationReason or input_json.get("qcAttemptReason") or "REALIGN_TRIM")
    result = run_fastp_trim(
        sample_id=str(sample_id),
        sample_dir=str(sample_dir),
        input_json=input_json,
    )
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.trim_fastq",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=reason,
        inputs={
            "trimFront1": result.get("trimFront1"),
            "trimTail1": result.get("trimTail1"),
            "trimFront2": result.get("trimFront2"),
            "trimTail2": result.get("trimTail2"),
            "sampleDir": str(sample_dir),
        },
        outputs=result,
        workflow_node_key=input_json.get("workflowNodeKey") or "trim_fastq",
    )
    return TrimFastqTaskOutput(status="ok", **result)


def _handle_parabricks_fq2bam(
    _capability: str, _action_name: str, input: ParabricksFq2bamTaskInput
) -> ParabricksTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..parabricks_runner import run_fq2bam_meth
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    reference_fasta = resolve_reference_fasta(input_json)

    result = run_fq2bam_meth(
        sample_id=sample_id,
        sample_dir=sample_dir,
        reference_fasta=reference_fasta,
        project=input.projectPath or input.project,
        input_json=input_json,
    )
    reason = str(input.remediationReason or "")
    if input.forceRealign:
        reason = reason or f"forceRealign after trim_front2={input_json.get('trimFront2', '?')}"
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.parabricks_fq2bam",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=reason or "Parabricks fq2bam_meth alignment",
        inputs={
            "forceRealign": bool(input.forceRealign),
            "alignmentPass": input.alignmentPass or "initial",
        },
        outputs=result,
        workflow_node_key=input.workflowNodeKey or "parabricks_fq2bam",
    )
    return ParabricksTaskOutput(status="ok", **result)


def _handle_parabricks_giraffe(
    _capability: str, _action_name: str, input: ParabricksGiraffeTaskInput
) -> ParabricksTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..giraffe_runner import run_giraffe_align
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId

    site_path = input_json.get("siteConfigPath")
    result = run_giraffe_align(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=input.projectPath or input.project,
        input_json=input_json,
        site_path=str(site_path) if site_path else None,
    )
    reason = str(input.remediationReason or "")
    if input.forceRealign:
        reason = reason or f"forceRealign after trim_front2={input_json.get('trimFront2', '?')}"
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.parabricks_giraffe",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=reason or "Parabricks giraffe pangenome alignment",
        inputs={
            "forceRealign": bool(input.forceRealign),
            "alignmentPass": input.alignmentPass or "initial",
            "alignmentMode": "pangenome",
        },
        outputs=result,
        workflow_node_key=input.workflowNodeKey or "parabricks_giraffe",
    )
    return ParabricksTaskOutput(status="ok", **result)


def _handle_methylgrapher_wgbs_align(
    _capability: str, _action_name: str, input: MethylGrapherWgbsAlignTaskInput
) -> MethylGrapherWgbsAlignTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..methylgrapher_wgbs_runner import run_methylgrapher_wgbs_align
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    result = run_methylgrapher_wgbs_align(
        sample_id=sample_id,
        sample_dir=sample_dir,
        input_json=input_json,
    )
    reason = str(input.remediationReason or "")
    if input.forceRealign:
        reason = reason or f"forceRealign after trim_front2={input_json.get('trimFront2', '?')}"
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.methylgrapher_wgbs_align",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=reason or "methylGrapher C2T/G2A WGBS pangenome alignment",
        inputs={
            "forceRealign": bool(input.forceRealign),
            "alignmentPass": input.alignmentPass or "initial",
            "alignmentMode": "pangenome_wgbs",
        },
        outputs=result,
        workflow_node_key=input.workflowNodeKey or "methylgrapher_wgbs_align",
    )
    return MethylGrapherWgbsAlignTaskOutput(status="ok", **result)


def _handle_methylgrapher_wgbs_extract(
    _capability: str, _action_name: str, input: MethylGrapherWgbsExtractTaskInput
) -> MethylExtractTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..methylgrapher_wgbs_runner import run_methylgrapher_wgbs_extract
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    project = input.project or input.projectPath
    if sample_dir and sample_id:
        try:
            from methyl_domain.sample_content_store import restore_sample_align_products

            restore_sample_align_products(sample_dir, str(sample_id))
        except Exception:
            logger.debug("CAAS product restore skipped for wgbs extract %s", sample_id, exc_info=True)
    raw = run_methylgrapher_wgbs_extract(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=str(project),
        input_json=input_json,
    )
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.methylgrapher_wgbs_extract",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason="methylGrapher graph-aware methylation extraction",
        inputs={"forceRealign": bool(input.forceRealign)},
        outputs=raw,
        workflow_node_key=input_json.get("workflowNodeKey") or "methylgrapher_wgbs_extract",
    )
    h5_files = list(raw.get("h5Files") or [])
    return MethylExtractTaskOutput(
        status="ok",
        sampleId=str(raw.get("sampleId") or sample_id),
        h5Files=h5_files,
        n_h5_files=len(h5_files),
    )


def _handle_delete_fastqs(
    _capability: str, _action_name: str, input: DeleteFastqsTaskInput
) -> DeleteTaskOutput:
    from methyl_utils.sample_arm_layout import sample_root_from_sample_dir

    sample_dir = Path(input.sampleDir)
    sample_id = input.sampleId
    root = (
        Path(input.sampleRoot)
        if input.sampleRoot
        else sample_root_from_sample_dir(sample_dir, sample_id)
    )
    removed = 0
    seen: set[str] = set()
    for base in (root, sample_dir):
        if not base.is_dir():
            continue
        key = str(base.resolve()) if base.exists() else str(base)
        if key in seen:
            continue
        seen.add(key)
        for pattern in ("*.fastq.gz", "*.fq.gz", "*.fastq", "*.fq"):
            for path in base.glob(pattern):
                path.unlink(missing_ok=True)
                removed += 1
    return DeleteTaskOutput(
        status="ok",
        sampleId=sample_id or root.name,
        deleted=True,
        n_files_removed=removed,
    )


def _handle_delete_bam(
    _capability: str, _action_name: str, input: DeleteBamTaskInput
) -> DeleteTaskOutput:
    sample_dir = input.sampleDir
    sample_id = input.sampleId
    sample_path = Path(sample_dir)
    removed = 0
    for name in (f"{sample_id}.bam", f"{sample_id}.BAM", f"{sample_id}.json"):
        path = sample_path / name
        if path.is_file():
            path.unlink()
            removed += 1
    return DeleteTaskOutput(
        status="ok",
        sampleId=sample_id,
        deleted=True,
        n_files_removed=removed,
    )


def _handle_methyl_extract(
    _capability: str, _action_name: str, input: MethylExtractTaskInput
) -> MethylExtractTaskOutput:
    from ..extract_runner import run_methyl_extract

    input_json: Dict[str, Any] = input.model_dump(mode="json")

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    project = input.project or input.projectPath

    if sample_dir and sample_id:
        try:
            from methyl_domain.sample_content_store import restore_sample_align_products

            restore_sample_align_products(sample_dir, str(sample_id))
        except Exception:
            logger.debug("CAAS product restore skipped for extract %s", sample_id, exc_info=True)

    raw = run_methyl_extract(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=str(project),
        input_json=input_json,
    )
    h5_files = list(raw.get("h5Files") or [])
    return MethylExtractTaskOutput(
        status="ok",
        sampleId=str(raw.get("sampleId") or sample_id),
        h5Files=h5_files,
        n_h5_files=len(h5_files),
    )


def _handle_archive_sample(
    _capability: str, _action_name: str, input: ArchiveSampleTaskInput
) -> ArchiveSampleTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..sample_archive import archive_from_task_input
    from ..sample_prep_log import append_sample_prep_log

    result = archive_from_task_input(input_json)
    sample_dir = input.sampleDir
    sample_id = result.get("sampleId") or input.sampleId
    if sample_dir and sample_id:
        append_sample_prep_log(
            Path(str(sample_dir)),
            sample_id=str(sample_id),
            action="sample.archive_sample",
            capability=_capability,
            attempt=int(input_json.get("qcAttempt") or 1),
            reason=str(
                input.rejectReason
                or result.get("skipReason")
                or "Archive sample bundle to durable storage"
            ),
            inputs={"mode": result.get("archiveMode"), "remotePrefix": result.get("remotePrefix")},
            outputs=result,
            workflow_node_key=input_json.get("workflowNodeKey") or "archive_sample",
        )
    status = "skipped" if result.get("archiveSkipped") else "ok"
    return ArchiveSampleTaskOutput(status=status, **result)


def _handle_demultiplex(
    _capability: str, _action_name: str, input: DemultiplexTaskInput
) -> DemultiplexTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..demultiplex_runner import run_demultiplex
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    resolved = dict(input.resolvedConfig or {})
    if input.barcodeTsv and not resolved.get("barcode_tsv"):
        resolved["barcode_tsv"] = input.barcodeTsv
    payload = {**input_json, "resolvedConfig": resolved}
    result = run_demultiplex(
        sample_id=str(sample_id),
        sample_dir=str(sample_dir),
        input_json=payload,
    )
    append_sample_prep_log(
        Path(str(sample_dir)),
        sample_id=str(sample_id),
        action="sample.demultiplex",
        capability=_capability,
        attempt=1,
        reason="Barcode demultiplex (epi-GBS / reduced-rep)",
        inputs={"barcodeTsv": resolved.get("barcode_tsv"), "skipped": result.get("skipped")},
        outputs=result,
        workflow_node_key=input_json.get("workflowNodeKey") or "demultiplex",
    )
    status = "skipped" if result.get("skipped") else "ok"
    return DemultiplexTaskOutput(status=status, **result)


def _handle_docker_align(
    _capability: str, _action_name: str, input: DockerAlignTaskInput
) -> ParabricksTaskOutput:
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..docker_align_runner import run_docker_align
    from ..sample_prep_log import append_sample_prep_log

    sample_dir = input.sampleDir
    sample_id = input.sampleId
    reference_fasta = resolve_reference_fasta(input_json)
    result = run_docker_align(
        sample_id=sample_id,
        sample_dir=sample_dir,
        reference_fasta=reference_fasta,
        input_json=input_json,
    )
    append_sample_prep_log(
        Path(sample_dir),
        sample_id=sample_id,
        action="sample.docker_align",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=str(input.remediationReason or "Generic Docker methylation align"),
        inputs={
            "forceRealign": bool(input.forceRealign),
            "alignmentPass": input.alignmentPass or "initial",
        },
        outputs=result,
        workflow_node_key=input.workflowNodeKey or "docker_align",
    )
    return ParabricksTaskOutput(status="ok", **result)



