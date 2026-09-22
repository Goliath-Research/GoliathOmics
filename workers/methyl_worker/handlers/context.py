"""Context / planner in-process handlers."""

from __future__ import annotations

import logging

from methyl_validation.workflow_planner import ValidationPlanRequest

from ..depends import Depends, get_logger, get_runtime
from ..task_models.context_models import ResolveProjectTaskInput, ResolveProjectTaskOutput
from ..task_models.runtime_models import TaskRuntimeContext
from ..task_models.validation_models import ValidationPlanTaskOutput

logger = logging.getLogger(__name__)


def _handle_context_resolve_project(
    _capability: str,
    _action_name: str,
    input: ResolveProjectTaskInput,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    log: logging.Logger = Depends(get_logger),
) -> ResolveProjectTaskOutput:
    from methyl_domain.helpers import build_resolved_project
    from methyl_domain.types import to_tagged_json

    del runtime  # reserved for future site/profile overlays
    cohort_paths = None
    if input.cohortPathsList:
        cohort_paths = [(label, list(paths)) for label, paths in input.cohortPathsList]
    log.debug("context.resolve_project projectPath=%s", input.projectPath)
    resolved = build_resolved_project(
        input.projectPath,
        monte_carlo_runs_root=input.monteCarloRunsRoot,
        cohort_paths_list=cohort_paths,
    )
    tagged = to_tagged_json(resolved)
    return ResolveProjectTaskOutput(status="ok", resolvedProject=tagged)


def _handle_validation_plan_iterations(
    _capability: str,
    _action_name: str,
    input: ValidationPlanRequest,
    runtime: TaskRuntimeContext = Depends(get_runtime),
    log: logging.Logger = Depends(get_logger),
) -> ValidationPlanTaskOutput:
    from methyl_validation.workflow_planner import plan_validation_context

    log.info(
        "validation.plan_iterations projectPath=%s profile_overrides=%s",
        input.projectPath,
        runtime.validationProfile is not None,
    )
    context = plan_validation_context(input, profile_overrides=runtime.validationProfile)
    return ValidationPlanTaskOutput(
        status="ok",
        projectPath=context.projectPath,
        n_iterations=len(context.iterations),
        centroidSeedGroups=context.centroidSeedGroups,
        iterations=context.iterations,
    )
