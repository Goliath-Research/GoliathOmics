"""Omics overrides for core-namespace platform actions."""

from __future__ import annotations

from typing import List, Optional

from pydantic import ConfigDict, Field

from methyl_validation.planner_models import CentroidSeedGroup, ValidationPlannedIteration

from .action_runtime import _DE_PLAN_ITERATIONS
from .action_spec_build import convert_domain_effects, entity_code, handler_module_path


def _plan_iterations_action_spec():
    from goliath_app.actions.spec import ActionLevel, ActionSpec
    from goliath_app.core.models import PlanIterationsInput, PlanIterationsOutput

    class OmicsPlanIterationsInput(PlanIterationsInput):
        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        overwrite: bool = False
        worker_tool_mapper: str = Field(default="MethylMapper", alias="workerToolMapper")
        worker_tool_enricher: str = Field(default="MethylEnricher", alias="workerToolEnricher")
        worker_tool_progression: str = Field(
            default="MethylDiseaseProgression", alias="workerToolProgression"
        )
        ordered_comparison_labels: Optional[List[str]] = Field(
            default=None, alias="orderedComparisonLabels"
        )

    class OmicsPlanIterationsOutput(PlanIterationsOutput):
        model_config = ConfigDict(extra="forbid", populate_by_name=True)

        n_iterations: int = 0
        iterations: List[ValidationPlannedIteration] = Field(default_factory=list)
        centroid_seed_groups: List[CentroidSeedGroup] = Field(
            default_factory=list, alias="centroidSeedGroups"
        )

    return ActionSpec(
        action_name="validation.plan_iterations",
        namespace="omics",
        level=ActionLevel.CORE,
        entity_code=entity_code("validation.plan_iterations"),
        display_name="Plan validation iterations",
        description=(
            "Monte Carlo planner: stratified per-cohort subsamples and materialize run projects."
        ),
        required_permission="workflow.validation.plan",
        input_model=OmicsPlanIterationsInput,
        output_model=OmicsPlanIterationsOutput,
        schema_version="2",
        overrides="core",
        capability="validation.plan-iterations",
        tool="validation.plan-iterations",
        action_config_key="validation",
        context_vars=("projectPath", "featureIterations", "qualityIterations"),
        domain_effects=convert_domain_effects(_DE_PLAN_ITERATIONS),
        in_process_handler=handler_module_path("_handle_validation_plan_iterations"),
    )


def __getattr__(name: str):
    if name == "PLAN_ITERATIONS_ACTION_SPEC":
        return _plan_iterations_action_spec()
    raise AttributeError(name)
