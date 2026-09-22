"""Tests for validation.plan-iterations worker handler."""

from __future__ import annotations

from unittest.mock import patch

from methyl_domain.types import McIterationTaskConfig
from methyl_validation.planner_models import (
    ValidationPlanContext,
    ValidationPlannedIteration,
    ValidationPlanSummary,
)
from methyl_worker.handlers import execute_task


def _sample_task_config(**overrides: object) -> McIterationTaskConfig:
    payload = {
        "runId": "feature_run_0001",
        "phase": "feature",
        "iteration": 1,
        "layout": "binary",
        "trainFraction": 0.8,
        "projectJson": "/work/demo/monte_carlo_runs/run_0001/project.json",
        "runDir": "/work/demo/monte_carlo_runs/run_0001",
        "monteCarloRunsRoot": "/work/demo/monte_carlo_runs",
        **overrides,
    }
    return McIterationTaskConfig.model_validate(payload)


def _sample_iteration(**overrides: object) -> ValidationPlannedIteration:
    payload = {
        "$type": "StratifiedCohortDraw",
        "runId": "feature_run_0001",
        "phase": "feature",
        "projectPath": "/work/demo/monte_carlo_runs/run_0001/project.json",
        "runDir": "/work/demo/monte_carlo_runs/run_0001",
        "taskConfig": _sample_task_config().model_dump(mode="json"),
        **overrides,
    }
    return ValidationPlannedIteration.model_validate(payload)


def _sample_context(**overrides: object) -> ValidationPlanContext:
    return ValidationPlanContext(
        projectPath="/work/demo",
        iterations=[_sample_iteration()],
        validationPlan=ValidationPlanSummary(
            baseProject="/cfg/project.json",
            layout="binary",
            featureIterations=1,
            qualityIterations=0,
            trainFraction=0.8,
            monteCarloRunsRoot="/work/demo/monte_carlo_runs",
        ),
        **overrides,
    )


def test_validation_plan_iterations_handler() -> None:
    fake_context = _sample_context()
    with patch(
        "methyl_validation.workflow_planner.plan_validation_context",
        return_value=fake_context,
    ):
        result = execute_task(
            "validation.plan-iterations",
            "validation.plan_iterations",
            {"projectPath": "/cfg/project.json", "featureIterations": 1},
        )
    out = result.output.model_dump()
    assert out["status"] == "ok"
    assert out["n_iterations"] == 1
    assert out["iterations"][0]["runId"] == "feature_run_0001"
    assert out["iterations"][0]["projectPath"] == "/work/demo/monte_carlo_runs/run_0001/project.json"
    assert out["iterations"][0]["runDir"] == "/work/demo/monte_carlo_runs/run_0001"
    iter_ref = result.output.iterations[0]
    assert iter_ref.runDir == "/work/demo/monte_carlo_runs/run_0001"
    assert iter_ref.projectPath == "/work/demo/monte_carlo_runs/run_0001/project.json"


def test_validation_plan_iterations_emits_centroid_seed_groups() -> None:
    from methyl_domain.types import CentroidSeedGroup

    seed = CentroidSeedGroup(
        label="healthy",
        addSamples=["/work/samples/H1"],
        removeSamples=[],
        centroidDir="/work/demo/monte_carlo_runs/_centroid_seed/centroids/controls/healthy/healthy",
    )
    fake_context = _sample_context(centroidSeedGroups=[seed])
    with patch(
        "methyl_validation.workflow_planner.plan_validation_context",
        return_value=fake_context,
    ):
        result = execute_task(
            "validation.plan-iterations",
            "validation.plan_iterations",
            {"projectPath": "/cfg/project.json", "featureIterations": 1},
        )
    assert len(result.output.centroidSeedGroups) == 1
    assert result.output.centroidSeedGroups[0].label == "healthy"
