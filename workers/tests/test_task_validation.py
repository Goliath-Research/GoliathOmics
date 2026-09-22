"""Tests for workflow task input/output schema validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from methyl_worker.task_models.pipeline_models import CentroidTaskOutput
from methyl_worker.task_validation import (
    TASK_VALIDATION_ERROR_CODE,
    TaskValidationError,
    validate_task_input,
    validate_task_output,
    try_validate_task_input,
    try_validate_task_output,
)


def test_pipeline_centroid_input_accepts_valid_payload() -> None:
    validate_task_input(
        "pipeline.centroid",
        "methyl-centroid",
        {"tool": "methyl-centroid", "project": "demo", "phase": "train"},
    )


def test_pipeline_centroid_input_rejects_missing_tool() -> None:
    with pytest.raises(TaskValidationError) as exc:
        validate_task_input("pipeline.centroid", "methyl-centroid", {"project": "demo"})
    assert exc.value.direction == "input"
    assert exc.value.action_name == "pipeline.centroid"


def test_pipeline_centroid_output_accepts_minimal() -> None:
    out = validate_task_output(
        "pipeline.centroid",
        "methyl-centroid",
        CentroidTaskOutput(status="ok"),
    )
    assert isinstance(out, CentroidTaskOutput)
    assert out.status == "ok"


def test_pipeline_centroid_output_rejects_wrong_output_model() -> None:
    from methyl_worker.task_models.validation_models import ValidationPlanTaskOutput

    with pytest.raises(TaskValidationError):
        validate_task_output(
            "pipeline.centroid",
            "methyl-centroid",
            ValidationPlanTaskOutput(status="ok", n_iterations=1),
        )


def test_unknown_action_skips_validation() -> None:
    class _AnyOutput(BaseModel):
        anything: bool = True

    validate_task_input("unknown.action", None, {"anything": True})
    out = validate_task_output("unknown.action", None, _AnyOutput(anything=True))
    assert out.anything is True


def test_validation_plan_resolves_by_capability() -> None:
    validate_task_input(
        "validation.plan_iterations",
        "validation.plan-iterations",
        {"projectPath": "/work/demo/project.json", "featureIterations": 3},
    )


def test_parse_task_envelope_strips_runtime_keys() -> None:
    from methyl_worker.task_models.runtime_models import TaskRuntimeContext
    from methyl_worker.task_validation import parse_task_envelope

    task, runtime = parse_task_envelope(
        "pipeline.detector",
        None,
        {
            "tool": "methyl-detector",
            "projectPath": "/work/demo/project.json",
            "chromosome": "1",
            "context": "CG",
            "comparison": "healthy_vs_disease",
            "workflowNodeKey": "detector-1",
            "resolvedConfig": {"train_fraction": 0.8, "n_iterations": 10},
        },
    )
    assert task.model_dump(mode="json")["projectPath"] == "/work/demo/project.json"
    assert isinstance(runtime, TaskRuntimeContext)
    assert runtime.workflowNodeKey == "detector-1"
    assert runtime.validationProfile is not None
    assert runtime.validationProfile.n_iterations == 10


def test_parse_task_envelope_keeps_typed_resolved_config_on_task() -> None:
    """SamplePrep actions declare resolvedConfig on the input model — do not strip it."""
    from methyl_worker.task_validation import parse_task_envelope

    task, runtime = parse_task_envelope(
        "sample.methylgrapher_wgbs_align",
        "methylgrapher.wgbs_align",
        {
            "tool": "MethylGrapherWgbsAlign",
            "sampleId": "S1",
            "sampleDir": "/work/samples/S1",
            "projectPath": "/work/demo/project.json",
            "executionScopeId": "scope-1",
            "resolvedConfig": {
                "index_prefix": "/work/genomes/pangenome/x",
                "directional": True,
                "c2t": {
                    "gbz": "/c2t.gbz",
                    "dist": "/c2t.dist",
                    "min": "/c2t.min",
                    "zipcodes": "/c2t.zip",
                },
                "g2a": {
                    "gbz": "/g2a.gbz",
                    "dist": "/g2a.dist",
                    "min": "/g2a.min",
                    "zipcodes": "/g2a.zip",
                },
                "ref_paths": "/ref.paths",
                "cpg_tsv": "/cpg.tsv",
                "linear_ref_fasta": "/ref.fa",
            },
        },
    )
    dumped = task.model_dump(mode="json")
    assert dumped["resolvedConfig"]["index_prefix"] == "/work/genomes/pangenome/x"
    assert dumped["executionScopeId"] == "scope-1"
    assert runtime.validationProfile is None


def test_cli_action_accepts_runtime_keys_on_wire() -> None:
    from unittest.mock import MagicMock, patch

    from methyl_worker.action_catalog import find_catalog_entry
    from methyl_worker.actions.base import CliAction, GenericPipelineCollector

    entry = find_catalog_entry("pipeline.detector")
    assert entry is not None
    action = CliAction(
        entry=entry,
        cli_tool="methyl-detector",
        argv_map={"projectPath": "--project"},
        collector=GenericPipelineCollector(),
    )
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = ""
    proc.stderr = ""
    with patch("methyl_worker.execution_handle.run_cancellable", return_value=proc):
        result = action.execute(
            {
                "tool": "methyl-detector",
                "projectPath": "/work/demo/project.json",
                "chromosome": "1",
                "context": "CG",
                "comparison": "healthy_vs_disease",
                "centroid1Dir": "/c1",
                "centroid2Dir": "/c2",
                "outputDir": "/out",
                "workflowNodeKey": "detector-1",
                "resolvedConfig": {"significance_test": "ks_ecdf"},
            }
        )
    assert result.result_code == 0


def test_validate_task_output_rejects_action_execution_result_wrapper() -> None:
    from methyl_worker.action_execution import ActionExecutionResult
    from methyl_worker.task_models.validation_models import ValidationPlanTaskOutput

    wrapped = ActionExecutionResult(
        result_code=0,
        output=ValidationPlanTaskOutput(status="ok", n_iterations=0),
    )
    with pytest.raises(TaskValidationError) as exc:
        validate_task_output(
            "validation.plan_iterations",
            "validation.plan-iterations",
            wrapped,  # type: ignore[arg-type]
        )
    assert exc.value.direction == "output"


def test_try_validate_returns_message() -> None:
    msg = try_validate_task_input("pipeline.detector", None, {})
    assert msg is not None
    assert "input_json failed schema validation" in msg


def test_task_validation_error_code_constant() -> None:
    assert TASK_VALIDATION_ERROR_CODE == 4001


def test_trim_fastq_accepts_compiler_project_path() -> None:
    from methyl_worker.task_models.sample_prep_models import TrimFastqTaskInput

    model = TrimFastqTaskInput.model_validate(
        {
            "tool": "SampleTrimFastq",
            "sampleId": "S1",
            "sampleDir": "/work/samples/S1",
            "projectPath": "/work/projects/demo/configs/project.json",
            "executionScopeId": "scope-1",
            "trimFront1": 5,
        }
    )
    assert model.projectPath.endswith("project.json")
    assert model.executionScopeId == "scope-1"


def test_sample_prep_compiler_keys_on_all_task_inputs() -> None:
    """Compiler always injects projectPath + executionScopeId into action templates."""
    import json
    from pathlib import Path

    from methyl_worker.action_catalog import ACTION_CATALOG

    prog = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "workflow_engine/domain/fixtures/sample_prep.program.json"
        ).read_text(encoding="utf-8")
    )
    actions: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            act = node.get("action")
            if isinstance(act, str):
                actions.add(act)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(prog)
    by_name = {e.action_name: e for e in ACTION_CATALOG}
    for act in sorted(actions):
        entry = by_name[act]
        mod = __import__(entry.input_module, fromlist=[entry.input_class])
        model = getattr(mod, entry.input_class)
        fields = set(model.model_fields)
        assert "projectPath" in fields, act
        assert "executionScopeId" in fields, act
        if entry.action_config_key:
            assert "project" in fields, act
            assert "resolvedConfig" in fields, act
