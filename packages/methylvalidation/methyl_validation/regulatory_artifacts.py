"""
PCCP and post-market monitoring scaffold artifact generators.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _regulatory_dict(config: Optional[Any]) -> Dict[str, Any]:
    reg = getattr(config, "regulatory", None) if config is not None else None
    return {
        "stage": getattr(reg, "stage", "feasibility") if reg is not None else "feasibility",
        "allow_clinical_performance_claims": bool(
            getattr(reg, "allow_clinical_performance_claims", False)
        )
        if reg is not None
        else False,
        "claim_boundary": getattr(reg, "claim_boundary", None) if reg is not None else None,
        "intended_use_summary": getattr(reg, "intended_use_summary", None) if reg is not None else None,
        "target_population": getattr(reg, "target_population", None) if reg is not None else None,
        "sample_type": getattr(reg, "sample_type", None) if reg is not None else None,
        "primary_analyte": getattr(reg, "primary_analyte", None) if reg is not None else None,
        "reference_standard": getattr(reg, "reference_standard", None) if reg is not None else None,
    }


def write_pccp_draft(
    *,
    production_dir: Path,
    config: Optional[Any],
    source_event: str,
) -> Dict[str, Any]:
    production_dir = Path(production_dir)
    production_dir.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "artifact_version": "pccp_draft_v1",
        "source_event": source_event,
        "regulatory": _regulatory_dict(config),
        "description_of_modifications": {
            "allowed_change_categories": [
                "periodic retraining with new representative samples",
                "decision-threshold recalibration",
                "feature-family expansion",
                "annotation enrichment changes (including motif/CIS-BP additions)",
            ],
            "constraints": [
                "No deployment without regression checks against locked baseline.",
                "No update that violates claim boundary or lifecycle stage constraints.",
            ],
        },
        "modification_protocol": {
            "required_steps": [
                "define data cut and freeze training/validation partitions",
                "train candidate model and evaluate clinical performance report with CI",
                "compare against locked baseline and acceptance gates",
                "document artifact hashes and release decision",
            ],
            "acceptance_hooks": {
                "min_sensitivity_lcb": getattr(config, "min_sensitivity_lcb", None) if config is not None else None,
                "min_specificity_lcb": getattr(config, "min_specificity_lcb", None) if config is not None else None,
            },
        },
        "impact_assessment_template": {
            "bias_subgroup_impact": "pending",
            "calibration_impact": "pending",
            "workflow_labeling_impact": "pending",
            "risk_mitigation_plan": "pending",
        },
        "disclaimer": "Draft regulatory support artifact; does not imply FDA acceptance.",
    }
    json_path = production_dir / "pccp_draft.json"
    md_path = production_dir / "pccp_draft.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    md = [
        "# PCCP draft",
        "",
        f"- source_event: `{source_event}`",
        f"- lifecycle stage: `{payload['regulatory'].get('stage')}`",
        f"- disclaimer: {payload.get('disclaimer')}",
        "",
        "## Description of modifications",
        "",
    ]
    for item in payload["description_of_modifications"]["allowed_change_categories"]:
        md.append(f"- {item}")
    md.extend(["", "## Modification protocol", ""])
    for step in payload["modification_protocol"]["required_steps"]:
        md.append(f"- {step}")
    md_path.write_text("\n".join(md).strip() + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "payload": payload}


def write_post_market_monitoring_scaffold(
    *,
    production_dir: Path,
    config: Optional[Any],
    source_event: str,
) -> Dict[str, Any]:
    production_dir = Path(production_dir)
    production_dir.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "artifact_version": "post_market_monitoring_scaffold_v1",
        "source_event": source_event,
        "regulatory": _regulatory_dict(config),
        "monitoring_signals": [
            "input distribution drift",
            "QC failure rate trend",
            "missing feature/sample rate",
            "prediction confidence or margin shift",
            "calibration/performance updates when truth labels arrive",
            "subgroup performance degradation",
        ],
        "collection_contract": {
            "required_fields": [
                "sample_id",
                "timestamp",
                "model_version",
                "prediction",
                "prediction_score",
                "qc_status",
            ],
            "optional_fields": list(getattr(config, "subgroup_columns", []) or []) if config is not None else [],
        },
        "status": "scaffold_only",
    }
    json_path = production_dir / "post_market_monitoring_scaffold.json"
    md_path = production_dir / "post_market_monitoring_scaffold.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    md = [
        "# Post-market monitoring scaffold",
        "",
        f"- source_event: `{source_event}`",
        f"- lifecycle stage: `{payload['regulatory'].get('stage')}`",
        f"- status: `{payload.get('status')}`",
        "",
        "## Monitoring signals",
        "",
    ]
    for signal in payload["monitoring_signals"]:
        md.append(f"- {signal}")
    md_path.write_text("\n".join(md).strip() + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "payload": payload}

