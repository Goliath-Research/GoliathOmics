"""ACTION dispatch: catalog lookup → CLI / in-process execution."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel

from ..action_execution import ActionExecutionResult, finalize_output
from ..action_catalog import (
    build_capability_handlers,
    build_tool_cli_map,
    find_catalog_entry,
    find_catalog_entry_by_capability,
)
from .stub import (
    _SAMPLE_PREP_DOMAIN_ACTIONS,
    _STUB_EXTERNAL_CAPABILITIES,
    _handle_stub_external,
    _stub_external_enabled,
)

logger = logging.getLogger(__name__)

TOOL_CLI: Dict[str, str] = build_tool_cli_map()
CAPABILITY_HANDLERS: Dict[str, str] = build_capability_handlers()

def _attach_domain_sample_ref(
    entry,
    action_name: str,
    input_json: Dict[str, Any],
    result: ActionExecutionResult,
) -> ActionExecutionResult:
    sample_id = input_json.get("sampleId")
    sample_dir = input_json.get("sampleDir")
    if not sample_id or not sample_dir:
        return result
    try:
        from methyl_domain.helpers import enrich_sample_prep_output
        from methyl_domain.types import MethylSampleRef, to_tagged_json

        existing = input_json.get("sample")
        if isinstance(existing, dict) and existing.get("$type") == "MethylSampleRef":
            sample = MethylSampleRef.model_validate(existing)
        else:
            sample = MethylSampleRef(sampleId=str(sample_id), sampleDir=str(sample_dir))
        payload = result.output.model_dump(mode="json")
        updated = enrich_sample_prep_output(action_name, sample, payload)
        merged = {**payload, "domainSample": to_tagged_json(updated)}
        from methyl_domain.action_result import utc_now

        started = getattr(result.output, "started_at_utc", None) or utc_now()
        finished = getattr(result.output, "finished_at_utc", None) or utc_now()
        duration = getattr(result.output, "duration_ms", None) or 0
        exit_code = getattr(result.output, "exit_code", None) or 0
        manifest = getattr(result.output, "manifest_path", None)
        output = finalize_output(
            entry,
            merged,
            started_at=started,
            finished_at=finished,
            duration_ms=duration,
            exit_code=exit_code,
            manifest_path=manifest,
        )
        return ActionExecutionResult(result_code=result.result_code, output=output)
    except Exception:
        logger.debug("domain sample enrichment skipped for %s", action_name, exc_info=True)
        return result


def execute_task(
    capability: str,
    action_name: str,
    input_json: Dict[str, Any],
    *,
    handle: Optional[Any] = None,
) -> ActionExecutionResult:
    """Run one ACTION and return typed output + branch result_code for sp_worker_submit_result.

    CAAS is on by default: idempotent actions commit product artifacts to
    ``{project_root}/.caas/`` and reuse entries keyed by ``content_key`` via
    ``maybe_skip_action`` / ``record_action_execution``. Opt out with
    ``caasEnabled: false`` or ``METHYL_CAAS_ENABLED=0``.

    Optional ``handle`` is an :class:`~methyl_worker.execution_handle.ExecutionHandle`
    for agnostic STOP (catalog ``control.can_stop``).
    """
    entry = find_catalog_entry(action_name) or find_catalog_entry_by_capability(capability)
    if entry is None:
        raise RuntimeError(f"Unknown action {action_name!r} / capability {capability!r}")

    from methyl_utils.modality_gate import refuse_methyl_action_for_modality

    _reg = input_json.get("regulatory") if isinstance(input_json.get("regulatory"), dict) else {}
    if not _reg and isinstance(input_json.get("resolvedConfig"), dict):
        _nested = input_json["resolvedConfig"].get("regulatory")
        if isinstance(_nested, dict):
            _reg = _nested
    refuse_methyl_action_for_modality(action_name, _reg.get("primary_modality"))

    from ..action_execution import validate_input
    from ..action_skip import maybe_skip_action, record_action_execution
    from ..task_validation import extract_runtime_input, merge_runtime_input, strip_runtime_input

    runtime = extract_runtime_input(input_json)
    task_input = strip_runtime_input(input_json)
    input_model = validate_input(entry, task_input)
    skip_input = merge_runtime_input(task_input, runtime)

    # Idempotent / CAAS skips must not require a GPU — outputs already exist.
    skipped_result = maybe_skip_action(entry, skip_input)
    if skipped_result is not None:
        _log_action_execution(
            entry, action_name, skip_input, skipped_result, skipped=True, input_model=input_model
        )
        if action_name in _SAMPLE_PREP_DOMAIN_ACTIONS:
            skipped_result = _attach_domain_sample_ref(entry, action_name, task_input, skipped_result)
        return skipped_result

    # Stubbed external tools (CI / dry-run) must not require a host GPU either.
    if _stub_external_enabled() and capability in _STUB_EXTERNAL_CAPABILITIES:
        from ..action_execution import ExecutionTimer, execution_result_from_output

        timer = ExecutionTimer()
        output_model = _handle_stub_external(capability, action_name, input_model)
        finished_at, duration_ms = timer.finish()
        output = finalize_output(
            entry,
            output_model.model_dump(mode="json"),
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        result = execution_result_from_output(output)
    else:
        from ..capabilities import (
            assert_execute_gpu_prereqs,
            assert_execute_host_tool_prereqs,
        )

        # Parabricks runners assert GPU after their product-level output short-circuit
        # so idempotent re-entry works on CPU agents. Other GPU actions guard here.
        if not str(capability).startswith("parabricks."):
            assert_execute_gpu_prereqs(capability, action_name)
        # Per-VM apt CLIs (samtools/bedtools) — shared /work does not provide them.
        assert_execute_host_tool_prereqs(capability, action_name)

        # Resolve via package attribute so tests can monkeypatch
        # ``methyl_worker.handlers.build_action_from_catalog``.
        import methyl_worker.handlers as handlers_pkg

        action = handlers_pkg.build_action_from_catalog(entry, handlers_pkg)
        result = action.execute(skip_input, handle=handle)

    record_action_execution(entry, skip_input, input_model, result, skipped=False)
    if action_name in _SAMPLE_PREP_DOMAIN_ACTIONS:
        result = _attach_domain_sample_ref(entry, action_name, task_input, result)
    _log_action_execution(entry, action_name, skip_input, result, skipped=False, input_model=input_model)
    return result


def _log_action_execution(
    entry,
    action_name: str,
    input_json: Dict[str, Any],
    result: ActionExecutionResult,
    *,
    skipped: bool,
    input_model: Optional[BaseModel] = None,
) -> None:
    try:
        from ..action_run_log import (
            append_action_run_log,
            resolve_workflow_action_log_root,
            task_inputs_for_log,
        )
        from ..action_skip import (
            compute_action_revision,
            compute_input_signature,
            compute_output_signature,
            artifacts_from_output,
        )
        from ..collectors import _run_key

        log_root = resolve_workflow_action_log_root(entry, input_json)
        if log_root is None:
            return

        run_dir = (
            input_json.get("runDir")
            or input_json.get("targetRunDir")
            or input_json.get("outputDir")
        )
        outputs = result.output.model_dump(mode="json")
        if input_model is None:
            from ..action_execution import validate_input

            input_model = validate_input(entry, input_json)
        artifacts = artifacts_from_output(outputs)
        append_action_run_log(
            log_root,
            action=action_name,
            capability=entry.capability,
            category=entry.category,
            result_code=result.result_code,
            run_dir=str(run_dir) if run_dir else None,
            run_key=_run_key(input_json) or None,
            inputs=task_inputs_for_log(input_json),
            outputs=outputs,
            workflow_node_key=input_json.get("workflowNodeKey"),
            started_at_utc=outputs.get("started_at_utc"),
            finished_at_utc=outputs.get("finished_at_utc"),
            duration_ms=outputs.get("duration_ms"),
            status=outputs.get("status"),
            exit_code=outputs.get("exit_code"),
            skipped=skipped,
            skip_reason="signature_match" if skipped else None,
            action_revision=compute_action_revision(entry),
            input_signature=compute_input_signature(entry, input_json, input_model),
            output_signature=compute_output_signature(artifacts),
        )
    except Exception:
        logger.debug("workflow_action_log append skipped for %s", action_name, exc_info=True)

