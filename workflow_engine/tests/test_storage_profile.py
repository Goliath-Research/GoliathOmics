"""Request wire locations win; otherwise cfg.storage_profile is expanded."""

from __future__ import annotations

import pytest
from cfg.store import FileConfigStore

from ops.storage_profile import apply_storage_to_planner_payload, storage_location_is_wire


def _store(tmp_path) -> FileConfigStore:
    store = FileConfigStore(tmp_path)
    store.upsert(
        "credential",
        "lab",
        {
            "provider": "s3",
            "authMode": "static",
            "accessKeyId": "AKIA",
            "secretAccessKey": "secret",
        },
        status="published",
        extra={"provider": "s3"},
    )
    store.upsert(
        "storage_endpoint",
        "reads",
        {
            "type": "s3",
            "bucket": "reads-bucket",
            "region": "us-east-1",
            "credentialName": "lab",
        },
        status="published",
        extra={"provider": "s3", "credentialName": "lab"},
    )
    store.upsert(
        "storage_endpoint",
        "archive",
        {"type": "file", "basePath": "/work/archive"},
        status="published",
        extra={"provider": "file"},
    )
    store.upsert(
        "storage_profile",
        "goliath-samples",
        {"fastqStorageEndpoint": "reads", "sampleStorageEndpoint": "archive"},
        status="published",
    )
    return store


def test_wire_locations_are_kept_without_reading_a_profile(tmp_path) -> None:
    store = _store(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("profile lookup")

    store.get = _boom  # type: ignore[method-assign]
    fastq = {"type": "file", "basePath": "/data/fastq"}
    sample = {"type": "file", "basePath": "/data/samples"}
    out = apply_storage_to_planner_payload(
        None,
        {"fastqStorage": fastq, "sampleStorage": sample},
        store=store,
    )
    assert out["fastqStorage"] == fastq
    assert out["sampleStorage"] == sample
    assert storage_location_is_wire(out["fastqStorage"])


def test_named_profile_expands_when_the_request_has_no_locations(tmp_path) -> None:
    out = apply_storage_to_planner_payload(None, {}, store=_store(tmp_path))
    assert out["fastqStorage"]["bucket"] == "reads-bucket"
    assert out["fastqStorage"]["credentials"]["accessKeyId"] == "AKIA"
    assert out["sampleStorage"]["basePath"] == "/work/archive"


def test_disable_archive_omits_sample_storage(tmp_path) -> None:
    out = apply_storage_to_planner_payload(
        None,
        {"disableArchive": True, "sampleStorage": {"type": "file", "basePath": "/nope"}},
        store=_store(tmp_path),
    )
    assert "sampleStorage" not in out
    assert out["fastqStorage"]["bucket"] == "reads-bucket"


def test_missing_profile_names_the_key(tmp_path) -> None:
    with pytest.raises(KeyError, match="storage_profile not found: missing"):
        apply_storage_to_planner_payload(
            None,
            {"archiveProfileKey": "missing"},
            store=_store(tmp_path),
        )


def test_missing_endpoint_is_not_reported_as_a_missing_profile(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert(
        "storage_profile",
        "broken",
        {"fastqStorageEndpoint": "does-not-exist"},
        status="published",
    )
    with pytest.raises(KeyError, match="storage_endpoint not found: does-not-exist"):
        apply_storage_to_planner_payload(
            None,
            {"archiveProfileKey": "broken"},
            store=store,
        )


def test_missing_credential_is_not_reported_as_a_missing_profile(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert(
        "storage_endpoint",
        "orphan",
        {
            "type": "s3",
            "bucket": "orphan-bucket",
            "region": "us-east-1",
            "credentialName": "missing-cred",
        },
        status="published",
        extra={"provider": "s3", "credentialName": "missing-cred"},
    )
    store.upsert(
        "storage_profile",
        "broken-cred",
        {"fastqStorageEndpoint": "orphan"},
        status="published",
    )
    with pytest.raises(KeyError, match="credential not found: missing-cred"):
        apply_storage_to_planner_payload(
            None,
            {"archiveProfileKey": "broken-cred"},
            store=store,
        )
