"""Tests for capability dispatch."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_worker.handlers import _handle_download_fastq, execute_task
from methyl_worker.handlers.sample_prep import _handle_delete_fastqs
from methyl_worker import parabricks_runner as runner
from methyl_worker.task_models import DeleteFastqsTaskInput, DownloadFastqTaskInput


def test_download_handler_receives_validated_input_model() -> None:
    task = DownloadFastqTaskInput.model_validate(
        {
            "sampleId": "S1",
            "sampleDir": "/tmp/S1",
            "fastqSource": {"type": "file", "basePath": "/data", "prefix": "S1/"},
        }
    )
    with patch("methyl_worker.fastq_source.download_from_source", return_value=["S1_1.fastq.gz"]) as mock_dl:
        out = _handle_download_fastq("sample.download-fastq", "sample.download_fastq", task)
    mock_dl.assert_called_once()
    assert out.n_files == 1


def test_download_writes_sample_root_and_links_arm(tmp_path: Path) -> None:
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    task = DownloadFastqTaskInput.model_validate(
        {
            "sampleId": "S1",
            "sampleDir": str(arm),
            "sampleRoot": str(root),
            "fastqSource": {"type": "file", "basePath": "/data", "prefix": "S1/"},
        }
    )

    def _fake_download(_source, dest, resolved_config=None):
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        fq = dest_path / "S1_1.fastq.gz"
        fq.write_bytes(b"fastq")
        return [str(fq)]

    with patch("methyl_worker.fastq_source.download_from_source", side_effect=_fake_download):
        out = _handle_download_fastq("sample.download-fastq", "sample.download_fastq", task)
    assert out.n_files == 1
    assert (root / "S1_1.fastq.gz").is_file()
    assert (arm / "S1_1.fastq.gz").is_file()


def test_delete_fastqs_targets_sample_root_and_arm_links(tmp_path: Path) -> None:
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    fq = root / "S1_1.fastq.gz"
    fq.write_bytes(b"fastq")
    os.link(fq, arm / "S1_1.fastq.gz")
    task = DeleteFastqsTaskInput.model_validate(
        {
            "sampleId": "S1",
            "sampleDir": str(arm),
            "sampleRoot": str(root),
        }
    )
    out = _handle_delete_fastqs("sample.delete-fastqs", "sample.delete_fastqs", task)
    assert out.n_files_removed >= 1
    assert not fq.exists()
    assert not (arm / "S1_1.fastq.gz").exists()


def test_mark_failed_handler() -> None:
    result = execute_task(
        "sample.mark-failed",
        "sample.qc_failed",
        {"sampleId": "S1", "sampleDir": "/work/samples/S1", "reason": "alignment_qc_failed"},
    )
    out = result.output.model_dump()
    assert out["status"] == "QC_FAILED"
    assert out["sampleId"] == "S1"


def test_download_requires_fastq_source() -> None:
    with pytest.raises(Exception):
        execute_task(
            "sample.download-fastq",
            "sample.download_fastq",
            {"sampleId": "S1", "sampleDir": "/tmp/S1"},
        )


def test_parabricks_idempotent_when_outputs_exist(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    bam = sample_dir / "S1.bam"
    metrics = sample_dir / "S1.json"
    ref = tmp_path / "genome.fa"
    ref.write_text(">ref\n")
    bam.write_bytes(b"BAM")
    metrics.write_text("{}")
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    with patch.dict(
        "os.environ",
        {"METHYL_PARABRICKS_IMAGE": "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1"},
    ):
        with patch(
            "methyl_worker.handlers.sample_prep.resolve_reference_fasta",
            return_value=str(ref),
        ):
            with patch(
                "methyl_worker.parabricks_runner.resolve_parabricks_config",
                return_value=runner.ParabricksConfig(
                    image="nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1",
                    gpu_flags=("--gpus", "all"),
                    bwa_threads=16,
                    extra_docker_args=(),
                    cleanup_tmp=True,
                ),
            ):
                with patch("methyl_worker.parabricks_runner.subprocess.run") as mock_run:
                    result = execute_task(
                    "parabricks.fq2bam",
                    "sample.parabricks_fq2bam",
                    {
                        "tool": "ParabricksFq2Bam",
                        "sampleId": "S1",
                        "sampleDir": str(sample_dir),
                        "projectPath": str(project),
                    },
                )

    mock_run.assert_not_called()
    out = result.output.model_dump()
    assert out["bamPath"] == str(bam)
    assert out["metricsJson"] == str(metrics)


def test_methyl_extract_idempotent_when_outputs_exist(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    for name in ("1-CG.h5", "1-CHG.h5", "1-CHH.h5"):
        (sample_dir / name).write_bytes(b"h5")

    with patch("methyl_worker.extract_runner.subprocess.run") as mock_run:
        with patch("methyl_worker.extract_runner.run_methyl_extract") as mock_extract:
            mock_extract.return_value = {
                "sampleId": "S1",
                "h5Files": ["1-CG.h5", "1-CHG.h5", "1-CHH.h5"],
            }
            result = execute_task(
                "methyl-extract",
                "sample.methyl_extract",
                {
                    "tool": "MethylExtract",
                    "sampleId": "S1",
                    "sampleDir": str(sample_dir),
                    "projectPath": str(tmp_path / "project.json"),
                },
            )

    mock_extract.assert_called_once()
    mock_run.assert_not_called()
    out = result.output.model_dump()
    assert out["h5Files"] == ["1-CG.h5", "1-CHG.h5", "1-CHH.h5"]


def test_pipeline_cli_dispatch() -> None:
    with patch("methyl_worker.execution_handle.run_cancellable") as mock_run:
        mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "done", "stderr": ""})()
        with patch(
            "methyl_worker.capabilities.assert_execute_host_tool_prereqs",
            return_value=None,
        ):
            result = execute_task(
                "methyl-mapper",
                "pipeline.mapper",
                {"tool": "MethylMapper", "project": "/cfg/project.json"},
            )
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["methyl-mapper", "--project", "/cfg/project.json"]
    assert result.output.model_dump()["status"] == "ok"
