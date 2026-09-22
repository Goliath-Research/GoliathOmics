"""Plan sample prep context and start SamplePrepPipeline (direct DB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ops._paths import REPO_ROOT, ensure_import_paths
from ops.study_lifecycle import resolve_workflow_version_id

_DEFAULT_SAMPLE_PREP_PROGRAM = (
    REPO_ROOT / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"
)


def _bind_methylation_sample_arms(body: Dict[str, Any], context: Dict[str, Any]) -> None:
    """Rewrite identity-root sampleDir to the align.* leaf after finalize.

    RNA / proteomics SamplePrep programs keep a flat sampleDir.
    """
    program = body.get("program_path") or str(_DEFAULT_SAMPLE_PREP_PROGRAM)
    from methyl_utils.sample_arm_layout import bind_production_sample_arms

    bind_production_sample_arms(context, program=program)


def plan_sample_prep_instance_context(db: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    """Plan samples and bake context_json (does not create an instance)."""
    project_path = body.get("projectPath")
    if not project_path:
        raise ValueError("projectPath is required")

    ensure_import_paths()
    from archive_profile_resolver import (
        apply_archive_profile_storage,
        apply_named_storage_locations,
        study_storage_defaults,
    )
    from methyl_validation.sample_prep_planner import plan_sample_prep_context
    from resource_profile import DEFAULT_ARCHIVE_PROFILE_KEY, ResourceProfileReader
    from workflow_context import finalize_instance_context

    planner_payload = dict(body)
    planner_payload.setdefault("projectPath", project_path)

    profile_key = str(
        body.get("archiveProfileKey")
        or body.get("archiveStorageKey")
        or body.get("storageKey")
        or DEFAULT_ARCHIVE_PROFILE_KEY
    )
    profile_reader = ResourceProfileReader(db)
    planner_payload = apply_named_storage_locations(
        planner_payload,
        expand_endpoint=profile_reader.expand_endpoint,
        load_profile=profile_reader.get_storage_profile,
        study_defaults=study_storage_defaults(str(project_path)),
    )
    planner_payload = apply_archive_profile_storage(
        planner_payload,
        profile_reader.h5_storage_defaults,
        profile_key=profile_key,
    )

    context = plan_sample_prep_context(planner_payload)
    # Profile / alignment overlays from the start request must survive planning so
    # finalize/seed_pipeline_scope_flags can derive usePangenome / useWgbsPangenome.
    for key in (
        "alignmentMode",
        "pipelineProfile",
        "pipelineProcedure",
        "libraryProtocol",
        "usePangenome",
        "useWgbsPangenome",
        "actionConfig",
        "profilePath",
        "procedurePath",
        "siteConfigPath",
        "program_path",
        "program",
        "programPath",
    ):
        if key in body and body[key] is not None:
            context[key] = body[key]
    # Bake site/profile actionConfig, alignment flags, and resolvedConfig__* scope vars.
    # Arm leaf is bound inside finalize (and again below) so generic
    # create_workflow_instance / local engine paths also isolate products.
    context = finalize_instance_context(context)
    # Arm leaf after finalize so site/profile engines are visible. Explicit
    # samples[].sampleDir that is already an arm/mode leaf is preserved.
    _bind_methylation_sample_arms(body, context)
    if body.get("disableArchive") is True:
        context.pop("sampleStorage", None)
        context.pop("h5Storage", None)
        # Seed JSON null so archive templates resolve var.sampleDestination without
        # Missing scope variable; archive handler skips when destination is null.
        context["sampleDestination"] = None
        context["h5Destination"] = None
        context["disableArchive"] = True
    elif context.get("sampleStorage") is None and context.get("h5Storage") is None:
        context.setdefault("sampleDestination", None)
        context.setdefault("h5Destination", None)
    return context


def start_sample_prep(
    db: Any,
    body: Dict[str, Any],
    *,
    create_workflow_definition,
    create_workflow_instance,
    start_workflow_instance,
) -> Dict[str, Any]:
    """Plan samples, build context_json, create and start SamplePrepPipeline."""
    context = plan_sample_prep_instance_context(db, body)

    program_path = body.get("program_path")
    if program_path is None and body.get("workflow_version_id") is None:
        program_path = str(_DEFAULT_SAMPLE_PREP_PROGRAM)

    version_body = dict(body)
    if program_path is not None:
        version_body["program_path"] = program_path

    version_id = resolve_workflow_version_id(
        db,
        version_body,
        create_workflow_definition=create_workflow_definition,
    )
    instance_id = create_workflow_instance(db, version_id, context)
    start_workflow_instance(db, instance_id)
    return {
        "instance_id": instance_id,
        "workflow_version_id": version_id,
        "context_json": context,
        "n_samples": len(context.get("samples") or []),
    }
