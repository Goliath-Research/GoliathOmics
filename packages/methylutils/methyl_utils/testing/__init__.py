"""Shared test-support helpers for MethylPipeline (real reference-sample access)."""

from .real_data import (
    ResolvedReferenceSample,
    ResolvedReferenceGroup,
    available_reference_sample,
    reference_sample,
    require_reference_group,
    require_reference_sample,
)

__all__ = [
    "ResolvedReferenceSample",
    "ResolvedReferenceGroup",
    "available_reference_sample",
    "reference_sample",
    "require_reference_group",
    "require_reference_sample",
]
