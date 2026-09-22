"""
Registry of workflow action JSON Schema specs (input/output Pydantic models).

Derived from the unified action catalog (action_catalog.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import List, Optional, Sequence, Type

from pydantic import BaseModel

from .action_catalog import ACTION_CATALOG, ActionCatalogEntry


@dataclass(frozen=True)
class TaskSchemaSpec:
    action_name: str
    schema_id: str
    input_module: str
    input_class: str
    output_module: str
    output_class: str

    def load_input_model(self) -> Type[BaseModel]:
        return _load_model(self.input_module, self.input_class)

    def load_output_model(self) -> Type[BaseModel]:
        return _load_model(self.output_module, self.output_class)

    @property
    def input_filename(self) -> str:
        safe = self.action_name.replace(".", "_")
        return f"{safe}.input.schema.json"

    @property
    def output_filename(self) -> str:
        safe = self.action_name.replace(".", "_")
        return f"{safe}.output.schema.json"


def _load_model(module: str, class_name: str) -> Type[BaseModel]:
    mod = import_module(module)
    model = getattr(mod, class_name, None)
    if model is None:
        raise AttributeError(f"{module}.{class_name} not found")
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError(f"{module}.{class_name} is not a Pydantic BaseModel")
    return model


def _entry_to_task_spec(entry: ActionCatalogEntry) -> TaskSchemaSpec:
    return TaskSchemaSpec(
        action_name=entry.action_name,
        schema_id=entry.schema_id,
        input_module=entry.input_module,
        input_class=entry.input_class,
        output_module=entry.output_module,
        output_class=entry.output_class,
    )


TASK_SCHEMA_SPECS: Sequence[TaskSchemaSpec] = tuple(
    _entry_to_task_spec(entry) for entry in ACTION_CATALOG
)


def list_task_schema_specs() -> List[TaskSchemaSpec]:
    return list(TASK_SCHEMA_SPECS)


def find_task_schema_spec(action_name: str) -> Optional[TaskSchemaSpec]:
    for spec in TASK_SCHEMA_SPECS:
        if spec.action_name == action_name:
            return spec
    return None


def resolve_task_schema_spec(action_name: str, capability: Optional[str] = None) -> Optional[TaskSchemaSpec]:
    spec = find_task_schema_spec(action_name)
    if spec is not None:
        return spec
    if capability == "validation.plan-iterations":
        return find_task_schema_spec("validation.plan_iterations")
    return None
