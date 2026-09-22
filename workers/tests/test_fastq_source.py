"""Tests for cloud and local FASTQ ingest."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from methyl_domain.fastq_storage import (
    AzureFastqSource,
    FileFastqSource,
    S3ExplicitKeysCredentials,
    S3FastqSource,
)
from methyl_worker.fastq_source import download_from_source


def _write_fastq(path: Path, content: bytes = b"ACGT") -> None:
    path.write_bytes(content)
    os.utime(path, (1_700_000_000.0, 1_700_000_000.0))


def _file_source(base_path: Path, prefix: str = "") -> FileFastqSource:
    return FileFastqSource(basePath=str(base_path), prefix=prefix)


def _s3_source(prefix: str) -> S3FastqSource:
    return S3FastqSource(
        bucket="methyl-cohort",
        prefix=prefix,
        credentials=S3ExplicitKeysCredentials(
            accessKeyId="AKIA",
            secretAccessKey="secret",
        ),
    )


def _azure_source(prefix: str) -> AzureFastqSource:
    return AzureFastqSource(
        account="methylstore",
        container="plasma",
        prefix=prefix,
        credentials={"authMode": "default_credential"},
    )


def test_local_folder_downloads_all_fastq_gz(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    _write_fastq(src / "S1_1.fastq.gz", b"read1")
    _write_fastq(src / "S1_2.fastq.gz", b"read2")
    (src / "notes.txt").write_text("ignore")

    dest = tmp_path / "dest"
    files = download_from_source(_file_source(src), dest)

    assert sorted(Path(p).name for p in files) == ["S1_1.fastq.gz", "S1_2.fastq.gz"]
    assert (dest / "S1_1.fastq.gz").read_bytes() == b"read1"
    assert (dest / "S1_2.fastq.gz").read_bytes() == b"read2"


def test_local_download_skips_unchanged_size_and_mtime(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    fastq = src / "S1_1.fastq.gz"
    _write_fastq(fastq, b"same")

    dest = tmp_path / "dest"
    download_from_source(_file_source(src), dest)
    target = dest / "S1_1.fastq.gz"
    first_mtime = target.stat().st_mtime

    import time

    time.sleep(0.01)
    _write_fastq(fastq, b"same")
    download_from_source(_file_source(src), dest)

    assert target.read_bytes() == b"same"
    assert target.stat().st_mtime == first_mtime


def test_local_download_refreshes_when_size_changes(tmp_path: Path) -> None:
    src = tmp_path / "source"
    src.mkdir()
    fastq = src / "S1_1.fastq.gz"
    _write_fastq(fastq, b"old")

    dest = tmp_path / "dest"
    download_from_source(_file_source(src), dest)

    _write_fastq(fastq, b"new-content")
    download_from_source(_file_source(src), dest)
    assert (dest / "S1_1.fastq.gz").read_bytes() == b"new-content"


@patch("methyl_worker.fastq_source.build_s3_client")
def test_s3_folder_download(mock_s3_client: MagicMock, tmp_path: Path) -> None:
    client = MagicMock()
    mock_s3_client.return_value = client
    client.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "plasma/S1/S1_1.fastq.gz",
                    "Size": 4,
                    "LastModified": datetime(2024, 6, 1, tzinfo=timezone.utc),
                },
                {
                    "Key": "plasma/S1/S1_2.fastq.gz",
                    "Size": 4,
                    "LastModified": datetime(2024, 6, 1, tzinfo=timezone.utc),
                },
            ]
        }
    ]

    def fake_download(bucket: str, key: str, target: str, **kwargs) -> None:
        Path(target).write_bytes(key.encode())

    client.download_file.side_effect = fake_download

    dest = tmp_path / "sample"
    files = download_from_source(_s3_source("plasma/S1/"), dest)

    assert sorted(Path(p).name for p in files) == ["S1_1.fastq.gz", "S1_2.fastq.gz"]
    assert client.download_file.call_count == 2
    assert mock_s3_client.call_count == 1
    assert "Config" in client.download_file.call_args.kwargs


@patch("methyl_worker.fastq_source.build_s3_client")
def test_s3_preserves_subprefix_paths_when_basenames_collide(
    mock_s3_client: MagicMock, tmp_path: Path
) -> None:
    client = MagicMock()
    mock_s3_client.return_value = client
    client.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "plasma/S1/lane1/S1_1.fastq.gz",
                    "Size": 5,
                    "LastModified": datetime(2024, 6, 1, tzinfo=timezone.utc),
                },
                {
                    "Key": "plasma/S1/lane2/S1_1.fastq.gz",
                    "Size": 6,
                    "LastModified": datetime(2024, 6, 1, tzinfo=timezone.utc),
                },
            ]
        }
    ]

    def fake_download(bucket: str, key: str, target: str, **kwargs) -> None:
        Path(target).write_bytes(key.encode())

    client.download_file.side_effect = fake_download

    dest = tmp_path / "sample"
    files = download_from_source(_s3_source("plasma/S1/"), dest)

    assert sorted(files) == sorted(
        [
            str(dest / "lane1" / "S1_1.fastq.gz"),
            str(dest / "lane2" / "S1_1.fastq.gz"),
        ]
    )
    assert (dest / "lane1" / "S1_1.fastq.gz").read_bytes() == b"plasma/S1/lane1/S1_1.fastq.gz"
    assert (dest / "lane2" / "S1_1.fastq.gz").read_bytes() == b"plasma/S1/lane2/S1_1.fastq.gz"


@patch("methyl_worker.fastq_source.build_s3_client")
def test_s3_skips_download_when_size_and_mtime_match(mock_s3_client: MagicMock, tmp_path: Path) -> None:
    client = MagicMock()
    mock_s3_client.return_value = client
    last_modified = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    client.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "plasma/S1/S1_1.fastq.gz",
                    "Size": 5,
                    "LastModified": last_modified,
                }
            ]
        }
    ]

    dest = tmp_path / "sample"
    dest.mkdir()
    target = dest / "S1_1.fastq.gz"
    target.write_bytes(b"reads")
    os.utime(target, (last_modified.timestamp(), last_modified.timestamp()))

    files = download_from_source(_s3_source("plasma/S1/"), dest)

    assert files == [str(target)]
    client.download_file.assert_not_called()
    assert mock_s3_client.call_count == 1


@patch("methyl_worker.fastq_source.build_azure_blob_service")
def test_azure_folder_download(mock_azure_service: MagicMock, tmp_path: Path) -> None:
    service = MagicMock()
    mock_azure_service.return_value = service
    container = MagicMock()
    service.get_container_client.return_value = container

    blob1 = MagicMock()
    blob1.name = "plasma/S1/S1_1.fastq.gz"
    blob1.size = 6
    blob1.last_modified = datetime(2024, 6, 2, tzinfo=timezone.utc)
    blob2 = MagicMock()
    blob2.name = "plasma/S1/S1_2.fastq.gz"
    blob2.size = 6
    blob2.last_modified = datetime(2024, 6, 2, tzinfo=timezone.utc)
    container.list_blobs.return_value = [blob1, blob2]

    blob_client = MagicMock()

    def download_blob():
        payload = MagicMock()

        def readinto(handle):
            handle.write(b"ACGTAC")

        payload.readinto = readinto
        return payload

    blob_client.download_blob.side_effect = download_blob
    service.get_blob_client.return_value = blob_client

    dest = tmp_path / "sample"
    files = download_from_source(_azure_source("S1/"), dest)

    assert sorted(Path(p).name for p in files) == ["S1_1.fastq.gz", "S1_2.fastq.gz"]
    assert service.get_blob_client.call_count == 2
    mock_azure_service.assert_called_once()


@patch("methyl_worker.fastq_source.build_azure_blob_service")
def test_azure_preserves_subprefix_paths_when_basenames_collide(
    mock_azure_service: MagicMock, tmp_path: Path
) -> None:
    service = MagicMock()
    mock_azure_service.return_value = service
    container = MagicMock()
    service.get_container_client.return_value = container

    blob1 = MagicMock()
    blob1.name = "S1/lane1/S1_1.fastq.gz"
    blob1.size = 5
    blob1.last_modified = datetime(2024, 6, 2, tzinfo=timezone.utc)
    blob2 = MagicMock()
    blob2.name = "S1/lane2/S1_1.fastq.gz"
    blob2.size = 6
    blob2.last_modified = datetime(2024, 6, 2, tzinfo=timezone.utc)
    container.list_blobs.return_value = [blob1, blob2]

    def make_blob_client(container: str, blob: str) -> MagicMock:
        blob_client = MagicMock()

        def download_blob():
            payload = MagicMock()

            def readinto(handle):
                handle.write(blob.encode())

            payload.readinto = readinto
            return payload

        blob_client.download_blob.side_effect = download_blob
        return blob_client

    service.get_blob_client.side_effect = make_blob_client

    dest = tmp_path / "sample"
    files = download_from_source(_azure_source("S1/"), dest)

    assert sorted(files) == sorted(
        [
            str(dest / "lane1" / "S1_1.fastq.gz"),
            str(dest / "lane2" / "S1_1.fastq.gz"),
        ]
    )
    assert (dest / "lane1" / "S1_1.fastq.gz").read_bytes() == b"S1/lane1/S1_1.fastq.gz"
    assert (dest / "lane2" / "S1_1.fastq.gz").read_bytes() == b"S1/lane2/S1_1.fastq.gz"
    mock_azure_service.assert_called_once()


@patch("methyl_worker.fastq_source.build_azure_blob_service")
def test_azure_empty_prefix_lists_container(mock_azure_service: MagicMock, tmp_path: Path) -> None:
    service = MagicMock()
    mock_azure_service.return_value = service
    container = MagicMock()
    service.get_container_client.return_value = container
    container.list_blobs.return_value = []

    dest = tmp_path / "sample"
    with pytest.raises(RuntimeError, match="No FASTQ files found"):
        download_from_source(_azure_source("S1/"), dest)

    mock_azure_service.assert_called_once()
