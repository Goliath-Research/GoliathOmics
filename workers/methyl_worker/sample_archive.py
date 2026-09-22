"""Upload sample artifacts (FASTQs, QC JSON, HDF5) to durable sample storage."""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from methyl_domain.sample_storage import (
    AzureSampleDestination,
    FileSampleDestination,
    SampleDestinationLocation,
    S3SampleDestination,
    resolve_file_local_root,
)
from methyl_worker.cloud_transfer import (
    TransferSettings,
    azure_upload_blob,
    build_azure_blob_service,
    build_s3_client,
    credentials_mapping,
    file_md5_hex,
    s3_upload_file,
    should_skip_azure_upload,
    should_skip_s3_upload,
)

logger = logging.getLogger(__name__)

H5_SUFFIX = ".h5"
FASTQ_GLOBS = ("*.fastq.gz", "*.fq.gz", "*.fastq", "*.fq")
RAW_METRICS_SUFFIXES = (".pb_metrics.json",)


@dataclass(frozen=True)
class UploadTarget:
    scheme: str
    bucket: str = ""
    container: str = ""
    account: str = ""
    prefix: str = ""
    local_root: Path | None = None
    s3_dest: S3SampleDestination | None = None
    azure_dest: AzureSampleDestination | None = None


@dataclass
class _CloudClients:
    s3: Any = field(default=None, repr=False)
    azure: Any = field(default=None, repr=False)
    settings: TransferSettings = field(default_factory=TransferSettings)


def _remote_key(prefix: str, relative_path: str) -> str:
    norm = prefix.rstrip("/")
    rel = relative_path.lstrip("/")
    if not norm:
        return rel
    return f"{norm}/{rel}"


def _file_md5(path: Path) -> str:
    return file_md5_hex(path)


def _should_skip_s3(client: Any, bucket: str, key: str, local: Path) -> bool:
    return should_skip_s3_upload(client, bucket=bucket, key=key, local=local)


def _should_skip_azure(blob_client: Any, local: Path) -> bool:
    return should_skip_azure_upload(blob_client, local)


def _ensure_s3(clients: _CloudClients, dest: S3SampleDestination) -> Any:
    if clients.s3 is None:
        clients.s3 = build_s3_client(
            credentials=credentials_mapping(dest.credentials),
            region=dest.region,
            endpoint_url=dest.endpointUrl,
            settings=clients.settings,
        )
    return clients.s3


def _ensure_azure(clients: _CloudClients, dest: AzureSampleDestination) -> Any:
    if clients.azure is None:
        clients.azure = build_azure_blob_service(
            account=dest.account,
            credentials=credentials_mapping(dest.credentials),
            settings=clients.settings,
        )
    return clients.azure


def _target_from_model(dest: SampleDestinationLocation) -> UploadTarget:
    if isinstance(dest, FileSampleDestination):
        return UploadTarget(
            scheme="file",
            prefix=dest.prefix,
            local_root=resolve_file_local_root(dest),
        )
    if isinstance(dest, S3SampleDestination):
        return UploadTarget(
            scheme="s3",
            bucket=dest.bucket,
            prefix=dest.prefix,
            s3_dest=dest,
        )
    if isinstance(dest, AzureSampleDestination):
        return UploadTarget(
            scheme="az",
            account=dest.account,
            container=dest.container,
            prefix=dest.prefix,
            azure_dest=dest,
        )
    raise RuntimeError(f"Unsupported sample destination: {type(dest)!r}")


def _upload_one(
    local: Path,
    relative_path: str,
    target: UploadTarget,
    clients: _CloudClients,
    *,
    local_md5: str | None = None,
) -> Tuple[bool, str]:
    """Upload file; return (uploaded?, md5_hex)."""
    key = _remote_key(target.prefix, relative_path)
    md5 = local_md5 if local_md5 is not None else _file_md5(local)
    if target.scheme == "file":
        root = target.local_root
        if root is None:
            raise RuntimeError("file sampleDestination missing local_root")
        dest_path = root / relative_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_path.is_file() and dest_path.stat().st_size == local.stat().st_size:
            return False, md5
        shutil.copy2(local, dest_path)
        return True, md5

    if target.scheme == "s3":
        assert target.s3_dest is not None
        client = _ensure_s3(clients, target.s3_dest)
        if should_skip_s3_upload(
            client, bucket=target.bucket, key=key, local=local, local_md5=md5
        ):
            logger.info("Skipping unchanged s3://%s/%s", target.bucket, key)
            return False, md5
        s3_upload_file(
            client,
            bucket=target.bucket,
            key=key,
            local=local,
            settings=clients.settings,
            extra_args={"Metadata": {"md5": md5}},
        )
        return True, md5

    if target.scheme == "az":
        assert target.azure_dest is not None
        service = _ensure_azure(clients, target.azure_dest)
        blob_client = service.get_blob_client(container=target.container, blob=key)
        if should_skip_azure_upload(blob_client, local, local_md5=md5):
            logger.info("Skipping unchanged %s/%s", target.container, key)
            return False, md5
        azure_upload_blob(
            blob_client,
            local,
            settings=clients.settings,
            content_md5=bytes.fromhex(md5),
        )
        return True, md5

    raise RuntimeError(f"Unsupported destination scheme {target.scheme!r}")


def _collect_fastqs(sample_dir: Path, sample_root: Optional[Path] = None) -> List[Path]:
    paths: List[Path] = []
    seen: set[str] = set()
    bases: List[Path] = []
    if sample_root is not None:
        bases.append(Path(sample_root))
    bases.append(Path(sample_dir))
    for base in bases:
        if not base.is_dir():
            continue
        key = str(base.resolve()) if base.exists() else str(base)
        if key in seen:
            continue
        seen.add(key)
        for pattern in FASTQ_GLOBS:
            paths.extend(sorted(base.glob(pattern)))
    trimmed = [p for p in paths if ".trimmed." in p.name]
    if trimmed:
        return trimmed
    # Prefer unique basenames; arm-leaf copies (hardlinks) after root originals.
    by_name: dict[str, Path] = {}
    for path in paths:
        by_name.setdefault(path.name, path)
    return list(by_name.values())


def _resolve_alignment_qc_path(
    sample_dir: Path,
    sample_id: str,
    alignment_qc_path: Optional[str | Path],
    project_path: Optional[str | Path],
) -> Optional[Path]:
    if alignment_qc_path:
        path = Path(str(alignment_qc_path))
        if path.is_file():
            return path
    candidate = sample_dir / f"{sample_id}.json"
    if candidate.is_file():
        return candidate
    if project_path:
        try:
            from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config

            cfg = resolve_alignment_qc_config(str(project_path))
            out = Path(cfg.output_dir) / f"{sample_id}.json"
            if out.is_file():
                return out
        except Exception:
            logger.debug("Could not resolve alignment QC from project", exc_info=True)
    return None


def _artifact_entries(
    *,
    sample_dir: Path,
    sample_id: str,
    mode: str,
    alignment_qc_path: Optional[str | Path],
    project_path: Optional[str | Path],
    sample_root: Optional[Path] = None,
) -> List[Tuple[Path, str]]:
    """Return (local_path, archive_relative_path) pairs."""
    entries: List[Tuple[Path, str]] = []

    align = _resolve_alignment_qc_path(sample_dir, sample_id, alignment_qc_path, project_path)
    if align is not None:
        entries.append((align, "qc/alignment.json"))

    extraction_qc = sample_dir / f"{sample_id}.extraction_qc.json"
    if extraction_qc.is_file():
        entries.append((extraction_qc, "qc/extraction_qc.json"))

    manifest = sample_dir / f"{sample_id}.extraction_manifest.json"
    if manifest.is_file() and mode == "full":
        entries.append((manifest, "qc/extraction_manifest.json"))

    log_path = sample_dir / f"{sample_id}.sample_prep_log.jsonl"
    if log_path.is_file():
        entries.append((log_path, "qc/sample_prep_log.jsonl"))

    if mode == "full":
        for fq in _collect_fastqs(sample_dir, sample_root=sample_root):
            entries.append((fq, f"fastq/{fq.name}"))
        for h5 in sorted(sample_dir.glob(f"*{H5_SUFFIX}")):
            entries.append((h5, f"h5/{h5.name}"))

    # methylGrapher WGBS provenance (align + extract) — archive when present.
    for name, rel in (
        (f"{sample_id}.alignment.gaf", f"pangenome/{sample_id}.alignment.gaf"),
        (f"{sample_id}.conversion_report.txt", f"pangenome/{sample_id}.conversion_report.txt"),
        (f"{sample_id}.methylgrapher_align.log", f"pangenome/{sample_id}.methylgrapher_align.log"),
        (f"{sample_id}.methylgrapher_extract.log", f"pangenome/{sample_id}.methylgrapher_extract.log"),
        (f"{sample_id}.alignment_metrics.json", f"pangenome/{sample_id}.alignment_metrics.json"),
        (f"{sample_id}.deduplicate_metrics.txt", f"qc/{sample_id}.deduplicate_metrics.txt"),
        (f"{sample_id}.qc-metrics.tar", f"qc/{sample_id}.qc-metrics.tar"),
    ):
        path = sample_dir / name
        if path.is_file():
            entries.append((path, rel))

    return entries


def _settings_from_resolved(resolved_config: Mapping[str, Any] | None) -> TransferSettings:
    if not resolved_config:
        return TransferSettings()
    slice_ = resolved_config.get("storage_transfer") or resolved_config.get(
        "storageTransfer"
    )
    return TransferSettings.from_config(slice_)


def archive_sample(
    *,
    sample_dir: str | Path,
    sample_id: str,
    sample_destination: SampleDestinationLocation,
    mode: str = "full",
    reject_reason: Optional[str] = None,
    alignment_qc_path: Optional[str | Path] = None,
    project_path: Optional[str | Path] = None,
    resolved_config: Mapping[str, Any] | None = None,
    sample_root: str | Path | None = None,
) -> dict[str, Any]:
    if mode not in {"full", "qc_only"}:
        raise RuntimeError(f"archive mode must be 'full' or 'qc_only', got {mode!r}")
    if mode == "qc_only" and not reject_reason:
        reject_reason = "qc_failed"

    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")
    root_path = Path(sample_root) if sample_root else None

    settings = _settings_from_resolved(resolved_config)
    target = _target_from_model(sample_destination)
    clients = _CloudClients(settings=settings)
    uploaded: List[str] = []
    skipped: List[str] = []
    manifest_files: List[dict[str, Any]] = []

    entries: List[Tuple[Path, str]] = []
    for local, rel in _artifact_entries(
        sample_dir=sample_path,
        sample_id=sample_id,
        mode=mode,
        alignment_qc_path=alignment_qc_path,
        project_path=project_path,
        sample_root=root_path,
    ):
        if not local.is_file():
            continue
        if local.suffix == ".json" and any(local.name.endswith(s) for s in RAW_METRICS_SUFFIXES):
            continue
        entries.append((local, rel))

    def _do_one(item: Tuple[Path, str]) -> Tuple[str, bool, str, int]:
        local, rel = item
        did_upload, md5 = _upload_one(local, rel, target, clients)
        return rel, did_upload, md5, local.stat().st_size

    max_workers = settings.max_concurrency if settings.max_concurrency else 1
    if max_workers > 1 and len(entries) > 1:
        results: List[Tuple[str, bool, str, int]] = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as pool:
            futs = [pool.submit(_do_one, e) for e in entries]
            for fut in as_completed(futs):
                results.append(fut.result())
        # Stable order by relative path
        results.sort(key=lambda r: r[0])
    else:
        results = [_do_one(e) for e in entries]

    for rel, did_upload, md5, size in results:
        manifest_files.append({"path": rel, "size": size, "md5": md5})
        if did_upload:
            uploaded.append(rel)
        else:
            skipped.append(rel)

    archive_manifest = {
        "schema_name": "methylpipeline.sample_archive",
        "schema_version": "1.0.0",
        "sample_id": sample_id,
        "archive_mode": mode,
        "reject_reason": reject_reason if mode == "qc_only" else None,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "remote_prefix": target.prefix,
        "files": manifest_files,
    }

    manifest_local = sample_path / "archive_manifest.json"
    manifest_local.write_text(json.dumps(archive_manifest, indent=2), encoding="utf-8")
    _upload_one(manifest_local, "archive_manifest.json", target, clients)
    uploaded.append("archive_manifest.json")

    return {
        "sampleId": sample_id,
        "archiveMode": mode,
        "rejectReason": reject_reason,
        "uploadedFiles": uploaded,
        "skippedFiles": skipped,
        "remotePrefix": target.prefix,
        "uploadedCount": len(uploaded),
        "skippedCount": len(skipped),
        "sampleArchived": True,
        "archiveSkipped": False,
        "skipReason": None,
        "missingConfiguration": [],
        "archiveManifestPath": str(manifest_local),
    }


def _resolve_destination(input_json: Mapping[str, Any]) -> SampleDestinationLocation | None:
    from pydantic import TypeAdapter

    adapter = TypeAdapter(SampleDestinationLocation)
    raw = input_json.get("sampleDestination") or input_json.get("h5Destination")
    if raw is None:
        return None
    return adapter.validate_python(raw)


def _archive_skip_no_destination(
    *,
    sample_id: str,
    mode: str,
    reject_reason: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "sampleId": sample_id,
        "archiveMode": mode,
        "rejectReason": reject_reason,
        "uploadedFiles": [],
        "skippedFiles": [],
        "remotePrefix": "",
        "uploadedCount": 0,
        "skippedCount": 0,
        "sampleArchived": False,
        "archiveSkipped": True,
        "skipReason": "sample_destination_not_configured",
        "missingConfiguration": ["sampleDestination"],
        "archiveManifestPath": None,
    }


def archive_from_task_input(input_json: Mapping[str, Any]) -> dict[str, Any]:
    sample_dir = input_json.get("sampleDir")
    if not sample_dir:
        raise RuntimeError("sample.archive_sample requires sampleDir")
    sample_id = str(input_json.get("sampleId") or Path(str(sample_dir)).name)
    mode = str(input_json.get("mode") or input_json.get("archiveMode") or "full")
    destination = _resolve_destination(input_json)
    if destination is None:
        return _archive_skip_no_destination(
            sample_id=sample_id,
            mode=mode,
            reject_reason=input_json.get("rejectReason"),
        )
    resolved = input_json.get("resolvedConfig")
    return archive_sample(
        sample_dir=str(sample_dir),
        sample_id=sample_id,
        sample_destination=destination,
        mode=mode,
        reject_reason=input_json.get("rejectReason"),
        alignment_qc_path=input_json.get("alignmentQcPath") or input_json.get("qcPath"),
        project_path=input_json.get("projectPath") or input_json.get("project"),
        resolved_config=resolved if isinstance(resolved, Mapping) else None,
        sample_root=input_json.get("sampleRoot"),
    )


# Backward-compatible H5-only upload (flat keys under prefix; does not archive FASTQs/QC).
def _local_h5_files(sample_dir: Path, h5_files: Sequence[str] | None) -> List[Path]:
    if h5_files:
        paths = [sample_dir / name for name in h5_files]
    else:
        paths = sorted(sample_dir.glob(f"*{H5_SUFFIX}"))
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise RuntimeError(f"HDF5 files not found: {', '.join(str(p) for p in missing)}")
    return paths


def upload_h5_files(
    *,
    sample_dir: str | Path,
    h5_destination: SampleDestinationLocation,
    h5_files: Sequence[str] | None = None,
    resolved_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload only HDF5 files to the destination prefix (legacy flat layout)."""
    sample_path = Path(sample_dir)
    local_files = _local_h5_files(sample_path, h5_files)
    settings = _settings_from_resolved(resolved_config)
    target = _target_from_model(h5_destination)
    clients = _CloudClients(settings=settings)
    uploaded: List[str] = []
    skipped: List[str] = []

    for local in local_files:
        rel = local.name
        did_upload, _ = _upload_one(local, rel, target, clients)
        if did_upload:
            uploaded.append(rel)
        else:
            skipped.append(rel)

    return {
        "uploadedFiles": uploaded,
        "skippedFiles": skipped,
        "remotePrefix": target.prefix,
        "uploadedCount": len(uploaded),
        "skippedCount": len(skipped),
    }


def upload_h5_from_task_input(input_json: Mapping[str, Any]) -> dict[str, Any]:
    sample_dir = input_json.get("sampleDir")
    if not sample_dir:
        raise RuntimeError("sample.upload_h5 requires sampleDir")
    destination = _resolve_destination(input_json)
    if destination is None:
        raise RuntimeError("sample.upload_h5 requires sampleDestination")
    h5_files = input_json.get("h5Files")
    if isinstance(h5_files, str):
        h5_files = [h5_files]
    resolved = input_json.get("resolvedConfig")
    result = upload_h5_files(
        sample_dir=str(sample_dir),
        h5_destination=destination,
        h5_files=h5_files,
        resolved_config=resolved if isinstance(resolved, Mapping) else None,
    )
    result["sampleId"] = input_json.get("sampleId") or Path(str(sample_dir)).name
    return result


def upload_from_task_input(input_json: Mapping[str, Any]) -> dict[str, Any]:
    """Deprecated alias — use ``upload_h5_from_task_input`` for H5-only uploads."""
    return upload_h5_from_task_input(input_json)
