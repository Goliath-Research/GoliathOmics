"""Write per-sample RNA-Seq QC JSON from quantifier outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..models.config import RnaQcGuardrailConfig
from .guardrails import evaluate_rna_guardrails
from .metrics import collect_rna_metrics


def process_sample_rna_qc(
    sample_dir: str | Path,
    sample_id: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    out_dir: str | Path | None = None,
) -> Path:
    """Evaluate RNA-Seq QC guardrails and write ``{sample_id}.rna_qc.json``.

    Returns the path to the QC JSON. The JSON mirrors the WGBS QC shape
    (``guardrails.overall_pass`` + per-metric blocks) so downstream code and the
    DomainProgram gate can treat it uniformly.
    """
    sample_path = Path(sample_dir)
    guardrail_cfg = RnaQcGuardrailConfig.from_resolved(config)
    metrics = collect_rna_metrics(sample_path, str(sample_id))
    guardrails = evaluate_rna_guardrails(metrics, config=guardrail_cfg)

    payload = {
        "sample_id": str(sample_id),
        "quant_mode": guardrails.get("quant_mode"),
        "metrics": metrics,
        "guardrails": guardrails,
    }
    out_root = Path(out_dir) if out_dir else sample_path
    out_root.mkdir(parents=True, exist_ok=True)
    qc_path = out_root / f"{sample_id}.rna_qc.json"
    qc_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return qc_path
