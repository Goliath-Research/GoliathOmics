"""Minimal stand-in for packages/methylvalidation config (docs smoke only)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ValidationStepConfig(BaseModel):
    """Placeholder; real model lives in methylvalidation."""

    model_config = ConfigDict(extra="allow")


def parse_validation_profile(resolved: Any) -> Optional[ValidationStepConfig]:
    if resolved is None:
        return None
    if isinstance(resolved, dict):
        return ValidationStepConfig.model_validate(resolved)
    return None
