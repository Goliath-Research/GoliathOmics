"""Write per-sample extraction QC JSON from MethylExtractor manifests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..guardrails import evaluate_guardrails
from ..manifest import load_extraction_manifest, manifest_path
from ..models.config import ExtractionQCConfig, ExtractionQCGuardrailConfig
from .panel_qc import compute_panel_qc


def extraction_qc_output_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.extraction_qc.json"


def write_extraction_qc_json(payload: Dict[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return output_path


def build_extraction_qc_payload(
    *,
    sample_id: str,
    sample_dir: Path,
    manifest: Dict[str, Any],
    guardrail_config: ExtractionQCGuardrailConfig,
    expected_chromosomes: list[str],
    panel_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guardrails = evaluate_guardrails(
        manifest,
        config=guardrail_config,
        expected_chromosomes=expected_chromosomes,
        panel_metrics=panel_metrics,
    )
    metadata = manifest.get("metadata") or {}
    return {
        "metadata": {
            "schema_name": "methylpipeline.extraction_qc",
            "schema_version": "1.0.0",
            "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sample_id": sample_id,
            "sample_dir": str(sample_dir),
            "manifest_path": str(manifest_path(sample_dir, sample_id)),
            "manifest_schema": metadata.get("schema_name"),
            "manifest_schema_version": metadata.get("schema_version"),
            "contexts_extracted": metadata.get("contexts_extracted"),
        },
        "summary": manifest.get("summary") or {},
        "panel_qc": panel_metrics or {},
        "guardrails": {
            **guardrails["metrics"],
            "overall_pass": guardrails["overall_pass"],
        },
    }


def process_sample_extraction_qc(
    sample_dir: Path | str,
    sample_id: str,
    *,
    config: Optional[ExtractionQCConfig] = None,
) -> Path:
    sample_path = Path(sample_dir)
    cfg = config or ExtractionQCConfig()
    manifest = load_extraction_manifest(sample_path, sample_id)
    panel_metrics = compute_panel_qc(
        sample_path,
        target_panel_bed=cfg.target_panel_bed,
        pos_control_bed=cfg.pos_control_bed,
        neg_control_bed=cfg.neg_control_bed,
    )
    payload = build_extraction_qc_payload(
        sample_id=sample_id,
        sample_dir=sample_path,
        manifest=manifest,
        guardrail_config=cfg.guardrails,
        expected_chromosomes=cfg.expected_chromosomes,
        panel_metrics=panel_metrics,
    )
    return write_extraction_qc_json(payload, extraction_qc_output_path(sample_path, sample_id))
