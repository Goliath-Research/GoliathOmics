"""Tunable cloud transfer settings (site/profile actionConfig.storage_transfer)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StorageTransferStepConfig(BaseModel):
    """Operator-set multipart / concurrency knobs for S3 and Azure Blob transfers.

    Set under site or profile ``actionConfig.storage_transfer``. All fields default
    to ``None`` (config-not-code); missing values leave SDK defaults in place.
    """

    model_config = ConfigDict(extra="forbid")

    max_concurrency: Optional[int] = Field(
        default=None,
        ge=1,
        le=64,
        description=(
            "Max parallel part/blob streams per object and max parallel files "
            "within one sample transfer. Operator-set via site/profile "
            "actionConfig.storage_transfer."
        ),
    )
    multipart_threshold_mb: Optional[int] = Field(
        default=None,
        ge=1,
        le=1024,
        description=(
            "S3 multipart threshold in MiB. Operator-set via "
            "actionConfig.storage_transfer."
        ),
    )
    multipart_chunksize_mb: Optional[int] = Field(
        default=None,
        ge=1,
        le=512,
        description=(
            "S3 multipart part size in MiB. Operator-set via "
            "actionConfig.storage_transfer."
        ),
    )
    max_attempts: Optional[int] = Field(
        default=None,
        ge=1,
        le=20,
        description=(
            "Max transfer attempts (botocore / Azure retry budget). Operator-set via "
            "actionConfig.storage_transfer."
        ),
    )
    mtime_tolerance_s: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=60.0,
        description=(
            "Seconds of mtime skew allowed when skipping unchanged downloads. "
            "Operator-set via actionConfig.storage_transfer."
        ),
    )


def transfer_config_from_mapping(raw: object | None) -> StorageTransferStepConfig:
    """Parse a resolvedConfig / actionConfig.storage_transfer slice."""
    if raw is None:
        return StorageTransferStepConfig()
    if isinstance(raw, StorageTransferStepConfig):
        return raw
    if isinstance(raw, dict):
        return StorageTransferStepConfig.model_validate(raw)
    return StorageTransferStepConfig()
