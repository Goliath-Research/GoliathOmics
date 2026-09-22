"""Deprecated module — import from ``methyl_domain.sample_storage`` instead."""

from __future__ import annotations

from .sample_storage import (
    AzureH5Destination,
    AzureH5StorageDefaults,
    AzureSampleDestination,
    AzureSampleStorageDefaults,
    FileH5Destination,
    FileH5StorageDefaults,
    FileSampleDestination,
    FileSampleStorageDefaults,
    H5DestinationLocation,
    H5StorageDefaults,
    S3H5Destination,
    S3H5StorageDefaults,
    S3SampleDestination,
    S3SampleStorageDefaults,
    SampleDestinationLocation,
    SampleStorageDefaults,
    merge_h5_destination,
    merge_sample_destination,
    resolve_file_local_root,
)

__all__ = [
    "H5StorageDefaults",
    "H5DestinationLocation",
    "merge_h5_destination",
    "resolve_file_local_root",
    "SampleStorageDefaults",
    "SampleDestinationLocation",
    "merge_sample_destination",
    "FileH5StorageDefaults",
    "S3H5StorageDefaults",
    "AzureH5StorageDefaults",
    "FileH5Destination",
    "S3H5Destination",
    "AzureH5Destination",
    "FileSampleStorageDefaults",
    "S3SampleStorageDefaults",
    "AzureSampleStorageDefaults",
    "FileSampleDestination",
    "S3SampleDestination",
    "AzureSampleDestination",
]
