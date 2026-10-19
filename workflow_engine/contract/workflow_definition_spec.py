"""
Pydantic models for programmatic workflow definition creation.

Used by direct-DB deploy (``ops.workflow_deploy``) and wf.wf_repo_create_workflow_graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


NodeType = Literal[
    "ACTION",
    "SEQUENCE",
    "PARALLEL",
    "IF",
    "SWITCH",
    "REPEAT",
    "WHILE",
    "FOREACH",
    "WAIT_EVENT",
]

EventSubscriptionMode = Literal["start_instance", "signal_wait", "start_or_signal"]

BranchKind = Literal[
    "SEQUENCE",
    "PARALLEL",
    "THEN",
    "ELSE",
    "CASE",
    "DEFAULT",
    "BODY",
]

OutputBindingKind = Literal["output_path", "result_code"]


class WorkflowNodeSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_key: str
    node_type: NodeType
    action_name: Optional[str] = None
    repeat_count: Optional[int] = None
    condition_ref_node_key: Optional[str] = None
    switch_ref_node_key: Optional[str] = None
    condition_var: Optional[str] = None
    switch_var: Optional[str] = None
    foreach_collection_var: Optional[str] = None
    foreach_item_var: Optional[str] = None
    foreach_index_var: Optional[str] = None
    foreach_parallel: Optional[bool] = None
    wait_event_type: Optional[str] = None
    wait_correlation_var: Optional[str] = None
    input_template: Optional[Dict[str, Any]] = None


class EventTriggerSpec(BaseModel):
    """Compiled DomainProgram ``on`` entry → ``wf.event_subscription``."""

    model_config = ConfigDict(extra="ignore")

    event: str
    mode: EventSubscriptionMode = "start_instance"
    filter_json: Optional[Dict[str, Any]] = None
    enabled: bool = True


class WorkflowEdgeSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parent_node_key: str
    child_node_key: str
    child_order: int = 0
    branch_kind: Optional[BranchKind] = None
    condition_expr: Optional[str] = None
    switch_case_value: Optional[int] = None
    is_default: bool = False


class WorkflowInputBindingSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_key: str
    target_json_path: str
    source_expr: str
    is_required: bool = False


class WorkflowOutputBindingSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_key: str
    var_name: str
    source_kind: OutputBindingKind
    source_json_path: Optional[str] = None


class WorkflowScopeDefaultSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_key: str
    var_name: str
    default_expr: str


CollectionSourceKind = Literal["jsonFile", "jsonPath"]


class CollectionBindingSpec(BaseModel):
    """Generic scope collection resolved by the engine before FOREACH runs."""

    model_config = ConfigDict(extra="ignore")

    scope_var: str
    kind: CollectionSourceKind
    path_var: Optional[str] = None
    base_var: Optional[str] = None
    json_path: Optional[str] = None
    bind_order: int = 0


class WorkflowDefinitionSpec(BaseModel):
    """Editor output contract for wf_repo_create_workflow_graph."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: Optional[str] = None
    version_major: int = 1
    version_minor: int = 0
    is_active: bool = True
    root_node_key: str
    nodes: List[WorkflowNodeSpec]
    edges: List[WorkflowEdgeSpec] = Field(default_factory=list)
    input_bindings: List[WorkflowInputBindingSpec] = Field(default_factory=list)
    output_bindings: List[WorkflowOutputBindingSpec] = Field(default_factory=list)
    scope_defaults: List[WorkflowScopeDefaultSpec] = Field(default_factory=list)
    collection_bindings: List[CollectionBindingSpec] = Field(default_factory=list)
    event_triggers: List[EventTriggerSpec] = Field(default_factory=list)
    # name → schemaRef string or inline JSON Schema object (from DomainProgram variable decls)
    variable_schemas: Dict[str, Any] = Field(default_factory=dict)

    def to_db_spec(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def repo_schemas_workflow_dir() -> Path:
    """``<repo>/schemas/workflow`` — walk-up so site-packages installs still find committed schemas."""
    try:
        from methyl_utils.repo_paths import repo_schemas_dir

        return repo_schemas_dir("workflow", start=Path(__file__))
    except Exception:
        start = Path(__file__).resolve()
        for candidate in (start, *start.parents, Path.cwd().resolve(), *Path.cwd().resolve().parents):
            marker = candidate / "schemas" / "workflow"
            if marker.is_dir() and (candidate / "scripts" / "packages.list").is_file():
                return marker
        return Path(__file__).resolve().parents[2] / "schemas" / "workflow"


def export_workflow_definition_schema(*, output_root: Path | None = None, write: bool = True) -> Path:
    schema = WorkflowDefinitionSpec.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema.setdefault("title", "WorkflowDefinitionSpec")
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    root = output_root if output_root is not None else repo_schemas_workflow_dir()
    path = root / "workflow_definition.schema.json"
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path


def check_workflow_definition_schema_drift(*, output_root: Path | None = None) -> List[str]:
    root = output_root if output_root is not None else repo_schemas_workflow_dir()
    path = root / "workflow_definition.schema.json"
    schema = WorkflowDefinitionSpec.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema.setdefault("title", "WorkflowDefinitionSpec")
    expected = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if not path.is_file():
        return [f"missing schema artifact: {path}"]
    if path.read_text(encoding="utf-8") != expected:
        return [f"stale schema artifact: {path}"]
    return []
