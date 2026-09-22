"""Validation in-process handlers."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel

from ..depends import Depends, get_logger, get_monte_carlo_runs_root, get_runtime
from ..task_models.pipeline_models import BiomarkerFilterTaskInput
from ..task_models.runtime_models import TaskRuntimeContext
from ..task_models.validation_models import (
    FinalizeFreezeModelBundleTaskInput,
    FreezeReadinessTaskInput,
    LinkArtifactsTaskInput,
    ModelBundleTaskInput,
    ModelMcTaskInput,
    ModelPredictTaskInput,
    ModelTrainTaskInput,
    PostModelValidationTaskInput,
    PrepareFreezeTaskInput,
    SelectBestModelTaskInput,
    StabilityTaskInput,
)

logger = logging.getLogger(__name__)


def _resolve_monte_carlo_runs_root(input_json: Dict[str, Any]) -> Path:
    """Legacy helper for callers that already have dumped input_json."""
    project_path = input_json.get("projectPath") or input_json.get("project")
    if not project_path:
        raise RuntimeError("validation action requires projectPath")
    explicit = input_json.get("monteCarloRunsRoot")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    from methyl_utils import load_project

    project = load_project(str(project_path))
    return Path(project.output_base) / project.project_name / "monte_carlo_runs"


def _mc_input_with_runtime(input: BaseModel, runtime=None) -> Dict[str, Any]:
    """Merge task input with runtime envelope so resolvedConfig reaches _load_mc_config."""
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    if runtime is None:
        return input_json
    profile = getattr(runtime, "validationProfile", None)
    if profile is not None and "resolvedConfig" not in input_json:
        try:
            input_json["resolvedConfig"] = profile.model_dump(mode="json", exclude_none=True)
        except Exception:
            pass
    # Prefer explicit wire resolvedConfig when present on runtime context construction path
    return input_json


def _load_mc_config(
    input_json: Dict[str, Any],
    *,
    profile_overrides=None,
):
    """Build MonteCarloConfig for validation aggregation actions.

    Prefer the worker Universal Action Input Contract: baked ``resolvedConfig`` /
    ``TaskRuntimeContext.validationProfile`` (same slice CAAS signs). Do **not**
    re-merge site/profile from the environment when a resolved slice is present —
    that mismatch previously let CAAS cache empty BA-gated panels under a raw_pool
    signature while execution used a different FeatureCuts profile.
    """
    from methyl_validation.config import ValidationStepConfig, parse_validation_profile
    from methyl_validation.workflow_planner import (
        ValidationPlanRequest,
        _load_config_from_project,
        resolve_base_project_json,
    )

    project_path = input_json.get("projectPath") or input_json.get("project")
    base_project = resolve_base_project_json(project_path)
    allowed = set(ValidationPlanRequest.model_fields)
    request_data = {key: value for key, value in input_json.items() if key in allowed}
    if project_path:
        request_data["projectPath"] = str(project_path)
    # Aggregation steps (stability, freeze, model-mc, …) don't carry the planner-only
    # fields train_fraction / n_iterations / seed, but MonteCarloConfig requires them.
    # Backfill from the MC config snapshot the planner wrote, so the config the runs were
    # produced with is honored (config-not-code) instead of re-resolving them from code.
    _backfill_planner_fields_from_snapshot(input_json, request_data)
    request = ValidationPlanRequest.model_validate(request_data)

    overrides = profile_overrides
    if overrides is None:
        resolved = input_json.get("resolvedConfig")
        if isinstance(resolved, dict):
            overrides = parse_validation_profile(resolved)
        elif isinstance(resolved, ValidationStepConfig):
            overrides = resolved
    return _load_config_from_project(base_project, request, profile_overrides=overrides), base_project


def _backfill_planner_fields_from_snapshot(
    input_json: Dict[str, Any], request_data: Dict[str, Any]
) -> None:
    """Fill trainFraction / featureIterations / seed from monte_carlo_runs/queue/mc_config.json.

    Only reads the scalar planner fields (not the full, possibly schema-drifted snapshot),
    and only when the task input did not already provide them.
    """
    from contextlib import suppress

    with suppress(Exception):
        import json as _json

        from methyl_validation.storage_layout import mc_config_snapshot_path

        mc_root = _resolve_monte_carlo_runs_root(input_json)
        snapshot = mc_config_snapshot_path(mc_root)
        if not snapshot.is_file():
            return
        raw = _json.loads(snapshot.read_text(encoding="utf-8"))
        if request_data.get("featureIterations") is None and raw.get("n_iterations") is not None:
            request_data["featureIterations"] = int(raw["n_iterations"])
        if request_data.get("trainFraction") is None and raw.get("train_fraction") is not None:
            request_data["trainFraction"] = float(raw["train_fraction"])
        if request_data.get("seed") is None and raw.get("seed") is not None:
            request_data["seed"] = int(raw["seed"])


def _handle_validation_stability(
    _capability: str,
    _action_name: str,
    input: StabilityTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    log: logging.Logger = Depends(get_logger),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    input_json = _mc_input_with_runtime(input, runtime)

    from methyl_validation.stability import run_stability_analysis

    from ..task_models.validation_models import StabilitySummary, ValidationStabilityOutput

    profile_overrides = runtime.validationProfile
    config, _base = _load_mc_config(input_json, profile_overrides=profile_overrides)
    output_dir = Path(input.outputDir or mc_root / "stability")
    log.info("validation.stability mc_root=%s output_dir=%s", mc_root, output_dir)
    from methyl_validation.modeling_modes import resolve_gene_stability_preferences

    gene_prefs = resolve_gene_stability_preferences(config.model_dump(mode="python"))
    summary_raw = run_stability_analysis(
        mc_root,
        output_dir=output_dir,
        dmp_min_freq=config.stability_dmp_freq,
        gene_min_freq=config.stability_gene_freq,
        min_balanced_accuracy=config.stability_min_balanced_accuracy,
        prefer_classifier_panel_dmps=bool(config.stability_featurecuts_enabled),
        prefer_classifier_gene_panels=bool(gene_prefs["prefer_classifier_gene_panels"]),
        prefer_mapper_gene_panels=bool(gene_prefs["prefer_mapper_gene_panels"]),
        gene_recurrence_source=str(gene_prefs["gene_recurrence_source"]),
        dual_cutoff_enabled=bool(config.stability_dual_cutoff_enabled),
        relaxed_cutoff_mode=config.stability_relaxed_cutoff_mode,
        relaxed_multiplier=config.stability_relaxed_multiplier,
        score_eps=config.stability_score_eps,
        tiered_stability_enabled=bool(config.stability_tiers_enabled),
        tier_core_frequency=config.stability_tier_core_freq,
        tier_extended_frequency=config.stability_tier_extended_freq,
        tier_exploratory_frequency=config.stability_tier_exploratory_freq,
        default_freeze_tier=config.stability_default_freeze_tier,
    )
    dmp_block = summary_raw.get("dmp_stability") if isinstance(summary_raw, dict) else None
    if isinstance(dmp_block, dict):
        n_runs = int(dmp_block.get("n_runs_analyzed") or 0)
        skipped_ba = int(dmp_block.get("skipped_low_balanced_accuracy") or 0)
        skipped_disc = int(dmp_block.get("skipped_no_discovery") or 0)
        run_dirs = list(mc_root.glob("run_*")) if mc_root is not None else []
        if run_dirs and n_runs == 0 and (skipped_ba + skipped_disc) >= len(run_dirs):
            from methyl_validation.stability import run_balanced_accuracy

            missing_ba = sum(1 for rd in run_dirs if run_balanced_accuracy(rd) is None)
            min_ba = dmp_block.get("min_balanced_accuracy")
            hint = (
                "Per-run balanced_accuracy was missing on all/most runs (detector "
                "discovery_only does not write BA; FeatureCuts/dmp_select must run, or "
                "set actionConfig.validation.stability_min_balanced_accuracy to null)."
                if missing_ba >= len(run_dirs)
                else "Runs were below stability_min_balanced_accuracy, or BA metrics were absent."
            )
            raise RuntimeError(
                "validation.stability analyzed 0 Monte Carlo runs "
                f"(skipped_low_balanced_accuracy={skipped_ba}, skipped_no_discovery={skipped_disc}, "
                f"missing_balanced_accuracy={missing_ba}, min_balanced_accuracy={min_ba!r}). "
                f"Refusing empty stable panels. {hint} "
                "Check resolvedConfig matches the DomainProgram profile "
                "(instance/context overlays must win over profile defaults)."
            )
    summary_path = output_dir / "stability_summary.json"
    return ValidationStabilityOutput(
        status="ok",
        outputDir=str(output_dir),
        summary=StabilitySummary(
            n_iterations=summary_raw.get("n_iterations"),
            n_stable_dmps=summary_raw.get("n_stable_dmps"),
            n_stable_genes=summary_raw.get("n_stable_genes"),
            dmp_min_frequency=summary_raw.get("dmp_min_frequency"),
            gene_min_frequency=summary_raw.get("gene_min_frequency"),
            summary_json_path=str(summary_path) if summary_path.is_file() else None,
        ),
    )


def _handle_validation_biomarker_filter(
    _capability: str,
    _action_name: str,
    input: BiomarkerFilterTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
):
    """In-process PPI-only biomarker gene pool filter on mapper combined genes."""
    input_json = _mc_input_with_runtime(input, runtime)
    from pathlib import Path

    import pandas as pd

    from methyl_gene_select.core.gene_featurecuts import _apply_biomarker_gene_pool_filter
    from methyl_worker.split_detector_task_models import BiomarkerFilterSummary, BiomarkerFilterTaskOutput

    project_path = input.projectPath or input.project
    run_dir = Path(str(input.runDir or project_path)).resolve()
    config, _base = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    mapper_dirs = list(run_dir.glob("**/mapper/*/*")) or list(run_dir.glob("mapper/*/*"))
    gene_df = None
    for d in mapper_dirs:
        for csv in d.glob("all-gene_name-combined.csv"):
            try:
                gene_df = pd.read_csv(csv)
                break
            except Exception:
                continue
        if gene_df is not None:
            break
    if gene_df is None or gene_df.empty:
        raise RuntimeError("No mapper all-gene_name-combined.csv found for biomarker filter")
    out_dir = run_dir / "gene_stability"
    filtered, meta = _apply_biomarker_gene_pool_filter(
        gene_df,
        project_json=Path(str(project_path)),
        config=config,
        out_dir=out_dir,
    )
    out_csv = out_dir / "biomarker_gene_pool.csv"
    filtered.to_csv(out_csv, index=False)
    summary = BiomarkerFilterSummary.model_validate(meta)
    return BiomarkerFilterTaskOutput(
        status="ok",
        n_genes=int(len(filtered)),
        outputCsv=str(out_csv),
        biomarker_filter=summary,
    )


def _handle_validation_prepare_freeze(
    _capability: str,
    _action_name: str,
    input: PrepareFreezeTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    input_json = _mc_input_with_runtime(input, runtime)
    from methyl_validation.stability import prepare_freeze_project

    from ..task_models.validation_models import ValidationPrepareFreezeOutput

    config, base_project = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    stable_csv = input.stableDmpCsv or config.freeze_stable_dmp_csv or str(
        mc_root / "stability" / "stable_dmps_production.csv"
    )
    result = prepare_freeze_project(
        base_project=base_project,
        stable_dmp_csv=str(stable_csv),
        monte_carlo_runs_root=mc_root,
        production_output_dir=input.productionOutputDir or config.production_output_dir,
        config=config,
    )
    production_project = result.get("productionProject") or result.get("projectPath")
    if not production_project:
        out_dir = result.get("outputDir") or result.get("productionOutputDir") or result.get("production_output_dir")
        production_project = str(Path(out_dir) / "project.json") if out_dir else str(base_project)
    # If CAAS already symlinked project.json, refresh eval sidecars into the product dir.
    prod_project_path = Path(str(production_project))
    prod_dir = prod_project_path.parent
    hm_path = prod_dir / "holdout_manifest.json"
    if hm_path.is_file():
        from methyl_validation.holdout_eval import write_holdout_eval_artifacts

        hm = json.loads(hm_path.read_text(encoding="utf-8"))
        class_map = dict(hm.get("holdout_class_map") or {})
        partition = str(hm.get("partition") or getattr(config, "holdout_partition", "locked_test"))
        partitions = getattr(config, "validation_partitions", None)
        holdout_paths = (
            list(getattr(partitions, partition, []) or []) if partitions is not None else []
        )
        if holdout_paths and class_map:
            write_holdout_eval_artifacts(
                production_dir=prod_dir,
                holdout_paths=holdout_paths,
                class_map=class_map,
                samples_base_path=str(getattr(config, "samples_base_path", "") or ""),
                project_json=prod_project_path,
                monte_carlo_runs_root=mc_root,
            )
    # Recompute centroid/detect paths from production project (not study-root enrichment).
    centroid1_dir = None
    centroid2_dir = None
    detect_out_dir = None
    try:
        from methyl_utils import load_project as _load_prod_project

        from ..handler_helpers import production_centroid_detect_dirs

        prod_cfg = _load_prod_project(str(production_project))
        centroid1_dir, centroid2_dir, detect_out_dir = production_centroid_detect_dirs(
            prod_cfg
        )
    except Exception as exc:
        logger.warning("prepare_freeze: could not derive production centroid dirs: %s", exc)

    return ValidationPrepareFreezeOutput(
        status="ok",
        productionOutputDir=result.get("outputDir")
        or result.get("productionOutputDir")
        or result.get("production_output_dir"),
        sourceRunDir=result.get("sourceRunDir"),
        targetRunDir=result.get("targetRunDir"),
        # Downstream freeze nodes must use production/project.json, not the study manifest.
        projectPath=str(production_project),
        fixedDmpPanel=result.get("fixedDmpPanel"),
        centroid1Dir=centroid1_dir,
        centroid2Dir=centroid2_dir,
        detectOutDir=detect_out_dir,
    )


def _handle_validation_finalize_freeze_model_bundle(
    _capability: str,
    _action_name: str,
    input: FinalizeFreezeModelBundleTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    """Build mapper annotations + frozen gene panel after DomainProgram freeze mapper."""
    input_json = _mc_input_with_runtime(input, runtime)
    from methyl_validation.stability import (
        finalize_production_model_bundle,
        production_requires_mapper_annotations,
        resolve_production_ecdf_feature_settings,
    )

    from ..task_models.validation_models import ValidationFinalizeFreezeModelBundleOutput

    config, _base_project = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    production_project = (
        input.projectPath
        or input.project
        or str(Path(input.productionOutputDir or (mc_root / "production")) / "project.json")
    )
    prod_path = Path(str(production_project))
    feature_mode, family = resolve_production_ecdf_feature_settings(prod_path, config=config)
    require_mapper = production_requires_mapper_annotations(feature_mode, family)
    result = finalize_production_model_bundle(
        production_project=prod_path,
        config=config,
        require_mapper=require_mapper,
    )
    return ValidationFinalizeFreezeModelBundleOutput(
        status="ok",
        projectPath=result.get("productionProject"),
        modelBundleDir=result.get("modelBundleDir"),
        n_mapper_rows=int(result.get("n_mapper_rows") or 0),
        n_genes=int(result.get("n_genes") or 0),
    )


def _handle_validation_stability_freeze_readiness(
    _capability: str, _action_name: str, input: FreezeReadinessTaskInput
):
    from methyl_validation.stability_freeze_readiness import analyze_project_root

    from ..task_models.validation_models import ValidationFreezeReadinessOutput

    project_path = input.projectPath or input.project
    if not project_path:
        raise RuntimeError("validation.stability_freeze_readiness requires projectPath")
    from methyl_utils import load_project

    project = load_project(str(project_path))
    project_root = Path(project.output_base) / project.project_name
    report = analyze_project_root(project_root)
    verdict = report.get("verdict", {}) if isinstance(report, dict) else {}
    missing = verdict.get("missing_artifacts") or verdict.get("missing") or []
    if not isinstance(missing, list):
        missing = []
    # Analyzer emits overall ∈ {go, go_with_risks, no_go}; keep legacy ready/status keys.
    overall = str(verdict.get("overall") or "").strip().lower()
    legacy = str(verdict.get("ready", verdict.get("status", ""))).strip().lower()
    ready = overall in {"go", "go_with_risks"} or legacy in {
        "ready",
        "pass",
        "true",
        "ok",
        "go",
        "go_with_risks",
    }
    if not missing:
        reasons = verdict.get("reasons") or []
        if isinstance(reasons, list):
            missing = reasons
    return ValidationFreezeReadinessOutput(
        status="ok",
        ready=ready,
        outputDir=str(project_root),
        missing_artifacts=[str(x) for x in missing],
    )


def _handle_validation_link_artifacts(
    _capability: str, _action_name: str, input: LinkArtifactsTaskInput
):
    from methyl_validation.project_gen import link_run_artifacts_from_source

    from ..task_models.validation_models import ValidationLinkArtifactsOutput

    source = input.sourceRunDir
    target = input.targetRunDir or input.runDir
    if not source or not target:
        raise RuntimeError("validation.link_artifacts requires sourceRunDir and targetRunDir")
    linked = link_run_artifacts_from_source(source, target)
    return ValidationLinkArtifactsOutput(
        status="ok",
        bundleDir=str(linked.get("targetRunDir") or target),
        linked_files=[str(x) for x in (linked.get("linked") or [])],
    )


def _handle_validation_model_bundle(
    _capability: str, _action_name: str, input: ModelBundleTaskInput
):
    from methyl_validation.model_bundle import build_model_feature_bundle

    from ..task_models.validation_models import ValidationModelBundleOutput

    project_path = input.projectPath or input.project
    if not project_path:
        raise RuntimeError("validation.model_bundle requires projectPath")
    bundle_dir = Path(input.bundleDir or Path(str(project_path)).parent / "model_bundle")
    manifest = build_model_feature_bundle(str(project_path), bundle_dir)
    bundle_h5 = bundle_dir / "model_feature_bundle.h5"
    return ValidationModelBundleOutput(
        status="ok",
        bundleDir=str(bundle_dir),
        bundleH5=str(bundle_h5) if bundle_h5.is_file() else str(manifest),
    )


def _handle_validation_model_train(
    _capability: str, _action_name: str, input: ModelTrainTaskInput
):
    from ..task_models.validation_models import ValidationModelTrainOutput

    backend = str(input.backend or "tabular_sklearn")
    project_path = Path(input.projectPath or input.project or "")
    if not project_path.is_file():
        raise RuntimeError("validation.model_train requires projectPath")
    dumped = input.model_dump(mode="json")
    run_dir = Path(dumped.get("runDir") or project_path.parent)
    # bundleDir is a directory; trainers require the .h5 file path.
    bundle_h5 = dumped.get("bundleH5") or run_dir / "model_bundle" / "model_feature_bundle.h5"
    output_dir = Path(dumped.get("outputDir") or run_dir / "models")
    output_dir.mkdir(parents=True, exist_ok=True)
    if backend == "tabular_sklearn":
        from methyl_validation.tabular_backend import train_tabular_model

        summary = train_tabular_model(project_path, bundle_h5, output_dir)
    elif backend == "generative_hybrid":
        from methyl_validation.generative_backend import train_generative_model

        summary = train_generative_model(project_path, bundle_h5, output_dir)
    else:
        raise RuntimeError(
            f"validation.model_train does not support backend={backend!r}; use pipeline.classifier for ecdf"
        )
    model_path = str(summary) if not isinstance(summary, dict) else summary.get("model_path")
    if model_path is None:
        candidate = output_dir / "tabular-model.joblib"
        model_path = str(candidate) if candidate.is_file() else str(output_dir)
    return ValidationModelTrainOutput(
        status="ok",
        model_path=str(model_path),
        backend=backend,
    )


def _handle_validation_model_predict(
    _capability: str, _action_name: str, input: ModelPredictTaskInput
):
    input_json: Dict[str, Any] = input.model_dump(mode="json")
    from ..task_models.validation_models import ValidationModelPredictOutput

    backend = str(input.backend or "tabular_sklearn")
    project_path = Path(input.projectPath or input.project or "")
    if not project_path.is_file():
        raise RuntimeError("validation.model_predict requires projectPath")
    run_dir = Path(input_json.get("runDir") or project_path.parent)
    if backend == "tabular_sklearn":
        from methyl_validation.tabular_backend import predict_tabular_model_from_project

        model_dir = run_dir / "models"
        summary = predict_tabular_model_from_project(project_path, model_dir, run_dir)
    elif backend == "generative_hybrid":
        from methyl_validation.generative_backend import predict_generative_model_from_project

        summary = predict_generative_model_from_project(project_path, output_dir=run_dir)
    else:
        raise RuntimeError(
            f"validation.model_predict does not support backend={backend!r}; use pipeline.predictor for ecdf"
        )
    predictions_path = run_dir / "predictions.csv"
    n_samples = None
    if isinstance(summary, dict):
        n_samples = summary.get("n_samples")
    if n_samples is None and predictions_path.is_file():
        try:
            import pandas as pd

            n_samples = len(pd.read_csv(predictions_path))
        except Exception:
            n_samples = None
    return ValidationModelPredictOutput(
        status="ok",
        predictions_path=str(predictions_path) if predictions_path.is_file() else None,
        n_samples=int(n_samples) if n_samples is not None else None,
    )


def _handle_validation_model_mc(
    _capability: str,
    _action_name: str,
    input: ModelMcTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    input_json = _mc_input_with_runtime(input, runtime)
    from methyl_validation.model_mc_runner import run_model_mc_all

    from ..task_models.validation_models import ValidationModelMcOutput

    config, _base = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    production_dir = Path(
        input.productionOutputDir or config.production_output_dir or mc_root / "production"
    )
    production_project = production_dir / "project.json"
    backends = input.backends
    resume = input.resume
    raw = run_model_mc_all(
        production_project=production_project,
        monte_carlo_runs_root=mc_root,
        config=config,
        backends=list(backends) if backends else None,
        resume=int(resume) if resume is not None else None,
        require_artifact_reuse=bool(input.requireArtifactReuse),
    )
    return ValidationModelMcOutput(
        status="ok",
        modelMcRoot=str(raw.get("modelMcRoot") or mc_root / "model_mc"),
        n_iterations=int(raw.get("nSharedIterations") or 0),
    )


def _handle_validation_select_best_model(
    _capability: str,
    _action_name: str,
    input: SelectBestModelTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    input_json = _mc_input_with_runtime(input, runtime)
    from methyl_validation.cli import _write_backend_ranking
    from methyl_validation.stability import build_production_model

    from ..task_models.validation_models import ValidationSelectBestModelOutput

    config, _base = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    model_mc_root = Path(input.modelMcRoot or mc_root / "model_mc")
    enabled = list(config.get_enabled_backends())
    # Prefer enabled backends from resolved config; ignore stale multi-backend task defaults
    # when the profile/context only enables one (typical ECDF+covariates studies).
    requested = list(input_json.get("backends") or [])
    if enabled:
        backends = enabled
    elif requested:
        backends = requested
    else:
        backends = ["ecdf"]
    metric = str(input.selectionMetric or "balanced_accuracy")
    stat = str(input_json.get("selectionStat") or "median")
    summaries_ready = bool(backends) and all(
        (model_mc_root / b / "metrics_summary.json").is_file() for b in backends
    )
    if summaries_ready:
        ranking = _write_backend_ranking(model_mc_root, backends, metric=metric, stat=stat)
        best_backend = str(ranking[0]["backend"])
        selection_stat = ranking[0].get(stat) if ranking else None
    elif len(backends) == 1:
        # Direct production model build: no model-MC bake-off required.
        best_backend = backends[0]
        ranking = [
            {
                "backend": best_backend,
                "selection_metric": metric,
                "selection_stat": stat,
                "note": "single enabled backend; skipped model_mc ranking",
            }
        ]
        selection_stat = None
    else:
        raise RuntimeError(
            f"Missing model_mc metrics under {model_mc_root} for backends {backends}. "
            "Run validation.model_mc first, or enable exactly one backend_profiles.*.enabled "
            "for a direct production model build."
        )
    summary = build_production_model(
        monte_carlo_runs_root=mc_root,
        production_output_dir=config.production_output_dir,
        config=config.with_backend_selection(best_backend),
    )
    if not summary.get("success", False):
        errs = "; ".join(str(e) for e in (summary.get("errors") or [])[:5]) or "unknown error"
        raise RuntimeError(f"Production model build failed for backend {best_backend}: {errs}")
    production_dir = Path(summary.get("output_dir") or mc_root / "production")
    selection_path = production_dir / "selected_backend.json"
    selection_payload = {
        "selected_backend": best_backend,
        "selection_metric": metric,
        "selection_stat": stat,
        "ranking": ranking,
    }
    selection_path.write_text(json.dumps(selection_payload, indent=2) + "\n", encoding="utf-8")
    return ValidationSelectBestModelOutput(
        status="ok",
        selectedBackend=best_backend,
        selectionMetric=metric,
        selectionStat=float(selection_stat) if selection_stat is not None else None,
    )


def _handle_validation_post_model_validation(
    _capability: str,
    _action_name: str,
    input: PostModelValidationTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    mc_root: Path = Depends(get_monte_carlo_runs_root),
):
    input_json = _mc_input_with_runtime(input, runtime)
    from methyl_validation.pipeline_runner import (
        run_post_model_validation_binary,
        run_post_model_validation_multiclass,
    )
    from methyl_validation.project_gen import infer_monte_carlo_layout

    from ..task_models.validation_models import ValidationPostModelValidationOutput

    config, base_project = _load_mc_config(
        input_json,
        profile_overrides=runtime.validationProfile,
    )
    production_dir = Path(
        input.productionOutputDir or config.production_output_dir or mc_root / "production"
    )
    production_project = production_dir / "project.json"
    if not production_project.is_file():
        raise RuntimeError(f"production project not found: {production_project}")
    layout = infer_monte_carlo_layout(production_project, len(config.cohorts))
    output_dir = Path(
        input.outputDir
        or input.runDir
        or mc_root / "post_model_validation"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    predictor_output_dir = output_dir / "predictors"
    if layout == "binary":
        raw_test_control = input.testControlCsv or input.valControlCsv
        raw_test_disease = input.testDiseaseCsv or input.valDiseaseCsv

        def _first_existing(*candidates: Path) -> Optional[Path]:
            for c in candidates:
                if c is not None and Path(c).is_file():
                    return Path(c)
            return None

        val_control_csv = _first_existing(
            Path(raw_test_control) if raw_test_control else None,
            mc_root / "test_control.csv",
            mc_root / "val_control.csv",
            production_dir / "test_control.csv",
            production_dir / "val_control.csv",
        )
        val_disease_csv = _first_existing(
            Path(raw_test_disease) if raw_test_disease else None,
            mc_root / "test_disease.csv",
            mc_root / "val_disease.csv",
            production_dir / "test_disease.csv",
            production_dir / "val_disease.csv",
        )
        if (
            val_control_csv is None
            or val_disease_csv is None
            or not val_control_csv.is_file()
            or not val_disease_csv.is_file()
        ):
            # Derive from freeze holdout_manifest + locked_test when freeze sidecars missing.
            from methyl_validation.holdout_eval import write_holdout_eval_artifacts

            hm_path = production_dir / "holdout_manifest.json"
            if hm_path.is_file():
                hm = json.loads(hm_path.read_text(encoding="utf-8"))
                class_map = dict(hm.get("holdout_class_map") or {})
                partition = str(
                    hm.get("partition") or getattr(config, "holdout_partition", "locked_test")
                )
                partitions = getattr(config, "validation_partitions", None)
                holdout_paths = (
                    list(getattr(partitions, partition, []) or [])
                    if partitions is not None
                    else []
                )
                if holdout_paths and class_map:
                    write_holdout_eval_artifacts(
                        production_dir=production_dir,
                        holdout_paths=holdout_paths,
                        class_map=class_map,
                        samples_base_path=str(getattr(config, "samples_base_path", "") or ""),
                        project_json=production_project,
                        monte_carlo_runs_root=mc_root,
                    )
                    val_control_csv = _first_existing(
                        mc_root / "test_control.csv",
                        mc_root / "val_control.csv",
                        production_dir / "test_control.csv",
                    )
                    val_disease_csv = _first_existing(
                        mc_root / "test_disease.csv",
                        mc_root / "val_disease.csv",
                        production_dir / "test_disease.csv",
                    )
        if (
            val_control_csv is None
            or val_disease_csv is None
            or not val_control_csv.is_file()
            or not val_disease_csv.is_file()
        ):
            raise RuntimeError(
                "post_model_validation binary requires val cohort CSVs "
                f"(missing under {mc_root} / {production_dir}; "
                "re-run freeze so holdout eval artifacts are emitted)."
            )
        success, errors, timings = run_post_model_validation_binary(
            project_json=production_project,
            val_control_csv=val_control_csv,
            val_disease_csv=val_disease_csv,
            predictor_output_dir=predictor_output_dir,
            production_output_dir=production_dir,
            logs_dir=output_dir / "logs",
            config=config,
        )
    else:
        raw_groups = input.testGroupsJson or input.valGroupsJson
        test_groups_json = Path(raw_groups) if raw_groups else output_dir / "test_groups.json"
        if not test_groups_json.is_file():
            for candidate in (
                output_dir / "test_groups.json",
                mc_root / "test_groups.json",
                production_dir / "test_groups.json",
                output_dir / "val_test_groups.json",
                mc_root / "val_test_groups.json",
                production_dir / "val_test_groups.json",
            ):
                if candidate.is_file():
                    test_groups_json = candidate
                    break
        if not test_groups_json.is_file():
            raise RuntimeError(
                f"post_model_validation multiclass requires testGroupsJson at {test_groups_json}"
            )
        success, errors, timings = run_post_model_validation_multiclass(
            project_json=production_project,
            test_groups_json=test_groups_json,
            predictor_output_dir=predictor_output_dir,
            production_output_dir=production_dir,
            logs_dir=output_dir / "logs",
            config=config,
        )
    report_path = output_dir / "post_model_validation_report.json"
    if not report_path.is_file():
        report_path.write_text(
            json.dumps({"success": success, "errors": errors, "timings": timings}, indent=2),
            encoding="utf-8",
        )
    return ValidationPostModelValidationOutput(
        status="ok" if success else "failed",
        result_code=0 if success else 1,
        outputDir=str(output_dir),
        report_path=str(report_path),
        passed=success,
    )



