"""Worker/runtime envelope models (separate from task I/O schemas)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict

from methyl_validation.config import ValidationStepConfig, parse_validation_profile


class TaskRuntimeContext(BaseModel):
    """Runtime fields injected by the workflow engine (not task input schema)."""

    model_config = ConfigDict(extra="forbid")

    forceRerun: Optional[bool] = None
    workflowNodeKey: Optional[str] = None
    pipelineProfile: Optional[str] = None
    profilePath: Optional[str] = None
    siteConfigPath: Optional[str] = None
    validationProfile: Optional[ValidationStepConfig] = None

    @classmethod
    def from_wire(cls, wire: Mapping[str, Any]) -> TaskRuntimeContext:
        data = {key: wire[key] for key in cls.model_fields if key in wire and key != "validationProfile"}
        profile = parse_validation_profile(wire.get("resolvedConfig"))
        if profile is not None:
            data["validationProfile"] = profile
        return cls.model_validate(data)
