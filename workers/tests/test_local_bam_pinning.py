"""SamplePrep keeps the Align BAM on worker-local scratch, pinned to one worker."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_utils import local_align
from methyl_worker import parabricks_runner as runner
from methyl_worker.action_catalog import find_catalog_entry

PINNED = (
    "sample.parabricks_fq2bam",
    "sample.parabricks_giraffe",
    "sample.methylgrapher_wgbs_align",
    "sample.methyl_qc",
    "sample.fragmentomics",
    "sample.methyl_extract",
    "sample.methylgrapher_wgbs_extract",
    "sample.extraction_qc",
    "sample.delete_bam",
)
UNPINNED = (
    "sample.download_fastq",
    "sample.trim_fastq",
    "sample.archive_sample",
    "sample.delete_fastqs",
    "sample.parabricks_rna_fq2bam",
)


@pytest.fixture
def local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "local"
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(root))
    return root


def _arm(tmp_path: Path, sample_id: str = "S1") -> Path:
    arm = tmp_path / "work" / "samples" / sample_id / "align.linear.parabricks"
    arm.mkdir(parents=True)
    return arm


def _write_local_bam(sample_id: str, arm: Path) -> Path:
    bam = local_align.local_bam_path(sample_id, arm)
    bam.parent.mkdir(parents=True, exist_ok=True)
    bam.write_bytes(b"BAM")
    (bam.parent / f"{sample_id}.bam.bai").write_bytes(b"BAI")
    return bam


def test_bam_chain_actions_are_hard_pinned() -> None:
    for name in PINNED:
        entry = find_catalog_entry(name)
        assert entry is not None, name
        dispatch = entry.dispatch.to_dict()
        assert dispatch.get("require_previous_worker") is True, name
        assert dispatch.get("affinity_key_field") == "sampleId", name
    for name in UNPINNED:
        entry = find_catalog_entry(name)
        assert entry is not None, name
        assert "require_previous_worker" not in entry.dispatch.to_dict(), name


def test_catalog_seed_passes_require_previous_worker() -> None:
    from workflow_engine.ops.catalog_seed import _dispatch_fields

    fields = _dispatch_fields({"dispatch": {"require_previous_worker": True}})
    assert fields["require_previous_worker"] is True
    assert _dispatch_fields({})["require_previous_worker"] is False


def test_local_tmp_root_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(tmp_path / "explicit"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "sys"))
    assert local_align.local_tmp_root() == tmp_path / "explicit"
    monkeypatch.delenv("METHYL_LOCAL_TMP")
    assert local_align.local_tmp_root() == tmp_path / "sys" / "methyl-pb"
    monkeypatch.delenv("TMPDIR")
    assert local_align.local_tmp_root() == local_align.DEFAULT_LOCAL_TMP_ROOT


def test_align_paths_put_bam_on_local_scratch(tmp_path: Path, local_root: Path) -> None:
    arm = _arm(tmp_path)
    ref = tmp_path / "genome.fa"
    ref.write_text(">ref\n", encoding="utf-8")
    paths = runner._resolve_paths(arm, "S1", ref)
    assert paths.bam_path.is_relative_to(local_root)
    assert not paths.bam_path.is_relative_to(tmp_path / "work")
    assert paths.bam_path == local_align.local_bam_path("S1", arm)
    # QC metrics still land in the arm on /work.
    assert paths.qc_metrics_tar.parent == arm


def _host_align_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    monkeypatch.setenv("METHYL_PARABRICKS_MODE", "host")
    monkeypatch.setenv("METHYL_PARABRICKS_IMAGE", "host-pbrun")
    monkeypatch.delenv("METHYL_PBRUN_BIN", raising=False)
    monkeypatch.setattr(
        runner.shutil, "which", lambda name: "/usr/bin/pbrun" if name == "pbrun" else None
    )
    arm = _arm(tmp_path)
    ref = tmp_path / "genome.fa"
    ref.write_text(">ref\n", encoding="utf-8")
    for path in runner.bwameth_c2t_index_paths(ref):
        path.write_bytes(b"")
    (arm / "S1_1.fastq.gz").write_bytes(b"1")
    (arm / "S1_2.fastq.gz").write_bytes(b"2")
    return arm, ref


def test_fq2bam_success_keeps_bam_off_work(
    tmp_path: Path, local_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm, ref = _host_align_env(monkeypatch, tmp_path)
    paths = runner._resolve_paths(arm, "S1", ref)

    def fake_align(cmd, *, log_path, label):
        assert f"--out-bam={paths.bam_path.resolve()}" in cmd
        paths.bam_path.write_bytes(b"BAM")
        paths.qc_metrics_dir.mkdir()
        (paths.qc_metrics_dir / "m.txt").write_text("x", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("methyl_worker.capabilities._gpu_available", return_value=True):
        with patch.object(runner, "_run_align_subprocess", side_effect=fake_align):
            out = runner.run_fq2bam_meth(sample_id="S1", sample_dir=arm, reference_fasta=ref)

    assert out["bamPath"] == str(paths.bam_path)
    assert paths.bam_path.is_file()
    assert not list((tmp_path / "work").rglob("*.bam"))
    assert (arm / "S1.qc-metrics.tar").is_file()


def test_fq2bam_failure_removes_local_bam(
    tmp_path: Path, local_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm, ref = _host_align_env(monkeypatch, tmp_path)
    paths = runner._resolve_paths(arm, "S1", ref)

    def failing_align(cmd, *, log_path, label):
        paths.bam_path.write_bytes(b"partial")
        raise RuntimeError("pbrun crashed")

    with patch("methyl_worker.capabilities._gpu_available", return_value=True):
        with patch.object(runner, "_run_align_subprocess", side_effect=failing_align):
            with pytest.raises(RuntimeError, match="pbrun crashed"):
                runner.run_fq2bam_meth(sample_id="S1", sample_dir=arm, reference_fasta=ref)

    assert not paths.bam_path.exists()
    assert not paths.bam_path.parent.exists()


def test_delete_bam_removes_local_bam_and_index(tmp_path: Path, local_root: Path) -> None:
    from methyl_worker.handlers.sample_prep import _handle_delete_bam
    from methyl_worker.task_models.sample_prep_models import DeleteBamTaskInput

    arm = _arm(tmp_path)
    bam = _write_local_bam("S1", arm)
    out = _handle_delete_bam(
        "sample.delete-bam",
        "sample.delete_bam",
        DeleteBamTaskInput(sampleId="S1", sampleDir=str(arm)),
    )
    assert out.n_files_removed == 2
    assert not bam.parent.exists()


def test_extract_failure_removes_local_bam(tmp_path: Path, local_root: Path) -> None:
    from methyl_worker.handlers.sample_prep import _handle_methyl_extract
    from methyl_worker.task_models.sample_prep_models import MethylExtractTaskInput

    arm = _arm(tmp_path)
    bam = _write_local_bam("S1", arm)
    task = MethylExtractTaskInput(sampleId="S1", sampleDir=str(arm), projectPath="/p.json")
    with patch(
        "methyl_worker.extract_runner.run_methyl_extract",
        side_effect=RuntimeError("MethylExtractor failed"),
    ):
        with pytest.raises(RuntimeError, match="MethylExtractor failed"):
            _handle_methyl_extract("methyl-extract", "sample.methyl_extract", task)
    assert not bam.exists()


def test_extract_success_keeps_local_bam_for_delete_bam(tmp_path: Path, local_root: Path) -> None:
    from methyl_worker.handlers.sample_prep import _handle_methyl_extract
    from methyl_worker.task_models.sample_prep_models import MethylExtractTaskInput

    arm = _arm(tmp_path)
    bam = _write_local_bam("S1", arm)
    task = MethylExtractTaskInput(sampleId="S1", sampleDir=str(arm), projectPath="/p.json")
    with patch(
        "methyl_worker.extract_runner.run_methyl_extract",
        return_value={"sampleId": "S1", "h5Files": ["1-CG.h5"]},
    ):
        out = _handle_methyl_extract("methyl-extract", "sample.methyl_extract", task)
    assert out.h5Files == ["1-CG.h5"]
    assert bam.is_file()


def test_bam_consumers_resolve_local_bam(tmp_path: Path, local_root: Path) -> None:
    from methyl_alignment_qc.core.bam_flagstat import resolve_sample_bam
    from methyl_worker.extract_runner import find_bam_path, resolve_bam_path

    arm = _arm(tmp_path)
    assert find_bam_path(arm, "S1") is None
    assert resolve_sample_bam(arm, "S1") == arm / "S1.bam"
    bam = _write_local_bam("S1", arm)
    assert resolve_bam_path(arm, "S1") == bam
    assert resolve_sample_bam(arm, "S1") == bam


def test_caas_align_restore_never_links_bam(tmp_path: Path) -> None:
    from methyl_domain.sample_content_store import restore_sample_align_products

    root = tmp_path / "samples" / "S1"
    arm = root / "align.parabricks"
    arm.mkdir(parents=True)
    key = root / ".caas" / "sample.methylgrapher_wgbs_align" / "k1"
    key.mkdir(parents=True)
    (key / "manifest.json").write_text("{}", encoding="utf-8")
    (key / "S1.bam").write_bytes(b"BAM")
    (key / "S1.qc-metrics.tar").write_bytes(b"TAR")
    restore_sample_align_products(arm, "S1")
    assert not (arm / "S1.bam").exists()
