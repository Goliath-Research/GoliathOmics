"""Context for a single QC export (attempt, audit trail)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class QcWriteContext:
    attempt: int = 1
    attempt_reason: str = "Initial alignment QC after Parabricks fq2bam."
    alignment_pass: str = "initial"
    workflow_node_key: Optional[str] = None
    remediation_trigger: Optional[Dict[str, Any]] = None
    sample_prep_log_path: Optional[str] = None
    prior_qc_history: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_input_json(cls, input_json: Dict[str, Any]) -> "QcWriteContext":
        trigger = input_json.get("remediationTrigger")
        if not isinstance(trigger, dict):
            trigger = None
        reason = str(
            input_json.get("qcAttemptReason")
            or input_json.get("attemptReason")
            or "Initial alignment QC after Parabricks fq2bam."
        )
        return cls(
            attempt=int(input_json.get("qcAttempt") or input_json.get("attempt") or 1),
            attempt_reason=reason,
            alignment_pass=str(input_json.get("alignmentPass") or "initial"),
            workflow_node_key=input_json.get("workflowNodeKey"),
            remediation_trigger=trigger,
        )
