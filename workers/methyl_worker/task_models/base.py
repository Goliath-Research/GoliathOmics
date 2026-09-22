"""Shared strict base models for workflow ACTION I/O."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from methyl_domain.action_result import ArtifactRef
from pydantic import BaseModel, ConfigDict, Field


class ActionOutputBase(BaseModel):
    """Telemetry and branch code fields included in every ACTION output_json."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    action_name: Optional[str] = None
    capability: Optional[str] = None
    started_at_utc: Optional[datetime] = None
    finished_at_utc: Optional[datetime] = None
    duration_ms: Optional[int] = None
    result_code: int = 0
    exit_code: int = 0
    manifest_path: Optional[str] = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    status: Literal["ok", "skipped", "failed"] = "ok"
    tool: Optional[str] = None
    stdout_tail: Optional[str] = None
    domainSample: Optional[Any] = None
