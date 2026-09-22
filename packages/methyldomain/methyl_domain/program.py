"""DomainProgram JSON IR — declarative high-level workflow language."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CollectionInSpec(BaseModel):
    """Reference to a scope collection or project-bound path."""

    model_config = ConfigDict(extra="ignore")

    ref: Optional[str] = None
    var: Optional[str] = None


class ForeachSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    collection: Optional[str] = None
    in_: Optional[Union[str, CollectionInSpec]] = Field(default=None, alias="in")
    item: Optional[str] = None
    as_: Optional[str] = Field(default=None, alias="as")
    index: Optional[str] = None
    parallel: bool = False

    def resolved_item(self) -> str:
        return self.as_ or self.item or "item"

    def resolved_collection_var(self) -> str:
        if self.collection:
            return self.collection
        if isinstance(self.in_, CollectionInSpec):
            if self.in_.var:
                return self.in_.var
            if self.in_.ref and self.in_.ref.startswith("project."):
                return self.in_.ref.split(".", 1)[1]
            if self.in_.ref:
                return self.in_.ref.split(".")[-1]
        if isinstance(self.in_, str):
            if self.in_.startswith("project."):
                return self.in_.split(".", 1)[1]
            return self.in_
        raise ValueError("foreach requires collection or in")


class VariableDecl(BaseModel):
    """Typed scope variable declaration (JSON Schema reference or inline schema)."""

    model_config = ConfigDict(extra="forbid")

    schemaRef: Optional[str] = None
    schema_: Optional[Union[str, Dict[str, Any]]] = Field(default=None, alias="schema")
    description: Optional[str] = None

    @model_validator(mode="after")
    def _require_schema(self) -> "VariableDecl":
        if not self.schemaRef and self.schema_ is None:
            raise ValueError("VariableDecl requires schemaRef or schema")
        return self

    def resolved_schema_ref(self) -> Optional[str]:
        if self.schemaRef:
            return self.schemaRef
        if isinstance(self.schema_, str):
            return self.schema_
        return None

    def resolved_schema_body(self) -> Optional[Dict[str, Any]]:
        if isinstance(self.schema_, dict):
            return self.schema_
        return None


class ActionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Optional[str] = None
    do: Optional[str] = None
    in_: Optional[Dict[str, Any]] = Field(default=None, alias="in")
    with_: Optional[Dict[str, Any]] = Field(default=None, alias="with")
    out: Optional[Dict[str, str]] = None
    node_key: Optional[str] = None

    def action_name(self) -> str:
        name = self.do or self.action
        if not name:
            raise ValueError("action step requires do or action")
        return name


class AssignStep(BaseModel):
    """Worker-delegated assignment: catalog ACTION + output binding into a declared variable."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    assign: str
    using: Optional[str] = None
    do: Optional[str] = None
    with_: Optional[Dict[str, Any]] = Field(default=None, alias="with")
    from_path: str = Field(default="$.value", alias="fromPath")
    node_key: Optional[str] = None

    def action_name(self) -> str:
        name = self.using or self.do
        if not name:
            raise ValueError("assign step requires using or do (catalog action)")
        return name

    def as_action_step(self) -> ActionStep:
        return ActionStep(
            do=self.action_name(),
            with_=self.with_,
            out={self.assign: self.from_path},
            node_key=self.node_key,
        )


class IfStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    if_: str = Field(alias="if")
    then: List["ProgramStep"] = Field(default_factory=list)
    else_: List["ProgramStep"] = Field(default_factory=list, alias="else")


class SwitchStep(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    switch: Union[str, Dict[str, Any]]
    cases: Dict[str, List["ProgramStep"]] = Field(default_factory=dict)
    default: List["ProgramStep"] = Field(default_factory=list)


class WhileStep(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    while_: str = Field(alias="while")
    do: List["ProgramStep"] = Field(default_factory=list)
    body: List["ProgramStep"] = Field(default_factory=list)

    def steps(self) -> List[Any]:
        return self.do if self.do else self.body


class RepeatStep(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    repeat: Union[int, Dict[str, Any]]
    do: List["ProgramStep"] = Field(default_factory=list)
    body: List["ProgramStep"] = Field(default_factory=list)

    def count(self) -> int:
        if isinstance(self.repeat, int):
            return self.repeat
        if isinstance(self.repeat, dict) and "count" in self.repeat:
            return int(self.repeat["count"])
        raise ValueError("repeat requires integer count or {\"count\": N}")

    def steps(self) -> List[Any]:
        return self.do if self.do else self.body


class ForeachStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    foreach: Optional[ForeachSpec] = None
    for_: Optional[ForeachSpec] = Field(default=None, alias="for")
    steps: List["ProgramStep"] = Field(default_factory=list)
    do: List["ProgramStep"] = Field(default_factory=list)

    def spec(self) -> ForeachSpec:
        s = self.for_ or self.foreach
        if s is None:
            raise ValueError("foreach step missing for/foreach spec")
        return s

    def body(self) -> List[Any]:
        return self.do if self.do else self.steps


class ParallelStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parallel: List["ProgramStep"] = Field(default_factory=list)


class EventTriggerSpec(BaseModel):
    """Program-level event subscription (start or signal)."""

    model_config = ConfigDict(extra="ignore")

    event: str
    mode: Literal["start_instance", "signal_wait", "start_or_signal"] = "start_instance"
    filter: Optional[Dict[str, Any]] = None


class WaitSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str
    correlation: Optional[str] = None


class WaitStep(BaseModel):
    """Park the graph until a matching event is ingested (WAIT_EVENT node)."""

    model_config = ConfigDict(extra="ignore")

    wait: WaitSpec
    node_key: Optional[str] = None


ProgramStep = Union[
    ActionStep,
    AssignStep,
    IfStep,
    SwitchStep,
    WhileStep,
    RepeatStep,
    ForeachStep,
    ParallelStep,
    WaitStep,
    Dict[str, Any],
]


class ProgramPhase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    foreach: Optional[ForeachSpec] = None
    steps: List[Any] = Field(default_factory=list)


class DomainProgram(BaseModel):
    """Client-side program lowered to WorkflowDefinitionSpec + context_json."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    programVersion: int = 1
    name: str
    description: Optional[str] = None
    projectPath: str
    # Legacy context seeds (scalars/lists) and/or VariableDecl objects (schemaRef/schema).
    variables: Dict[str, Any] = Field(default_factory=dict)
    declarations: Dict[str, Any] = Field(default_factory=dict)
    phases: List[ProgramPhase] = Field(default_factory=list)
    body: List[Any] = Field(default_factory=list)
    on: List[EventTriggerSpec] = Field(default_factory=list)

    def split_variables(self) -> tuple[Dict[str, Any], Dict[str, VariableDecl]]:
        """Split ``variables`` into context seeds vs typed declarations."""
        context: Dict[str, Any] = {}
        decls: Dict[str, VariableDecl] = {}
        for key, value in self.variables.items():
            if isinstance(value, VariableDecl):
                decls[key] = value
            elif isinstance(value, dict) and ("schemaRef" in value or "schema" in value):
                decls[key] = VariableDecl.model_validate(value)
            else:
                context[key] = value
        return context, decls


IfStep.model_rebuild()
SwitchStep.model_rebuild()
WhileStep.model_rebuild()
RepeatStep.model_rebuild()
ForeachStep.model_rebuild()
ParallelStep.model_rebuild()
WaitStep.model_rebuild()
AssignStep.model_rebuild()
