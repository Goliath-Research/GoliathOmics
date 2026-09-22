"""
Generate a locked-model specification artifact for auditability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


def _sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _backend_ecdf_params(config: Optional[Any], project_payload: Dict[str, Any]) -> Dict[str, Any]:
    if config is not None:
        try:
            params = config.get_backend_params("ecdf")
            return params.model_dump(mode="python") if hasattr(params, "model_dump") else dict(params)
        except Exception:
            pass
        profiles = getattr(config, "backend_profiles", None)
        if profiles is not None:
            ecdf = getattr(profiles, "ecdf", None)
            params = getattr(ecdf, "params", None)
            if params is not None and hasattr(params, "model_dump"):
                return params.model_dump(mode="python")
    action = project_payload.get("actionConfig") or {}
    validation = action.get("validation") if isinstance(action, dict) else {}
    if isinstance(validation, dict):
        ecdf = ((validation.get("backend_profiles") or {}).get("ecdf") or {}).get("params") or {}
        if isinstance(ecdf, dict):
            return ecdf
    return {}


def _regulatory_block(config: Optional[Any], project_payload: Dict[str, Any]) -> Dict[str, Any]:
    if config is not None:
        reg = getattr(config, "regulatory", None)
        if reg is not None and hasattr(reg, "model_dump"):
            return reg.model_dump(mode="python", exclude_none=True)
        if isinstance(reg, dict):
            return dict(reg)
    top = project_payload.get("regulatory")
    if isinstance(top, dict):
        return dict(top)
    return {}


def write_locked_model_spec(
    *,
    production_dir: Path,
    source_event: str,
    config: Optional[Any] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Emit `locked_model_spec.json/md` under production directory.
    """
    production_dir = Path(production_dir)
    production_dir.mkdir(parents=True, exist_ok=True)
    project_json = production_dir / "project.json"
    production_summary = _read_json(production_dir / "production_summary.json")
    selected_backend = _read_json(production_dir / "selected_backend.json")
    project_payload = _read_json(project_json)
    reg_cfg = _regulatory_block(config, project_payload)
    ecdf_params = _backend_ecdf_params(config, project_payload)

    # Optional model_bundle paths may still appear on study manifests under actionConfig.
    action = project_payload.get("actionConfig") if isinstance(project_payload, dict) else {}
    model_bundle_cfg = (action.get("model_bundle") or {}) if isinstance(action, dict) else {}
    if not model_bundle_cfg:
        # Older production copies may still list paths in production_summary only.
        model_bundle_cfg = {}

    stable_panel = Path(
        str(production_summary.get("fixed_dmp_panel") or production_dir / "stable_dmps_genomewide.csv")
    )
    stable_gene_panel = Path(
        str(
            production_summary.get("stable_gene_panel")
            or model_bundle_cfg.get("stability_gene_panel")
            or ""
        )
    )
    mapper_ann = Path(str(model_bundle_cfg.get("mapper_annotation_csv") or ""))
    fixed_gene_panel = Path(str(model_bundle_cfg.get("fixed_gene_panel") or ""))
    fixed_gene_features = Path(str(model_bundle_cfg.get("fixed_gene_features") or ""))

    feature_mode = str(ecdf_params.get("feature_mode") or "raw_dmp").strip().lower()
    if feature_mode == "raw_gene":
        classifier_artifact = production_dir / "classifiers" / "ecdf_gene_ovr.pkl"
        classifier_kind = "ecdf_gene_one_vs_rest"
    elif feature_mode == "observed_hybrid" and bool(ecdf_params.get("ecdf_aggregated_enabled")):
        classifier_artifact = production_dir / "classifiers" / "ecdf_aggregated_ovr.pkl"
        classifier_kind = "ecdf_aggregated_one_vs_rest"
    else:
        classifier_artifact = production_dir / "classifiers"
        classifier_kind = "ecdf_one_vs_rest"

    feature_family_set = ecdf_params.get("feature_family_set")
    covariates_path = ecdf_params.get("covariates_path")
    validation_partitions = None
    if config is not None and getattr(config, "validation_partitions", None) is not None:
        partitions = getattr(config, "validation_partitions")
        if hasattr(partitions, "model_dump"):
            validation_partitions = partitions.model_dump(mode="python", exclude_none=True)

    payload: Dict[str, Any] = {
        "spec_version": "locked_model_spec_v1",
        "source_event": source_event,
        "project_json": str(project_json),
        "regulatory": {
            "stage": reg_cfg.get("stage", "feasibility"),
            "allow_clinical_performance_claims": bool(
                reg_cfg.get("allow_clinical_performance_claims", False)
            ),
            "claim_boundary": reg_cfg.get(
                "claim_boundary",
                "Not pivotal-validation evidence unless lifecycle stage is pivotal_validation/fda_submission.",
            ),
            "intended_use_summary": reg_cfg.get("intended_use_summary"),
            "target_population": reg_cfg.get("target_population"),
            "sample_type": reg_cfg.get("sample_type"),
            "primary_analyte": reg_cfg.get("primary_analyte"),
            "model_training_analyte": reg_cfg.get("model_training_analyte")
            or reg_cfg.get("primary_analyte"),
            "reference_standard": reg_cfg.get("reference_standard"),
            "fragmentomics_schema_version": "fragmentomics_run_v1",
        },
        "artifacts": {
            "fixed_dmp_panel": {
                "path": str(stable_panel) if stable_panel else None,
                "sha256": _sha256(stable_panel) if stable_panel else None,
            },
            "stable_genes_production": {
                "path": str(stable_gene_panel) if stable_gene_panel else None,
                "sha256": _sha256(stable_gene_panel) if stable_gene_panel else None,
            },
            "mapper_annotation_csv": {
                "path": str(mapper_ann) if mapper_ann else None,
                "sha256": _sha256(mapper_ann) if mapper_ann else None,
            },
            "fixed_gene_panel": {
                "path": str(fixed_gene_panel) if fixed_gene_panel else None,
                "sha256": _sha256(fixed_gene_panel) if fixed_gene_panel else None,
            },
            "fixed_gene_features": {
                "path": str(fixed_gene_features) if fixed_gene_features else None,
                "sha256": _sha256(fixed_gene_features) if fixed_gene_features else None,
            },
            "classifier_model": {
                "kind": classifier_kind,
                "path": str(classifier_artifact),
                "sha256": _sha256(classifier_artifact) if classifier_artifact.is_file() else None,
            },
        },
        "selection": selected_backend or None,
        "runtime": {
            "model_backend": str(getattr(config, "model_backend", "")) if config is not None else None,
            "feature_mode": feature_mode,
            "feature_family_set": feature_family_set,
            "covariates_path": str(covariates_path) if covariates_path else None,
            "validation_partitions": validation_partitions,
        },
    }
    if extra:
        payload["extra"] = extra

    json_path = production_dir / "locked_model_spec.json"
    md_path = production_dir / "locked_model_spec.md"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    lines = [
        "# Locked model specification",
        "",
        f"- source_event: `{source_event}`",
        f"- lifecycle stage: `{payload['regulatory'].get('stage')}`",
        f"- claims allowed: `{payload['regulatory'].get('allow_clinical_performance_claims')}`",
        f"- claim boundary: {payload['regulatory'].get('claim_boundary')}",
        "",
        "## Artifact hashes",
        "",
    ]
    for name, spec in (payload.get("artifacts") or {}).items():
        lines.append(f"- {name}: path=`{spec.get('path')}`, sha256=`{spec.get('sha256')}`")
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path), "payload": payload}
