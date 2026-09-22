"""
Structured sample archive destinations for Portal JSON and upload workers.

Instance-level ``SampleStorageDefaults`` plus per-sample ``prefix`` merge into
``SampleDestinationLocation`` on each archive task (FASTQs, QC JSON, HDF5, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .fastq_storage import (
    AzureCredentials,
    S3Credentials,
    StorageChangeTokens,
    _token_kwargs,
    normalize_sample_prefix,
)

__all__ = [
    "SampleStorageDefaults",
    "SampleDestinationLocation",
    "merge_sample_destination",
    "resolve_file_local_root",
    # Legacy aliases (deprecated)
    "H5StorageDefaults",
    "H5DestinationLocation",
    "FileH5Destination",
    "S3H5Destination",
    "AzureH5Destination",
    "FileH5StorageDefaults",
    "S3H5StorageDefaults",
    "AzureH5StorageDefaults",
    "merge_h5_destination",
]


class FileSampleStorageDefaults(StorageChangeTokens):
    type: Literal["file"] = "file"
    basePath: str


class S3SampleStorageDefaults(StorageChangeTokens):
    type: Literal["s3"] = "s3"
    bucket: str
    region: str | None = None
    endpointUrl: str | None = None
    prefixBase: str | None = None
    credentials: S3Credentials


class AzureSampleStorageDefaults(StorageChangeTokens):
    type: Literal["azure_blob"] = "azure_blob"
    account: str
    container: str
    credentials: AzureCredentials


SampleStorageDefaults = Annotated[
    Union[FileSampleStorageDefaults, S3SampleStorageDefaults, AzureSampleStorageDefaults],
    Field(discriminator="type"),
]


class FileSampleDestination(StorageChangeTokens):
    type: Literal["file"] = "file"
    basePath: str
    prefix: str = ""

    @model_validator(mode="after")
    def _normalize_prefix(self) -> FileSampleDestination:
        if self.prefix:
            object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


class S3SampleDestination(StorageChangeTokens):
    type: Literal["s3"] = "s3"
    bucket: str
    prefix: str
    region: str | None = None
    endpointUrl: str | None = None
    credentials: S3Credentials

    @model_validator(mode="after")
    def _normalize_prefix(self) -> S3SampleDestination:
        object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


class AzureSampleDestination(StorageChangeTokens):
    type: Literal["azure_blob"] = "azure_blob"
    account: str
    container: str
    prefix: str
    credentials: AzureCredentials

    @model_validator(mode="after")
    def _normalize_prefix(self) -> AzureSampleDestination:
        object.__setattr__(self, "prefix", normalize_sample_prefix(self.prefix))
        return self


SampleDestinationLocation = Annotated[
    Union[FileSampleDestination, S3SampleDestination, AzureSampleDestination],
    Field(discriminator="type"),
]


def merge_sample_destination(
    defaults: SampleStorageDefaults,
    prefix: str,
) -> SampleDestinationLocation:
    """Materialize per-sample destination from instance defaults and sample prefix."""
    norm = normalize_sample_prefix(prefix)
    tokens = _token_kwargs(defaults)
    if isinstance(defaults, FileSampleStorageDefaults):
        return FileSampleDestination(basePath=defaults.basePath, prefix=norm, **tokens)
    if isinstance(defaults, S3SampleStorageDefaults):
        return S3SampleDestination(
            bucket=defaults.bucket,
            prefix=norm,
            region=defaults.region,
            endpointUrl=defaults.endpointUrl,
            credentials=defaults.credentials,
            **tokens,
        )
    if isinstance(defaults, AzureSampleStorageDefaults):
        return AzureSampleDestination(
            account=defaults.account,
            container=defaults.container,
            prefix=norm,
            credentials=defaults.credentials,
            **tokens,
        )
    raise TypeError(f"unsupported sample storage defaults: {type(defaults)!r}")


def resolve_file_local_root(destination: FileSampleDestination) -> Path:
    base = Path(destination.basePath).expanduser()
    if not destination.prefix:
        return base
    return base / destination.prefix.rstrip("/")


# Legacy aliases (deprecated — use Sample* names)
FileH5StorageDefaults = FileSampleStorageDefaults
S3H5StorageDefaults = S3SampleStorageDefaults
AzureH5StorageDefaults = AzureSampleStorageDefaults
H5StorageDefaults = SampleStorageDefaults

FileH5Destination = FileSampleDestination
S3H5Destination = S3SampleDestination
AzureH5Destination = AzureSampleDestination
H5DestinationLocation = SampleDestinationLocation

merge_h5_destination = merge_sample_destination
