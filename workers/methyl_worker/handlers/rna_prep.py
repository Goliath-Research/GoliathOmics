"""RNA-Seq sample-prep in-process handlers (alignment/quant, QC, expression registration)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from ..task_models.rna_prep_models import (
    KallistoTaskInput,
    ParabricksRnaFq2bamTaskInput,
    RegisterExpressionTaskInput,
    RegisterExpressionTaskOutput,
    RnaQcTaskInput,
    RnaQcTaskOutput,
    RnaQuantTaskOutput,
)

logger = logging.getLogger(__name__)


def _handle_parabricks_rna_fq2bam(
    _capability: str, _action_name: str, input: ParabricksRnaFq2bamTaskInput
) -> RnaQuantTaskOutput:
    from ..rna_fq2bam_runner import run_rna_fq2bam
    from ..sample_prep_log import append_sample_prep_log

    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId

    site_path = input_json.get("siteConfigPath")
    result = run_rna_fq2bam(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=input.projectPath or input.project,
        input_json=input_json,
        site_path=str(site_path) if site_path else None,
    )
    append_sample_prep_log(
        Path(sample_dir),
        sample_id=sample_id,
        action="sample.parabricks_rna_fq2bam",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=str(input_json.get("remediationReason") or "Parabricks rna_fq2bam alignment + gene counts"),
        inputs={"quantMode": "star"},
        outputs=result,
        workflow_node_key=input_json.get("workflowNodeKey") or "parabricks_rna_fq2bam",
    )
    return RnaQuantTaskOutput(status="ok", **result)


def _handle_kallisto(
    _capability: str, _action_name: str, input: KallistoTaskInput
) -> RnaQuantTaskOutput:
    from ..kallisto_runner import run_kallisto
    from ..sample_prep_log import append_sample_prep_log

    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId

    site_path = input_json.get("siteConfigPath")
    result = run_kallisto(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=input.projectPath or input.project,
        input_json=input_json,
        site_path=str(site_path) if site_path else None,
    )
    append_sample_prep_log(
        Path(sample_dir),
        sample_id=sample_id,
        action="sample.kallisto",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason=str(input_json.get("remediationReason") or "Parabricks kallisto pseudo-alignment"),
        inputs={"quantMode": "kallisto"},
        outputs=result,
        workflow_node_key=input_json.get("workflowNodeKey") or "kallisto",
    )
    return RnaQuantTaskOutput(status="ok", **result)


def _handle_rna_qc(
    _capability: str, _action_name: str, input: RnaQcTaskInput
) -> RnaQcTaskOutput:
    from rna_alignment_qc.core import process_sample_rna_qc

    from ..handler_helpers import guardrails_from_payload
    from ..sample_prep_log import append_sample_prep_log

    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId
    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    from methyl_utils.action_config_resolver import resolve_from_task_input

    qc_cfg = resolve_from_task_input("rna_qc", input_json)
    qc_path = process_sample_rna_qc(sample_path, sample_id, config=qc_cfg)
    payload = json.loads(Path(qc_path).read_text(encoding="utf-8"))
    guardrails_raw = payload.get("guardrails") or {}
    guardrails = guardrails_from_payload(guardrails_raw if isinstance(guardrails_raw, dict) else {})
    append_sample_prep_log(
        sample_path,
        sample_id=sample_id,
        action="sample.rna_qc",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason="RNA-Seq alignment/quantification guardrails",
        inputs={},
        outputs={"qcPath": str(qc_path), "overallPass": guardrails.overall_pass},
        workflow_node_key=input_json.get("workflowNodeKey") or "rna_qc",
    )
    return RnaQcTaskOutput(
        status="ok",
        sampleId=sample_id,
        qcPath=str(qc_path),
        guardrails=guardrails,
        rnaQcPass=guardrails.overall_pass,
    )


def _handle_register_expression(
    _capability: str, _action_name: str, input: RegisterExpressionTaskInput
) -> RegisterExpressionTaskOutput:
    from rna_express.core import register_sample_expression

    from ..sample_prep_log import append_sample_prep_log

    input_json: Dict[str, Any] = input.model_dump(mode="json")
    sample_dir = input.sampleDir
    sample_id = input.sampleId

    from methyl_utils.action_config_resolver import (
        resolve_from_task_input,
        resolve_rna_reference,
    )

    align_cfg = resolve_from_task_input("rna_align", input_json)
    site = input_json.get("siteConfig")
    tx2gene = align_cfg.get("tx2gene")
    if not tx2gene:
        ref = resolve_rna_reference(site if isinstance(site, dict) else None)
        tx2gene = ref.get("tx2gene")

    result = register_sample_expression(
        sample_dir=sample_dir,
        sample_id=sample_id,
        tx2gene_path=str(tx2gene) if tx2gene else None,
    )
    append_sample_prep_log(
        Path(sample_dir),
        sample_id=sample_id,
        action="sample.register_expression",
        capability=_capability,
        attempt=int(input_json.get("qcAttempt") or 1),
        reason="Normalize gene counts / transcript abundances into expression.h5",
        inputs={"quantMode": result.get("quantMode")},
        outputs=result,
        workflow_node_key=input_json.get("workflowNodeKey") or "register_expression",
    )
    return RegisterExpressionTaskOutput(status="ok", **result)
