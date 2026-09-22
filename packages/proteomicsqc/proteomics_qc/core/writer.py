"""Write per-sample proteomics QC JSON from the registered abundance contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from omics_features.feature_store import read_sample_features

from ..models.config import ProteomicsQcGuardrailConfig


def _metric(*, value: Any, normal_range: str, passed: bool, message: str) -> Dict[str, Any]:
    return {"value": value, "normal_range": normal_range, "pass": passed, "message": message}


def process_sample_proteomics_qc(
    sample_dir: str | Path,
    sample_id: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    out_dir: str | Path | None = None,
) -> Path:
    """Evaluate proteomics QC guardrails and write ``{sample_id}.proteomics_qc.json``."""
    sample_path = Path(sample_dir)
    cfg = ProteomicsQcGuardrailConfig.from_resolved(config)

    feature_ids, values, source = read_sample_features(sample_path, sample_id, kind="abundance")
    finite = np.isfinite(values) & (values > 0)
    n_identified = int(np.count_nonzero(finite))

    results: Dict[str, Any] = {}
    results["proteins_identified"] = _metric(
        value=n_identified,
        normal_range=f">= {cfg.min_proteins_identified}",
        passed=n_identified >= cfg.min_proteins_identified,
        message="Number of proteins with a positive quantified abundance.",
    )
    if cfg.expected_proteins and cfg.expected_proteins > 0:
        missing_fraction = 1.0 - (n_identified / float(cfg.expected_proteins))
        results["missing_fraction"] = _metric(
            value=round(missing_fraction, 4),
            normal_range=f"<= {cfg.max_missing_fraction}",
            passed=missing_fraction <= cfg.max_missing_fraction,
            message="Fraction of the expected panel not quantified in this sample.",
        )

    overall_pass = all(bool(m.get("pass")) for m in results.values())
    payload = {
        "sample_id": str(sample_id),
        "source": source,
        "n_features_total": int(len(feature_ids)),
        "metrics": results,
        "guardrails": {"metrics": results, "overall_pass": overall_pass},
    }
    out_root = Path(out_dir) if out_dir else sample_path
    out_root.mkdir(parents=True, exist_ok=True)
    qc_path = out_root / f"{sample_id}.proteomics_qc.json"
    qc_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return qc_path
