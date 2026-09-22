"""
Orchestrate per-comparison enrichment until all Enrichr libraries complete.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from .enricher import EnrichmentAnalyzer
from .enricher_completeness import (
    COMPLETENESS_MANIFEST_FILENAME,
    CompletenessReport,
    LibraryEnrichResult,
    RetryPolicy,
    assess_completeness,
    enrich_one_library,
    merge_library_results,
    production_enricher_root,
    resolve_expected_libraries,
    write_task_status,
)
from methyl_utils.action_config_resolver import resolve_for_project
from .module_pipeline import run_module_pipeline


def _cisbp_merge_labels(result: Any) -> List[str]:
    """Normalize run_cisbp return value to a list of library labels."""
    if not result:
        return []
    if isinstance(result, list):
        return [str(x) for x in result if x]
    return [str(result)]


def _maybe_run_cisbp(
    cisbp: Optional[Any],
    cisbp_context: Optional[Any],
    genes: List[str],
    output_dir: Path,
    cutoff: float,
) -> List[str]:
    """
    Run the optional CIS-BP integration in the ensure-complete path and return
    library labels to include in the merge. Soft-fails so a CIS-BP problem never
    breaks the core enrichment.
    """
    if cisbp is None or not getattr(cisbp, "enabled", False):
        return []
    try:
        from .cisbp import run_cisbp, CisbpContext

        context = cisbp_context or CisbpContext(cutoff=cutoff)
        result = run_cisbp(cisbp, genes, output_dir, context=context)
        labels = _cisbp_merge_labels(result)
        if labels:
            print(f"[INFO] ✓ CIS-BP: results added as {', '.join(labels)}")
        else:
            print("[WARN] CIS-BP produced no enrichment terms; skipping")
        return labels
    except NotImplementedError as exc:
        print(f"[WARN] CIS-BP skipped: {exc}")
    except Exception as exc:  # noqa: BLE001 - never break core enrichment
        print(f"[WARN] CIS-BP integration failed ({type(exc).__name__}): {exc}")
    return []


def _filter_comparison(
    per_group: List[Tuple[Any, str]], comparison: Optional[str]
) -> List[Tuple[Any, str]]:
    if not comparison:
        return per_group
    want = comparison.strip()
    out = [(p, lbl) for p, lbl in per_group if lbl == want]
    if not out:
        labels = [lbl for _, lbl in per_group]
        raise ValueError(f"Unknown comparison {want!r}. Known: {labels}")
    return out


def run_comparison_enrichment(
    input_file: Path,
    output_dir: Path,
    comparison_label: str,
    *,
    libraries: List[str],
    organism: str,
    cutoff: float,
    policy: RetryPolicy,
    force: bool = False,
    verify_only: bool = False,
    modules_enabled: bool = False,
    load_genes_fn: Optional[Callable[..., List[str]]] = None,
    module_pipeline_kwargs: Optional[Dict[str, Any]] = None,
    cisbp: Optional[Any] = None,
    cisbp_context: Optional[Any] = None,
) -> Tuple[CompletenessReport, List[LibraryEnrichResult]]:
    """
    Ensure all libraries enriched for one comparison; optionally run modules.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    modules_required = bool(modules_enabled)

    if verify_only:
        report = assess_completeness(
            output_dir, libraries, modules_required=modules_required
        )
        status = "completed" if report.complete else "partial"
        write_task_status(
            output_dir,
            comparison_label=comparison_label,
            status=status,
            libraries_ok=report.present_libraries,
            libraries_missing=report.missing_libraries,
            modules_required=modules_required,
            modules_present=report.modules_present,
        )
        return report, []

    try:
        if load_genes_fn is None:
            analyzer = EnrichmentAnalyzer(libraries=libraries, organism=organism, cutoff=cutoff)
            genes = analyzer.load_gene_list(input_file)
        else:
            genes = load_genes_fn(input_file)
    except ValueError as exc:
        if "No genes left after filters" not in str(exc):
            raise
        print(f"[ERROR] {comparison_label}: {exc}")
        report = CompletenessReport(
            output_dir=str(output_dir),
            expected_libraries=libraries,
            present_libraries=[],
            missing_libraries=list(libraries),
            modules_required=modules_enabled,
            modules_present=False,
            complete=False,
        )
        write_task_status(
            output_dir,
            comparison_label=comparison_label,
            status="failed",
            libraries_ok=[],
            libraries_missing=list(libraries),
            modules_required=modules_enabled,
            modules_present=False,
        )
        return report, []

    results: List[LibraryEnrichResult] = []
    for i, lib in enumerate(libraries):
        print(f"\n[INFO] Querying {lib} ({comparison_label})...")
        res = enrich_one_library(
            lib,
            genes,
            output_dir,
            organism=organism,
            policy=policy,
            force=force,
        )
        results.append(res)
        if res.success:
            print(f"[INFO] ✓ {lib}: {res.n_terms} terms (attempts={res.attempts})")
        else:
            print(f"[ERROR] Failed {lib}: {res.error_message}")

        if i + 1 < len(libraries) and policy.inter_library_delay_seconds > 0:
            import time

            time.sleep(policy.inter_library_delay_seconds)

    cisbp_labels = _maybe_run_cisbp(cisbp, cisbp_context, genes, output_dir, cutoff)
    merge_libraries = list(libraries) + cisbp_labels
    merge_library_results(output_dir, merge_libraries, cutoff=cutoff)

    report = assess_completeness(output_dir, libraries, modules_required=False)

    if modules_enabled and report.complete:
        mod_path = output_dir / "modules_ranked.csv"
        if force or not (mod_path.is_file() and mod_path.stat().st_size > 0):
            kwargs = dict(module_pipeline_kwargs or {})
            kwargs.setdefault("input_path", input_file)
            kwargs.setdefault("output_dir", output_dir)
            kwargs.setdefault("libraries", libraries)
            kwargs.setdefault("organism", organism)
            kwargs.setdefault("cutoff", cutoff)
            run_module_pipeline(**kwargs)

    report = assess_completeness(
        output_dir, libraries, modules_required=modules_required
    )
    status = "completed" if report.complete else ("partial" if report.present_libraries else "failed")
    write_task_status(
        output_dir,
        comparison_label=comparison_label,
        status=status,
        libraries_ok=report.present_libraries,
        libraries_missing=report.missing_libraries,
        library_results=results,
        modules_required=modules_required,
        modules_present=report.modules_present,
    )
    return report, results


def write_project_completeness_manifest(
    project_json: Path,
    comparison_reports: Dict[str, CompletenessReport],
) -> Path:
    project_json = Path(project_json)
    root = production_enricher_root(project_json)
    root.mkdir(parents=True, exist_ok=True)
    all_complete = all(r.complete for r in comparison_reports.values())
    payload = {
        "project_json": str(project_json),
        "all_complete": all_complete,
        "comparisons": {k: v.to_dict() for k, v in comparison_reports.items()},
    }
    path = root / COMPLETENESS_MANIFEST_FILENAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    try:
        from methyl_domain.action_result import atomic_write_json, manifest_path_for

        comparison = None
        if len(comparison_reports) == 1:
            comparison = next(iter(comparison_reports))
        run_key = str(comparison) if comparison else "default"
        manifest = manifest_path_for(root, "pipeline.enricher", run_key)
        atomic_write_json(
            manifest,
            {
                "schema_version": "1.0",
                "status": "ok",
                "action_name": "pipeline.enricher",
                "comparison": comparison,
                "output_dir": str(root),
                "all_complete": all_complete,
                "completeness_manifest_path": str(path),
                "n_comparisons": len(comparison_reports),
                "result_code": 0 if all_complete else 1,
            },
        )
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "Could not write enricher action manifest", exc_info=True
        )

    return path


def run_project_ensure_complete(
    project_path: Path,
    *,
    comparison: Optional[str] = None,
    force: bool = False,
    verify_only: bool = False,
    step_override_path: Optional[Path] = None,
    run_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, CompletenessReport]]:
    """
    Run ensure-complete for all (or one) comparisons. Returns (all_complete, reports_by_label).
    """
    from .project_resolver import resolve_enricher_paths_per_cancer_group
    from .config import EnricherStepConfig
    from methyl_utils import load_project

    project_path = Path(project_path)
    project = load_project(project_path)
    step_cfg = dict(resolve_for_project("enricher", project))
    if step_override_path and step_override_path.exists():
        import json as _json

        step_cfg = {**step_cfg, **_json.loads(step_override_path.read_text())}
    enricher_config = EnricherStepConfig.model_validate(step_cfg)
    cfg_dump = enricher_config.model_dump(mode="python", exclude_none=True)
    policy = RetryPolicy.from_config(cfg_dump)

    libraries = resolve_expected_libraries(
        libraries=enricher_config.libraries,
        library_preset=enricher_config.library_preset,
    )
    per_group = resolve_enricher_paths_per_cancer_group(project_path, step_override_path)
    per_group = _filter_comparison(per_group, comparison)

    run_kwargs = run_kwargs or {}
    modules_enabled = bool(enricher_config.modules or run_kwargs.get("modules"))

    # Optional CIS-BP TF-motif integration (config-driven via step_config.enricher.cisbp).
    from .cisbp import effective_cisbp_config, resolve_cisbp_context

    cisbp_config = effective_cisbp_config(enricher_config)
    cisbp_context = None
    if cisbp_config is not None and getattr(cisbp_config, "enabled", False):
        cisbp_context = resolve_cisbp_context(
            cisbp_config,
            project=project,
            project_path=project_path,
            cutoff=float(enricher_config.cutoff or 0.05),
        )
        print(
            f"[INFO] CIS-BP enabled (mode={cisbp_config.mode}, "
            f"source={cisbp_config.gene_set_source}, species={cisbp_config.species})"
        )

    analyzer = EnrichmentAnalyzer(
        libraries=libraries,
        library_preset=enricher_config.library_preset,
        organism=enricher_config.organism or "Human",
        cutoff=enricher_config.cutoff or 0.05,
    )

    def _load_genes(inp: Path) -> List[str]:
        return analyzer.load_gene_list(
            inp,
            top_n=enricher_config.top,
            gene_column=enricher_config.gene_column,
            disease_only=bool(enricher_config.disease_only),
            disease_association_types=enricher_config.disease_association_type,
            min_disease_evidence_level=enricher_config.min_disease_evidence_level,
            min_disease_publications=enricher_config.min_disease_publications,
            min_disease_score=enricher_config.min_disease_score,
            min_dmp_count=enricher_config.min_dmp_count,
            min_unique_dmps=enricher_config.min_unique_dmps,
            max_gene_q_value=enricher_config.max_gene_q_value,
            min_gene_z=enricher_config.min_gene_z,
            min_gene_importance=enricher_config.min_gene_importance,
            sort_by=enricher_config.sort_by,
            sort_ascending=bool(enricher_config.sort_ascending),
        )

    module_kwargs = _module_kwargs_from_config(enricher_config, libraries)

    reports: Dict[str, CompletenessReport] = {}
    for paths, label in per_group:
        inp = Path(paths.input_file)
        if not inp.exists():
            print(f"[WARN] Skipping {label}: input not found: {inp}")
            reports[label] = CompletenessReport(
                output_dir=paths.output_dir,
                expected_libraries=libraries,
                present_libraries=[],
                missing_libraries=list(libraries),
                modules_required=modules_enabled,
                modules_present=False,
                complete=False,
            )
            continue

        print(f"\n--- Ensure-complete: {label} -> {paths.output_dir} ---")
        comparison_cisbp_context = cisbp_context
        if cisbp_config is not None and getattr(cisbp_config, "enabled", False):
            comparison_cisbp_context = resolve_cisbp_context(
                cisbp_config,
                project=project,
                project_path=project_path,
                cutoff=float(enricher_config.cutoff or 0.05),
                cache_dir=str(cisbp_context.cache_dir) if cisbp_context else None,
                comparison_label=label,
            )
        report, _ = run_comparison_enrichment(
            inp,
            Path(paths.output_dir),
            label,
            libraries=libraries,
            organism=enricher_config.organism or "Human",
            cutoff=float(enricher_config.cutoff or 0.05),
            policy=policy,
            force=force,
            verify_only=verify_only,
            modules_enabled=modules_enabled,
            load_genes_fn=_load_genes,
            module_pipeline_kwargs={
                **module_kwargs,
                "input_path": inp,
                "output_dir": Path(paths.output_dir),
                "cisbp": cisbp_config,
                "cisbp_context": comparison_cisbp_context,
            },
            cisbp=cisbp_config,
            cisbp_context=comparison_cisbp_context,
        )
        reports[label] = report
        if report.complete:
            print(f"[OK] {label}: complete ({len(report.present_libraries)} libraries)")
        elif not report.present_libraries:
            print(f"[WARN] {label}: no genes passed filters or enrichment not started")
        else:
            print(
                f"[WARN] {label}: incomplete — missing {len(report.missing_libraries)} libraries"
            )

    manifest_path = write_project_completeness_manifest(project_path, reports)
    print(f"\n[INFO] Completeness manifest: {manifest_path}")
    all_complete = all(r.complete for r in reports.values())
    return all_complete, reports


def _module_kwargs_from_config(config: Any, libraries: List[str]) -> Dict[str, Any]:
    from .config import EnricherStepConfig

    c: EnricherStepConfig = config
    return {
        "gene_column": c.gene_column,
        "top_n": c.top,
        "libraries": libraries,
        "organism": c.organism or "Human",
        "cutoff": c.cutoff or 0.05,
        "disease_only": bool(c.disease_only),
        "disease_association_types": c.disease_association_type,
        "min_disease_evidence_level": c.min_disease_evidence_level,
        "min_disease_publications": c.min_disease_publications,
        "min_disease_score": c.min_disease_score,
        "min_dmp_count": c.min_dmp_count,
        "min_unique_dmps": c.min_unique_dmps,
        "max_gene_q_value": c.max_gene_q_value,
        "min_gene_z": c.min_gene_z,
        "min_gene_importance": c.min_gene_importance,
        "sort_by": c.sort_by,
        "sort_ascending": bool(c.sort_ascending),
        "similarity_threshold": c.similarity_threshold or 0.15,
        "cluster_resolution": c.cluster_resolution or 0.8,
        "cluster_seed": c.cluster_seed or 42,
        "module_cluster_max_q": c.module_cluster_max_q,
        "module_cluster_top_terms_per_library": c.module_cluster_top_terms_per_library,
        "module_label_mode": c.module_label_mode or "dual_label",
        "network_plot": c.network_plot or "plotly",
        "network_refinement_enabled": bool(c.network_refinement_enabled),
        "network_refinement_source": c.network_refinement_source or "string_api",
        "network_refinement_local_edges_file": c.network_refinement_local_edges_file,
        "network_refinement_cache_path": c.network_refinement_cache_path,
        "network_refinement_score_threshold": c.network_refinement_score_threshold or 400.0,
        "network_refinement_community_method": c.network_refinement_community_method or "louvain",
        "network_refinement_min_component_size": c.network_refinement_min_component_size or 2,
        "network_refinement_weight_in_final_score": c.network_refinement_weight_in_final_score or 0.3,
    }


def verify_project_complete(project_path: Path, *, comparison: Optional[str] = None) -> bool:
    all_ok, _ = run_project_ensure_complete(
        Path(project_path),
        comparison=comparison,
        verify_only=True,
    )
    return all_ok
