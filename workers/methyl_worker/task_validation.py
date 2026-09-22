"""Validate workflow task input_json / output_json against registered Pydantic models."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from .task_models.runtime_models import TaskRuntimeContext
from .task_schema_registry import resolve_task_schema_spec

TASK_VALIDATION_ERROR_CODE = 4001

# Worker/runtime control fields — not part of task I/O schemas; preserved across normalization.
# If a key is also declared on the action's typed input model (extra=forbid schemas), it stays
# on the task payload and is NOT stripped into the runtime envelope.
RUNTIME_INPUT_KEYS = frozenset({
    "forceRerun",
    "workflowNodeKey",
    "actionConfig",
    "resolvedConfig",
    "resolvedProject",
    "siteConfig",
    "siteConfigPath",
    "pipelineProfile",
    "profilePath",
    "stepOverride",
    "executionScopeId",
    "hyperparamSetId",
    "caasEnabled",
    "sampleCaasEnabled",
})


def runtime_keys_for_model(model: Type[BaseModel] | None) -> frozenset[str]:
    """Runtime-only keys after subtracting fields owned by the typed task input schema."""
    if model is None:
        return RUNTIME_INPUT_KEYS
    declared = set(model.model_fields)
    return frozenset(key for key in RUNTIME_INPUT_KEYS if key not in declared)


def strip_runtime_input(
    input_json: Dict[str, Any],
    model: Type[BaseModel] | None = None,
) -> Dict[str, Any]:
    """Task payload only (fields accepted by the registered input model)."""
    skip = runtime_keys_for_model(model)
    return {k: v for k, v in input_json.items() if k not in skip}


def extract_runtime_input(
    input_json: Dict[str, Any],
    model: Type[BaseModel] | None = None,
) -> Dict[str, Any]:
    keys = runtime_keys_for_model(model)
    return {k: input_json[k] for k in keys if k in input_json}


def merge_runtime_input(task_input: Dict[str, Any], runtime: Dict[str, Any]) -> Dict[str, Any]:
    if not runtime:
        return task_input
    return {**task_input, **runtime}


class TaskValidationError(ValueError):
    def __init__(self, message: str, *, direction: str, action_name: str) -> None:
        super().__init__(message)
        self.direction = direction
        self.action_name = action_name


def _validate_input_model(model: type[BaseModel], payload: Dict[str, Any]) -> None:
    model.model_validate(payload)


def _coerce_output_model(model: type[BaseModel], output: BaseModel) -> BaseModel:
    if isinstance(output, model):
        return output
    return model.model_validate(output)


def normalize_task_input(
    action_name: str,
    capability: Optional[str],
    input_json: Dict[str, Any],
) -> Dict[str, Any]:
    """Drop template fields not in the task input schema (e.g. projectPath)."""
    spec = resolve_task_schema_spec(action_name, capability)
    if spec is None:
        return input_json
    model = spec.load_input_model()
    runtime = extract_runtime_input(input_json, model)
    allowed = set(model.model_fields.keys())
    filtered = {k: v for k, v in input_json.items() if k in allowed}
    normalized = model.model_validate(filtered).model_dump(
        mode="python", exclude_none=True, exclude_unset=True
    )
    return merge_runtime_input(normalized, runtime)


def parse_task_envelope(
    action_name: str,
    capability: Optional[str],
    input_json: Mapping[str, Any],
) -> Tuple[BaseModel, TaskRuntimeContext]:
    """Validate task input and parse runtime envelope from wire JSON."""
    payload = dict(input_json)
    spec = resolve_task_schema_spec(action_name, capability)
    if spec is None:
        raise TaskValidationError(
            f"No input schema registered for {action_name!r}",
            direction="input",
            action_name=action_name,
        )
    model_cls = spec.load_input_model()
    runtime_wire = extract_runtime_input(payload, model_cls)
    task_payload = strip_runtime_input(payload, model_cls)
    try:
        task_model = model_cls.model_validate(task_payload)
    except ValidationError as exc:
        raise TaskValidationError(
            f"input_json failed schema validation for {action_name}: {exc}",
            direction="input",
            action_name=action_name,
        ) from exc
    runtime = TaskRuntimeContext.from_wire(runtime_wire)
    return task_model, runtime


def validate_task_input(
    action_name: str,
    capability: Optional[str],
    input_json: Dict[str, Any],
) -> None:
    spec = resolve_task_schema_spec(action_name, capability)
    if spec is None:
        return
    try:
        normalized = normalize_task_input(action_name, capability, input_json)
        _validate_input_model(spec.load_input_model(), strip_runtime_input(normalized))
    except ValidationError as exc:
        raise TaskValidationError(
            f"input_json failed schema validation for {action_name}: {exc}",
            direction="input",
            action_name=action_name,
        ) from exc


def validate_task_output(
    action_name: str,
    capability: Optional[str],
    output: BaseModel,
) -> BaseModel:
    """Validate an ACTION output model; return the coerced registry output type."""
    spec = resolve_task_schema_spec(action_name, capability)
    if spec is None:
        return output
    try:
        return _coerce_output_model(spec.load_output_model(), output)
    except ValidationError as exc:
        raise TaskValidationError(
            f"output failed schema validation for {action_name}: {exc}",
            direction="output",
            action_name=action_name,
        ) from exc


def try_validate_task_input(
    action_name: str,
    capability: Optional[str],
    input_json: Dict[str, Any],
) -> Optional[str]:
    try:
        validate_task_input(action_name, capability, input_json)
        return None
    except TaskValidationError as exc:
        return str(exc)


def try_validate_task_output(
    action_name: str,
    capability: Optional[str],
    output: BaseModel,
) -> Optional[str]:
    try:
        validate_task_output(action_name, capability, output)
        return None
    except TaskValidationError as exc:
        return str(exc)
