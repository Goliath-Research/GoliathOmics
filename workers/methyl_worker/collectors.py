"""Collect typed ACTION outputs from /work manifests and legacy artifacts."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from methyl_domain.action_result import manifest_path_for
from pydantic import BaseModel


class ArtifactCollector(ABC):
    @abstractmethod
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        """Build action-specific output fields (without telemetry)."""


def _run_key(input_json: Mapping[str, Any]) -> str:
    parts = [
        str(input_json.get("chromosome") or ""),
        str(input_json.get("context") or ""),
        str(input_json.get("group") or input_json.get("comparison") or ""),
        str(input_json.get("sampleId") or ""),
    ]
    key = "_".join(p for p in parts if p)
    return key or "default"


def _try_read_manifest(
    output_dir: Path,
    action_name: str,
    input_json: Mapping[str, Any],
    output_model: type[BaseModel],
) -> Optional[Dict[str, Any]]:
    path = manifest_path_for(output_dir, action_name, _run_key(input_json))
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    # Worker idempotency envelopes (1.1/1.2) wrap task_output for skip/replay; CLIs write
    # flat 1.0 manifests. After a fresh CLI run, fall back to legacy artifact scan.
    # Never validate the envelope as ActionOutputBase (extra=forbid, schema_version=1.0).
    schema_version = str(raw.get("schema_version") or "")
    if schema_version in {"1.1", "1.2"} or isinstance(raw.get("task_output"), dict):
        return None
    model = output_model.model_validate(raw)
    data = model.model_dump(mode="json")
    data["manifest_path"] = str(path)
    return data


class ManifestFirstCollector(ArtifactCollector):
    def __init__(
        self,
        *,
        output_model: type[BaseModel],
        resolve_output_dir,
        legacy_collect: Optional[ArtifactCollector] = None,
    ) -> None:
        self.output_model = output_model
        self.resolve_output_dir = resolve_output_dir
        self.legacy_collect = legacy_collect

    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        out_dir = self.resolve_output_dir(input_json)
        if out_dir is not None:
            manifest = _try_read_manifest(Path(out_dir), action_name, input_json, self.output_model)
            if manifest is not None:
                return manifest
        if self.legacy_collect is not None:
            return self.legacy_collect.collect(input_json, action_name=action_name, stdout=stdout)
        return {"status": "ok", "stdout_tail": stdout[-500:] if stdout else None}


class DmpSelectLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        from .actions.detector import resolve_detector_group

        chrom = str(input_json.get("chromosome") or "")
        payload: Dict[str, Any] = {"status": "ok", "chromosome": chrom or None, "stdout_tail": stdout[-500:]}
        if not chrom:
            return payload
        project = input_json.get("projectPath") or input_json.get("project")
        output_dir = input_json.get("outputDir")
        if not output_dir and project:
            try:
                from methyl_dmp_select.utils.project_resolver import resolve_dmp_selection_config

                cfg = resolve_dmp_selection_config(
                    str(project),
                    comparison=resolve_detector_group(dict(input_json)),
                    chromosome=chrom,
                )
                output_dir = cfg.output_dir
            except Exception:
                return payload
        if not output_dir:
            return payload
        out = Path(str(output_dir))
        audit_path = out / f"dmp_selection-{chrom}.json"
        if audit_path.is_file():
            try:
                with open(audit_path, encoding="utf-8") as f:
                    audit = json.load(f)
                payload.update(
                    n_dmps_discovery=audit.get("n_dmps_discovery"),
                    n_dmps_classifier=audit.get("n_dmps_classifier"),
                    n_dmps_extended=audit.get("n_dmps_extended"),
                    discovery_csv=audit.get("discovery_csv"),
                    classifier_csv=str(out / f"dmps-{chrom}-classifier.csv"),
                    extended_csv=str(out / f"dmps-{chrom}-classifier-extended.csv"),
                    audit_path=str(audit_path),
                )
            except Exception:
                pass
        if not (out / f"dmps-{chrom}-classifier.csv").is_file():
            payload["status"] = "skipped"
        return payload


def _resolve_dmp_output_dir(input_json: Mapping[str, Any]) -> Optional[str]:
    if input_json.get("outputDir"):
        return str(input_json["outputDir"])
    project = input_json.get("projectPath") or input_json.get("project")
    chrom = input_json.get("chromosome")
    if not project or not chrom:
        return None
    from methyl_dmp_select.utils.project_resolver import resolve_dmp_selection_config
    from .actions.detector import resolve_detector_group

    cfg = resolve_dmp_selection_config(
        str(project),
        comparison=resolve_detector_group(dict(input_json)),
        chromosome=str(chrom),
    )
    return str(cfg.output_dir)


class GeneSelectLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        from methyl_gene_select.core.gene_featurecuts import (
            GENE_DMP_LOCI_CSV,
            GENE_FEATURECUTS_METRICS_JSON,
            GENES_CLASSIFIER_CSV,
            GENE_STABILITY_DIR,
        )
        from .actions.gene_select import default_run_dir_for_project

        project = input_json.get("projectPath") or input_json.get("project")
        run_dir = input_json.get("runDir")
        if project and not run_dir:
            run_dir = default_run_dir_for_project(str(project))
        payload: Dict[str, Any] = {"status": "ok", "run_dir": str(run_dir) if run_dir else None}
        if not run_dir:
            return payload
        out_dir = Path(str(run_dir)) / GENE_STABILITY_DIR
        metrics_path = out_dir / GENE_FEATURECUTS_METRICS_JSON
        if metrics_path.is_file():
            try:
                with open(metrics_path, encoding="utf-8") as f:
                    metrics = json.load(f)
                payload["selected_k"] = metrics.get("selected_k")
                payload["balanced_accuracy"] = metrics.get("balanced_accuracy")
            except Exception:
                pass
        genes_csv = out_dir / GENES_CLASSIFIER_CSV
        loci_csv = out_dir / GENE_DMP_LOCI_CSV
        if genes_csv.is_file():
            payload["genes_classifier_csv"] = str(genes_csv)
        if loci_csv.is_file():
            payload["gene_dmp_loci_csv"] = str(loci_csv)
        if metrics_path.is_file():
            payload["metrics_json"] = str(metrics_path)
        return payload


class GeneFeatureSelectLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        from methyl_gene_feature_select.core.runner import (
            GENE_FEATURE_SELECTION_JSON,
            GENE_FEATURES_CLASSIFIER_CSV,
        )

        output_dir = Path(str(input_json["outputDir"]))
        audit_path = output_dir / GENE_FEATURE_SELECTION_JSON
        out_csv = output_dir / GENE_FEATURES_CLASSIFIER_CSV
        payload: Dict[str, Any] = {
            "status": "skipped" if "skipping" in stdout.lower() else "ok",
            "output_csv": str(out_csv) if out_csv.is_file() else None,
            "audit_path": str(audit_path) if audit_path.is_file() else None,
        }
        if audit_path.is_file():
            try:
                with open(audit_path, encoding="utf-8") as f:
                    audit = json.load(f)
                payload["n_features"] = audit.get("n_features")
            except Exception:
                pass
        return payload


class DetectorLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        project = input_json.get("projectPath") or input_json.get("project")
        chrom = input_json.get("chromosome")
        ctx = input_json.get("context")
        group = input_json.get("group") or input_json.get("comparison")
        payload: Dict[str, Any] = {
            "status": "ok",
            "chromosome": str(chrom) if chrom else None,
            "context": str(ctx) if ctx else None,
            "group": str(group) if group else None,
        }
        if not project:
            return payload
        try:
            from methyl_detector.utils.project_resolver import resolve_detector_config

            cfg = resolve_detector_config(
                str(project),
                comparison=group,
                chromosome=str(chrom) if chrom else None,
                context=str(ctx) if ctx else None,
            )
            out_dir = Path(cfg.output_dir)
            payload["output_dir"] = str(out_dir)
            disc = out_dir / f"dmps-{chrom}-discovery.csv"
            if disc.is_file():
                payload["discovery_csv"] = str(disc)
                try:
                    import pandas as pd

                    df = pd.read_csv(disc)
                    payload["n_biological_dmps"] = len(df)
                except Exception:
                    pass
            for result_json in sorted(out_dir.glob("result*.json")):
                payload["result_json_path"] = str(result_json)
                try:
                    with open(result_json, encoding="utf-8") as f:
                        data = json.load(f)
                    payload["n_statistical_dmps"] = data.get("total_statistical_dmps")
                    if payload.get("n_biological_dmps") is None:
                        payload["n_biological_dmps"] = data.get("total_biological_dmps")
                except Exception:
                    pass
                break
        except Exception:
            pass
        return payload


class MapperLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        project = input_json.get("projectPath") or input_json.get("project")
        chrom = input_json.get("chromosome")
        ctx = input_json.get("context")
        group = input_json.get("group") or input_json.get("comparison")
        payload: Dict[str, Any] = {
            "status": "ok",
            "chromosome": str(chrom) if chrom else None,
            "context": str(ctx) if ctx else None,
            "group": str(group) if group else None,
        }
        output_dir = input_json.get("outputDir")
        if not output_dir and project:
            try:
                from methyl_mapper.utils.project_resolver import resolve_mapper_config

                cfg = resolve_mapper_config(str(project), comparison=group, chromosome=chrom, context=ctx)
                output_dir = cfg.output_dir
            except Exception:
                return payload
        if not output_dir:
            return payload
        out = Path(str(output_dir))
        payload["output_dir"] = str(out)
        for csv in out.glob("*-combined.csv"):
            payload["output_csv"] = str(csv)
            try:
                import pandas as pd

                df = pd.read_csv(csv)
                payload["n_output_genes"] = len(df)
            except Exception:
                pass
            break
        for js in out.glob("*-genes.json"):
            payload["output_json"] = str(js)
            break
        return payload


class CentroidLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        chrom = input_json.get("chromosome")
        ctx = input_json.get("context")
        group = input_json.get("group") or input_json.get("comparison")
        payload: Dict[str, Any] = {
            "status": "ok",
            "chromosome": str(chrom) if chrom else None,
            "context": str(ctx) if ctx else None,
            "group": str(group) if group else None,
        }
        output_dir = input_json.get("outputDir")
        project = input_json.get("projectPath") or input_json.get("project")
        if not output_dir and project:
            try:
                from methyl_centroid.project_resolver import resolve_centroid_batch_config

                cfg = resolve_centroid_batch_config(str(project), group=str(group) if group else None)
                if chrom and ctx:
                    output_dir = str(Path(cfg.output_base) / f"{chrom}-{ctx}")
            except Exception:
                pass
        if output_dir:
            out = Path(str(output_dir))
            payload["output_dir"] = str(out)
            for h5 in out.glob("*.h5"):
                payload["centroid_h5_path"] = str(h5)
                break
        return payload


class EnricherLegacyCollector(ArtifactCollector):
    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        from methyl_enricher.enricher_completeness import (
            COMPLETENESS_MANIFEST_FILENAME,
            production_enricher_root,
        )

        comparison = input_json.get("group") or input_json.get("comparison")
        payload: Dict[str, Any] = {
            "status": "ok",
            "comparison": str(comparison) if comparison else None,
            "stdout_tail": stdout[-500:] if stdout else None,
        }
        project = input_json.get("projectPath") or input_json.get("project")
        if not project:
            return payload
        try:
            root = production_enricher_root(Path(str(project)))
            payload["output_dir"] = str(root)
            manifest_path = root / COMPLETENESS_MANIFEST_FILENAME
            if manifest_path.is_file():
                with open(manifest_path, encoding="utf-8") as f:
                    data = json.load(f)
                payload["all_complete"] = data.get("all_complete")
                payload["completeness_manifest_path"] = str(manifest_path)
                comparisons = data.get("comparisons") or {}
                if isinstance(comparisons, dict):
                    payload["n_comparisons"] = len(comparisons)
                    if comparison and str(comparison) in comparisons:
                        payload["comparison"] = str(comparison)
        except Exception:
            pass
        return payload


def _resolve_enricher_output_dir(input_json: Mapping[str, Any]) -> Optional[str]:
    explicit = input_json.get("outputDir")
    if explicit:
        return str(explicit)
    project = input_json.get("projectPath") or input_json.get("project")
    if not project:
        return None
    try:
        from methyl_enricher.enricher_completeness import production_enricher_root

        return str(production_enricher_root(Path(str(project))))
    except Exception:
        return None


class GenericPipelineCollector(ArtifactCollector):
    """Minimal collector for pipeline tools without bespoke artifact scraping yet."""

    def collect(
        self,
        input_json: Mapping[str, Any],
        *,
        action_name: str,
        stdout: str = "",
    ) -> Dict[str, Any]:
        return {"status": "ok", "stdout_tail": stdout[-500:] if stdout else None}
