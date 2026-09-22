"""Tests for hardened cloud transfer + storage credential refs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from methyl_domain.storage_secrets import (
    apply_node_credential_cache,
    read_node_credential_cache,
    resolve_secret_payload,
    write_encrypted_secret_file,
    write_node_credential_cache,
)
from methyl_domain.storage_transfer_config import StorageTransferStepConfig
from methyl_worker.cloud_transfer import (
    TransferSettings,
    download_to_path_safe,
    should_skip_s3_upload,
)


def test_storage_transfer_config_defaults_none() -> None:
    cfg = StorageTransferStepConfig()
    assert cfg.max_concurrency is None
    assert cfg.multipart_chunksize_mb is None


def test_transfer_settings_from_mapping() -> None:
    settings = TransferSettings.from_config(
        {"max_concurrency": 8, "multipart_chunksize_mb": 16}
    )
    assert settings.max_concurrency == 8
    assert settings.multipart_chunksize_mb == 16


def test_download_to_path_safe_atomic(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"

    def write(partial: Path) -> None:
        partial.write_bytes(b"ok")

    download_to_path_safe(write, target)
    assert target.read_bytes() == b"ok"
    assert not list(tmp_path.glob("*.partial"))


def test_should_skip_s3_multipart_etag_without_checksum_does_not_skip(
    tmp_path: Path,
) -> None:
    local = tmp_path / "f.bin"
    local.write_bytes(b"abcdef")
    client = MagicMock()
    client.head_object.return_value = {
        "ContentLength": 6,
        "ETag": '"abc123-3"',  # multipart style
    }
    assert should_skip_s3_upload(client, bucket="b", key="k", local=local) is False


def test_should_skip_s3_single_part_etag_md5(tmp_path: Path) -> None:
    import hashlib

    local = tmp_path / "f.bin"
    payload = b"abcdef"
    local.write_bytes(payload)
    etag = hashlib.md5(payload).hexdigest()
    client = MagicMock()
    client.head_object.return_value = {
        "ContentLength": len(payload),
        "ETag": f'"{etag}"',
    }
    assert should_skip_s3_upload(client, bucket="b", key="k", local=local) is True


def test_encrypted_file_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cred.encrypted"
    payload = json.dumps(
        {
            "authMode": "explicit_keys",
            "accessKeyId": "AKIATEST",
            "secretAccessKey": "secret",
        }
    )
    write_encrypted_secret_file(path, payload)
    resolved = resolve_secret_payload(
        {"authMode": "encrypted_file", "path": str(path)}
    )
    assert resolved["authMode"] == "explicit_keys"
    assert resolved["accessKeyId"] == "AKIATEST"
    assert resolved["secretAccessKey"] == "secret"


def test_resolve_vault_ref_mocked() -> None:
    secret = json.dumps(
        {
            "authMode": "explicit_keys",
            "accessKeyId": "AKIA",
            "secretAccessKey": "sec",
        }
    )
    with patch(
        "methyl_domain.storage_secrets.read_key_vault_secret", return_value=secret
    ):
        resolved = resolve_secret_payload(
            {
                "authMode": "azure_key_vault",
                "vaultUrl": "https://kv.vault.azure.net/",
                "secretName": "myqnap-keys",
            }
        )
    assert resolved["accessKeyId"] == "AKIA"


def test_connection_string_with_port_not_parsed_as_s3_keys() -> None:
    """Azure CS with host:port must not hit accessKeyId:secret colon split."""
    from methyl_domain.storage_secrets import _payload_from_vault_or_file_value

    raw = (
        "DefaultEndpointsProtocol=https;AccountName=acct;"
        "AccountKey=abc/+/=;BlobEndpoint=https://host:8443/account;"
    )
    resolved = _payload_from_vault_or_file_value(raw, {"authMode": "azure_key_vault"})
    assert resolved["authMode"] == "connection_string"
    assert resolved["connectionString"] == raw
    assert "accessKeyId" not in resolved


def test_plain_s3_access_key_pair_still_parsed() -> None:
    from methyl_domain.storage_secrets import _payload_from_vault_or_file_value

    resolved = _payload_from_vault_or_file_value(
        "AKIATEST:secret/with+chars=",
        {"authMode": "azure_key_vault"},
    )
    assert resolved == {
        "authMode": "explicit_keys",
        "accessKeyId": "AKIATEST",
        "secretAccessKey": "secret/with+chars=",
    }


def test_s3_transfer_config_uses_resolved_knobs() -> None:
    from methyl_worker.cloud_transfer import _s3_transfer_config

    settings = TransferSettings.from_config(
        {"max_concurrency": 12, "multipart_chunksize_mb": 8, "multipart_threshold_mb": 16}
    )
    cfg = _s3_transfer_config(settings)
    assert cfg.max_concurrency == 12
    assert cfg.multipart_chunksize == 8 * 1024 * 1024
    assert cfg.multipart_threshold == 16 * 1024 * 1024


def test_azure_download_passes_max_concurrency(tmp_path: Path) -> None:
    from methyl_worker.cloud_transfer import azure_download_blob

    target = tmp_path / "blob.bin"
    downloader = MagicMock()
    downloader.readinto.side_effect = lambda handle: handle.write(b"data") or 4
    blob = MagicMock()
    blob.download_blob.return_value = downloader

    azure_download_blob(
        blob, target, settings=TransferSettings(max_concurrency=6)
    )
    blob.download_blob.assert_called_once_with(max_concurrency=6)
    assert target.read_bytes() == b"data"


def test_should_skip_s3_multipart_with_metadata_md5(tmp_path: Path) -> None:
    local = tmp_path / "f.bin"
    payload = b"multipart-payload"
    local.write_bytes(payload)
    md5 = __import__("hashlib").md5(payload).hexdigest()
    client = MagicMock()
    client.head_object.return_value = {
        "ContentLength": len(payload),
        "ETag": '"deadbeef-4"',
        "Metadata": {"md5": md5},
    }
    assert should_skip_s3_upload(client, bucket="b", key="k", local=local) is True


def test_node_credential_cache_updates_on_hash_change(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_STORAGE_CREDENTIAL_PASSWORD", "test-wrap-key")
    monkeypatch.delenv("METHYL_WORK_ROOT", raising=False)
    cache_dir = tmp_path / "cred-cache"
    creds_v1 = {
        "authMode": "explicit_keys",
        "accessKeyId": "AKIA1",
        "secretAccessKey": "sec1",
        "credentialName": "lab-aws-keys",
        "contentHash": "hash-one",
    }
    apply_node_credential_cache(creds_v1, cache_dir=cache_dir)
    cached = read_node_credential_cache("lab-aws-keys", cache_dir=cache_dir)
    assert cached is not None
    assert cached[0] == "hash-one"
    assert cached[1]["accessKeyId"] == "AKIA1"

    creds_v2 = {**creds_v1, "accessKeyId": "AKIA2", "contentHash": "hash-two"}
    apply_node_credential_cache(creds_v2, cache_dir=cache_dir)
    cached2 = read_node_credential_cache("lab-aws-keys", cache_dir=cache_dir)
    assert cached2 is not None
    assert cached2[0] == "hash-two"
    assert cached2[1]["accessKeyId"] == "AKIA2"


def test_node_credential_cache_refuses_shared_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_STORAGE_CREDENTIAL_PASSWORD", "test-wrap-key")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("METHYL_WORK_ROOT", str(work))
    with pytest.raises(RuntimeError, match="shared work root"):
        write_node_credential_cache(
            "x",
            "h",
            {"authMode": "explicit_keys", "accessKeyId": "a", "secretAccessKey": "b"},
            cache_dir=work / "secrets",
        )


def test_resolve_secret_payload_refreshes_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_STORAGE_CREDENTIAL_PASSWORD", "test-wrap-key")
    cache_dir = tmp_path / "c"
    monkeypatch.setattr(
        "methyl_domain.storage_secrets.DEFAULT_NODE_CACHE_DIR",
        cache_dir,
    )
    resolved = resolve_secret_payload(
        {
            "authMode": "explicit_keys",
            "accessKeyId": "AKIA",
            "secretAccessKey": "sec",
            "credentialName": "goliath-archive-keys",
            "contentHash": "deadbeef",
        }
    )
    assert resolved["accessKeyId"] == "AKIA"
    assert read_node_credential_cache("goliath-archive-keys", cache_dir=cache_dir)[0] == "deadbeef"
