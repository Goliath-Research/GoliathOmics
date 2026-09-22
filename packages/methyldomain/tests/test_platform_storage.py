"""Tests for portal profile JSON → h5Storage mapping."""

from __future__ import annotations

from methyl_domain.platform_storage import (
    DEFAULT_ARCHIVE_PROFILE_KEY,
    normalize_s3_endpoint_url,
    profile_json_to_h5_storage,
    profile_json_to_h5_storage_dict,
)
from methyl_domain.fastq_storage import resolve_sample_storage_prefix


def test_normalize_s3_endpoint_url_adds_https() -> None:
    assert normalize_s3_endpoint_url("s3.us-east-1.myqnapcloud.io") == (
        "https://s3.us-east-1.myqnapcloud.io"
    )


def test_profile_json_builds_myqnap_h5_defaults() -> None:
    profile_json = {
        "type": "s3",
        "bucket": "goliath",
        "region": "us-east-1",
        "endpointUrl": "s3.us-east-1.myqnapcloud.io",
        "prefixBase": "samples/",
        "credentials": {
            "authMode": "explicit_keys",
            "accessKeyId": "AKIATEST",
            "secretAccessKey": "secret",
        },
    }
    h5 = profile_json_to_h5_storage(profile_json)
    assert h5.bucket == "goliath"
    assert h5.endpointUrl == "https://s3.us-east-1.myqnapcloud.io"
    assert h5.prefixBase == "samples/"
    assert h5.credentials.accessKeyId == "AKIATEST"
    prefix = resolve_sample_storage_prefix(sample_id="S1", prefix_base=h5.prefixBase)
    assert prefix == "samples/S1/"
    assert DEFAULT_ARCHIVE_PROFILE_KEY == "goliath-samples"


def test_profile_json_to_h5_storage_dict_reveals_secrets() -> None:
    profile_json = {
        "type": "s3",
        "bucket": "goliath",
        "credentials": {
            "authMode": "explicit_keys",
            "accessKeyId": "AKIATEST",
            "secretAccessKey": "real-secret",
        },
    }
    dumped = profile_json_to_h5_storage_dict(profile_json)
    assert dumped["credentials"]["secretAccessKey"] == "real-secret"
