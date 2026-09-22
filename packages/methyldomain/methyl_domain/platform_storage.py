"""
Map portal resource profile JSON into domain storage models.

``portal.resource_profile.profile_json`` for archive rows validates as
``SampleStorageDefaults``. Initial FASTQ ingress always uses laboratory
``fastqStorage`` from the study start request — never from portal profiles.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import TypeAdapter

from .sample_storage import SampleStorageDefaults

DEFAULT_ARCHIVE_PROFILE_KEY = "goliath-samples"
DEFAULT_STORAGE_KEY = DEFAULT_ARCHIVE_PROFILE_KEY

_SAMPLE_DEFAULTS_ADAPTER = TypeAdapter(SampleStorageDefaults)
_H5_DEFAULTS_ADAPTER = _SAMPLE_DEFAULTS_ADAPTER


def normalize_s3_endpoint_url(endpoint_url: str) -> str:
    url = str(endpoint_url).strip()
    if not url:
        raise ValueError("endpoint_url is required")
    if not url.startswith("http://") and not url.startswith("https://"):
        return f"https://{url}"
    return url


def profile_json_to_sample_storage(profile_json: Mapping[str, Any]) -> SampleStorageDefaults:
    """Validate portal profile JSON as instance-level sampleStorage defaults."""
    data = dict(profile_json)
    endpoint = data.get("endpointUrl")
    if isinstance(endpoint, str) and endpoint:
        data["endpointUrl"] = normalize_s3_endpoint_url(endpoint)
    return _SAMPLE_DEFAULTS_ADAPTER.validate_python(data)


def profile_json_to_h5_storage(profile_json: Mapping[str, Any]) -> SampleStorageDefaults:
    """Deprecated alias for ``profile_json_to_sample_storage``."""
    return profile_json_to_sample_storage(profile_json)


def profile_json_to_sample_storage_dict(profile_json: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and dump sampleStorage defaults with secrets revealed for workers."""
    from .fastq_storage import dump_storage_model

    return dump_storage_model(profile_json_to_sample_storage(profile_json))


def profile_json_to_h5_storage_dict(profile_json: Mapping[str, Any]) -> dict[str, Any]:
    return profile_json_to_sample_storage_dict(profile_json)
