"""Typed ACTION execution helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Type

from methyl_domain.action_result import utc_now
from pydantic import BaseModel

from .action_catalog import ActionCatalogEntry
from .task_schema_registry import resolve_task_schema_spec


@dataclass(frozen=True)
class ActionExecutionResult:
    result_code: int
    output: BaseModel


def load_input_model(entry: ActionCatalogEntry) -> Type[BaseModel]:
    spec = resolve_task_schema_spec(entry.action_name, entry.capability)
    if spec is None:
        raise RuntimeError(f"No input schema for {entry.action_name!r}")
    return spec.load_input_model()


def load_output_model(entry: ActionCatalogEntry) -> Type[BaseModel]:
    spec = resolve_task_schema_spec(entry.action_name, entry.capability)
    if spec is None:
        raise RuntimeError(f"No output schema for {entry.action_name!r}")
    return spec.load_output_model()


def validate_input(entry: ActionCatalogEntry, payload: Mapping[str, Any]) -> BaseModel:
    return load_input_model(entry).model_validate(dict(payload))


def finalize_output(
    entry: ActionCatalogEntry,
    payload: Mapping[str, Any],
    *,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    exit_code: int = 0,
    manifest_path: Optional[str] = None,
) -> BaseModel:
    output_model = load_output_model(entry)
    merged: dict[str, Any] = {
        "action_name": entry.action_name,
        "capability": entry.capability,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "manifest_path": manifest_path,
    }
    for key, val in payload.items():
        if key in output_model.model_fields:
            merged[key] = val
    out = output_model.model_validate(merged)
    rc = getattr(out, "result_code", 0)
    if not isinstance(rc, int):
        rc = 0
    return out


def execution_result_from_output(output: BaseModel) -> ActionExecutionResult:
    rc = getattr(output, "result_code", 0)
    if not isinstance(rc, int):
        rc = 0
    return ActionExecutionResult(result_code=rc, output=output)


class ExecutionTimer:
    def __init__(self) -> None:
        self.started_at = utc_now()
        self._t0 = time.perf_counter()

    def finish(self) -> tuple[datetime, int]:
        finished_at = utc_now()
        duration_ms = int((time.perf_counter() - self._t0) * 1000)
        return finished_at, duration_ms
