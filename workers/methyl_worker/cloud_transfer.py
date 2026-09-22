"""High-performance S3 / Azure Blob transfers with retries and strong skip logic."""

from __future__ import annotations

import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from methyl_domain.storage_secrets import resolve_secret_payload
from methyl_domain.storage_transfer_config import (
    StorageTransferStepConfig,
    transfer_config_from_mapping,
)

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024


@dataclass(frozen=True)
class TransferSettings:
    max_concurrency: Optional[int] = None
    multipart_threshold_mb: Optional[int] = None
    multipart_chunksize_mb: Optional[int] = None
    max_attempts: Optional[int] = None
    mtime_tolerance_s: float = 1.0

    @classmethod
    def from_config(
        cls, cfg: StorageTransferStepConfig | Mapping[str, Any] | None
    ) -> TransferSettings:
        model = transfer_config_from_mapping(cfg)
        return cls(
            max_concurrency=model.max_concurrency,
            multipart_threshold_mb=model.multipart_threshold_mb,
            multipart_chunksize_mb=model.multipart_chunksize_mb,
            max_attempts=model.max_attempts,
            mtime_tolerance_s=(
                float(model.mtime_tolerance_s)
                if model.mtime_tolerance_s is not None
                else 1.0
            ),
        )


def file_md5_hex(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _s3_transfer_config(settings: TransferSettings):
    from boto3.s3.transfer import TransferConfig

    kwargs: dict[str, Any] = {}
    if settings.max_concurrency is not None:
        kwargs["max_concurrency"] = settings.max_concurrency
    if settings.multipart_threshold_mb is not None:
        kwargs["multipart_threshold"] = settings.multipart_threshold_mb * _MIB
    if settings.multipart_chunksize_mb is not None:
        kwargs["multipart_chunksize"] = settings.multipart_chunksize_mb * _MIB
    return TransferConfig(**kwargs) if kwargs else TransferConfig()


def _botocore_config(settings: TransferSettings):
    from botocore.config import Config

    retries: dict[str, Any] = {"mode": "adaptive"}
    if settings.max_attempts is not None:
        retries["max_attempts"] = settings.max_attempts
    return Config(retries=retries)


def build_s3_client(
    *,
    credentials: Mapping[str, Any],
    region: str | None = None,
    endpoint_url: str | None = None,
    settings: TransferSettings | None = None,
):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for s3 transfers; install methyl-worker cloud deps"
        ) from exc

    settings = settings or TransferSettings()
    resolved = resolve_secret_payload(dict(credentials))
    client_kwargs: dict[str, Any] = {"config": _botocore_config(settings)}
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url
    if region:
        client_kwargs["region_name"] = region
    auth = str(resolved.get("authMode") or "")
    if auth == "explicit_keys":
        token = resolved.get("sessionToken")
        if hasattr(token, "get_secret_value"):
            token = token.get_secret_value()
        secret = resolved.get("secretAccessKey")
        if hasattr(secret, "get_secret_value"):
            secret = secret.get_secret_value()
        return boto3.client(
            "s3",
            aws_access_key_id=resolved["accessKeyId"],
            aws_secret_access_key=secret,
            aws_session_token=token,
            **client_kwargs,
        )
    if auth in ("instance_profile", ""):
        return boto3.client("s3", **client_kwargs)
    raise RuntimeError(f"Unsupported S3 authMode after resolve: {auth!r}")


def build_azure_blob_service(
    *,
    account: str,
    credentials: Mapping[str, Any],
    settings: TransferSettings | None = None,
):
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError(
            "azure-identity and azure-storage-blob are required for azure_blob"
        ) from exc

    settings = settings or TransferSettings()
    resolved = resolve_secret_payload(dict(credentials))
    auth = str(resolved.get("authMode") or "")

    if auth == "connection_string":
        cs = resolved.get("connectionString")
        if hasattr(cs, "get_secret_value"):
            cs = cs.get_secret_value()
        return BlobServiceClient.from_connection_string(cs)
    if auth == "account_key":
        key = resolved.get("accountKey")
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        account_url = f"https://{account}.blob.core.windows.net"
        return BlobServiceClient(account_url, credential=key)
    if auth == "default_credential":
        account_url = f"https://{account}.blob.core.windows.net"
        return BlobServiceClient(account_url, credential=DefaultAzureCredential())
    raise RuntimeError(f"Unsupported Azure authMode after resolve: {auth!r}")


def download_to_path_safe(
    download_fn: Callable[[Path], None],
    target: Path,
) -> None:
    """Write via ``*.partial`` then atomic replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        download_fn(partial)
        os.replace(partial, target)
    except Exception:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        raise


def s3_download_file(
    client: Any,
    *,
    bucket: str,
    key: str,
    target: Path,
    settings: TransferSettings | None = None,
) -> None:
    settings = settings or TransferSettings()
    config = _s3_transfer_config(settings)

    def _do(partial: Path) -> None:
        client.download_file(bucket, key, str(partial), Config=config)

    download_to_path_safe(_do, target)


def s3_upload_file(
    client: Any,
    *,
    bucket: str,
    key: str,
    local: Path,
    settings: TransferSettings | None = None,
    extra_args: Mapping[str, Any] | None = None,
) -> None:
    settings = settings or TransferSettings()
    config = _s3_transfer_config(settings)
    client.upload_file(
        str(local),
        bucket,
        key,
        ExtraArgs=dict(extra_args) if extra_args else None,
        Config=config,
    )


def should_skip_s3_upload(
    client: Any,
    *,
    bucket: str,
    key: str,
    local: Path,
    local_md5: str | None = None,
) -> bool:
    """Skip when remote size matches and content is unchanged.

    Single-part ETag (32 hex) may equal MD5. Multipart ETags (``…-<n>``) are
    never treated as MD5; compare size + optional SHA256 checksum / metadata.
    """
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception:
        return False
    if int(head["ContentLength"]) != local.stat().st_size:
        return False
    etag = str(head.get("ETag", "")).strip('"')
    md5 = local_md5 if local_md5 is not None else file_md5_hex(local)
    if len(etag) == 32 and "-" not in etag:
        return etag.lower() == md5.lower()
    # Multipart or opaque ETag — prefer checksum headers / metadata
    checksum = head.get("ChecksumSHA256")
    if checksum:
        import base64

        digest = hashlib.sha256()
        with local.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        local_b64 = base64.b64encode(digest.digest()).decode("ascii")
        return checksum.rstrip("=") == local_b64.rstrip("=")
    meta = head.get("Metadata") or {}
    remote_md5 = meta.get("md5") or meta.get("content-md5")
    if remote_md5:
        return remote_md5.lower() == md5.lower()
    # Size match alone is insufficient for multipart — do not skip
    return False


def should_skip_azure_upload(blob_client: Any, local: Path, local_md5: str | None = None) -> bool:
    try:
        props = blob_client.get_blob_properties()
    except Exception:
        return False
    if int(props.size) != local.stat().st_size:
        return False
    content_settings = getattr(props, "content_settings", None)
    content_md5 = getattr(content_settings, "content_md5", None) if content_settings else None
    if content_md5:
        import base64

        md5 = local_md5 if local_md5 is not None else file_md5_hex(local)
        remote_hex = content_md5.hex() if isinstance(content_md5, (bytes, bytearray)) else None
        if remote_hex is None:
            try:
                remote_hex = base64.b64decode(content_md5).hex()
            except Exception:
                return False
        return remote_hex.lower() == md5.lower()
    # Size-only is weak; still allow skip when sizes match for idempotent re-runs
    # of small unchanged artifacts (QC JSON). Callers may force upload.
    return True


def azure_download_blob(
    blob_client: Any,
    target: Path,
    *,
    settings: TransferSettings | None = None,
) -> None:
    settings = settings or TransferSettings()
    max_concurrency = settings.max_concurrency

    def _do(partial: Path) -> None:
        kwargs: dict[str, Any] = {}
        if max_concurrency is not None:
            kwargs["max_concurrency"] = max_concurrency
        downloader = blob_client.download_blob(**kwargs)
        with open(partial, "wb") as handle:
            downloader.readinto(handle)

    download_to_path_safe(_do, target)


def azure_upload_blob(
    blob_client: Any,
    local: Path,
    *,
    settings: TransferSettings | None = None,
    content_md5: bytes | None = None,
) -> None:
    settings = settings or TransferSettings()
    kwargs: dict[str, Any] = {"overwrite": True}
    if settings.max_concurrency is not None:
        kwargs["max_concurrency"] = settings.max_concurrency
    if content_md5 is not None:
        try:
            from azure.storage.blob import ContentSettings

            kwargs["content_settings"] = ContentSettings(content_md5=content_md5)
        except Exception:
            logger.debug("Could not set Azure content_md5", exc_info=True)
    with open(local, "rb") as handle:
        blob_client.upload_blob(handle, **kwargs)


def should_skip_download(
    local: Path,
    remote_size: int,
    remote_mtime: float,
    *,
    mtime_tolerance_s: float = 1.0,
) -> bool:
    if not local.is_file():
        return False
    stat = local.stat()
    if stat.st_size != remote_size:
        return False
    return abs(stat.st_mtime - remote_mtime) <= mtime_tolerance_s


def run_parallel(
    items: Sequence[Any],
    worker: Callable[[Any], Any],
    *,
    max_workers: Optional[int],
) -> List[Any]:
    """Run ``worker`` over ``items`` with a bounded thread pool (or serially)."""
    if not items:
        return []
    workers = max_workers if max_workers and max_workers > 1 else 1
    if workers == 1 or len(items) == 1:
        return [worker(item) for item in items]
    results: List[Any] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        futures = {pool.submit(worker, item): idx for idx, item in enumerate(items)}
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
    return results


def credentials_mapping(model: Any) -> dict[str, Any]:
    """Dump a Pydantic credentials model (or dict) to a plain mapping."""
    if model is None:
        return {}
    if isinstance(model, Mapping):
        return dict(model)
    if hasattr(model, "model_dump"):
        from methyl_domain.fastq_storage import reveal_secrets

        return reveal_secrets(model.model_dump(mode="python"))
    return dict(model)
