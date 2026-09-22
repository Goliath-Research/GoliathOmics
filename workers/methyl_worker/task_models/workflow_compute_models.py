"""Typed workflow compute ACTION I/O (const / json_path / fs_stat)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .base import ActionOutputBase


class WorkflowComputeEnvelope(BaseModel):
    """Fields the DomainProgram compiler always injects into ACTION templates."""

    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = Field(default=None, description="Catalog tool label (compiler-injected).")
    projectPath: Optional[str] = Field(
        default=None, description="Study manifest path (compiler-injected; provenance only)."
    )
    executionScopeId: Optional[str] = Field(
        default=None,
        description="Execution scope id (compiler-injected; unused by compute).",
    )
    hyperparamSetId: Optional[str] = Field(
        default=None,
        description="Deprecated alias for executionScopeId (compiler-injected; unused by compute).",
    )


class ConstBoolTaskInput(WorkflowComputeEnvelope):
    value: bool = Field(description="Boolean constant to publish into scope.")


class ConstBoolTaskOutput(ActionOutputBase):
    value: bool = False


class ConstIntTaskInput(WorkflowComputeEnvelope):
    value: int = Field(description="Integer constant to publish into scope.")


class ConstIntTaskOutput(ActionOutputBase):
    value: int = 0


class ConstStringTaskInput(WorkflowComputeEnvelope):
    value: str = Field(description="String constant to publish into scope.")


class ConstStringTaskOutput(ActionOutputBase):
    value: str = ""


class ConstPathTaskInput(WorkflowComputeEnvelope):
    value: str = Field(description="Filesystem path string to publish into scope.")


class ConstPathTaskOutput(ActionOutputBase):
    value: str = ""


class JsonPathFileTaskInput(WorkflowComputeEnvelope):
    documentPath: str = Field(description="Absolute path to a JSON document on shared storage.")
    jsonPath: str = Field(
        description="JSONPath-like dotted path starting with $ (e.g. $.hasMore or $.meta.count)."
    )


class JsonPathBoolTaskInput(JsonPathFileTaskInput):
    pass


class JsonPathBoolTaskOutput(ActionOutputBase):
    value: bool = False


class JsonPathIntTaskInput(JsonPathFileTaskInput):
    pass


class JsonPathIntTaskOutput(ActionOutputBase):
    value: int = 0


class JsonPathStringTaskInput(JsonPathFileTaskInput):
    pass


class JsonPathStringTaskOutput(ActionOutputBase):
    value: str = ""


class FsStatTaskInput(WorkflowComputeEnvelope):
    path: str = Field(description="Filesystem path to stat.")


class FsStatTaskOutput(ActionOutputBase):
    exists: bool = False
    size: int = 0
    mtimeUtc: Optional[str] = None
    # Convenience for assign bindings that expect $.value as bool exists
    value: bool = False
