"""stageFastqs=local (Align) and workDirMode=local (methylGrapher extract)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_utils import local_align
from methyl_worker import fastq_stage
from methyl_worker import parabricks_runner as runner
from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.task_models.sample_prep_models import (
    DockerAlignTaskInput,
    MethylGrapherWgbsAlignTaskInput,
    MethylGrapherWgbsExtractTaskInput,
    ParabricksFq2bamTaskInput,
    ParabricksGiraffeTaskInput,
)

STAGE = {"stageFastqs": "local"}
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "local"
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(root))
    return root


def _arm(tmp_path: Path, sample_id: str = "S1") -> Path:
    arm = tmp_path / "work" / "samples" / sample_id / "align.linear.parabricks"
    arm.mkdir(parents=True)
    return arm


def test_stage_mode_parsing() -> None:
    assert fastq_stage.stage_fastqs_mode(None) is None
    assert fastq_stage.stage_fastqs_mode({}) is None
    assert fastq_stage.stage_fastqs_mode({"stageFastqs": None}) is None
    assert fastq_stage.stage_fastqs_mode(STAGE) == "local"
    with pytest.raises(ValueError):
        fastq_stage.stage_fastqs_mode({"stageFastqs": "s3"})


def test_stage_copies_into_local_bam_dir(tmp_path: Path, local_root: Path) -> None:
    arm = _arm(tmp_path)
    r1, r2 = arm / "S1_1.fastq.gz", arm / "S1_2.fastq.gz"
    r1.write_bytes(b"one")
    r2.write_bytes(b"two")
    assert fastq_stage.stage_fastqs([r1, r2], sample_id="S1", sample_dir=arm, input_json={}) == [
        r1,
        r2,
    ]
    staged = fastq_stage.stage_fastqs([r1, r2], sample_id="S1", sample_dir=arm, input_json=STAGE)
    bam_dir = local_align.local_align_dir("S1", arm)
    assert [p.parent for p in staged] == [bam_dir / "fastq"] * 2
    assert [p.read_bytes() for p in staged] == [b"one", b"two"]
    assert r1.is_file() and r2.is_file()  # /work copies untouched
    assert not list(bam_dir.rglob("*.part"))
    # delete_bam / failure cleanup frees the staged copies with the BAM dir.
    local_align.remove_local_align("S1", arm)
    assert not bam_dir.exists()
    assert r1.is_file()


def test_container_fastq_arg_maps_staged_and_work(tmp_path: Path, local_root: Path) -> None:
    arm = _arm(tmp_path)
    ref = tmp_path / "genome.fa"
    ref.write_text(">r\n", encoding="utf-8")
    paths = runner._resolve_paths(arm, "S1", ref)
    staged = paths.bam_path.parent / "fastq" / "S1_1.fastq.gz"
    assert runner.container_fastq_arg(staged, paths) == "/bamdir/fastq/S1_1.fastq.gz"
    assert runner.container_fastq_arg(arm / "S1_1.fastq.gz", paths) == "/workdir/S1_1.fastq.gz"


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


def test_fq2bam_aligns_from_staged_fastqs(
    tmp_path: Path, local_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm, ref = _host_align_env(monkeypatch, tmp_path)
    paths = runner._resolve_paths(arm, "S1", ref)
    local_fq = local_align.local_align_dir("S1", arm) / "fastq"

    def fake_align(cmd, *, log_path, label):
        joined = " ".join(cmd)
        assert str(local_fq / "S1_1.fastq.gz") in joined
        assert str(arm / "S1_1.fastq.gz") not in joined
        paths.bam_path.write_bytes(b"BAM")
        paths.qc_metrics_dir.mkdir()
        (paths.qc_metrics_dir / "m.txt").write_text("x", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("methyl_worker.capabilities._gpu_available", return_value=True):
        with patch.object(runner, "_run_align_subprocess", side_effect=fake_align):
            runner.run_fq2bam_meth(
                sample_id="S1", sample_dir=arm, reference_fasta=ref, input_json=STAGE
            )
    assert (local_fq / "S1_2.fastq.gz").read_bytes() == b"2"
    assert (arm / "S1_1.fastq.gz").is_file()


def test_fq2bam_failure_removes_staged_fastqs(
    tmp_path: Path, local_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arm, ref = _host_align_env(monkeypatch, tmp_path)

    def failing(cmd, *, log_path, label):
        raise RuntimeError("pbrun crashed")

    with patch("methyl_worker.capabilities._gpu_available", return_value=True):
        with patch.object(runner, "_run_align_subprocess", side_effect=failing):
            with pytest.raises(RuntimeError, match="pbrun crashed"):
                runner.run_fq2bam_meth(
                    sample_id="S1", sample_dir=arm, reference_fasta=ref, input_json=STAGE
                )
    assert not local_align.local_align_dir("S1", arm).exists()
    assert (arm / "S1_1.fastq.gz").is_file()


def test_docker_align_uses_staged_pair(tmp_path: Path, local_root: Path) -> None:
    from methyl_worker import docker_align_runner as dar

    arm = _arm(tmp_path)
    (arm / "S1_R1.fastq.gz").write_bytes(b"r1")
    (arm / "S1_R2.fastq.gz").write_bytes(b"r2")
    ref = tmp_path / "ref.fa"
    ref.write_text(">1\nACGT\n", encoding="utf-8")
    seen: list[list[str]] = []

    def fake_run(cmd, check):
        seen.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"BAM")
        return subprocess.CompletedProcess(cmd, 0)

    with patch.object(dar.subprocess, "run", side_effect=fake_run):
        dar.run_docker_align(
            sample_id="S1",
            sample_dir=arm,
            reference_fasta=ref,
            input_json={
                **STAGE,
                "resolvedConfig": {"image": "img", "argv": ["align", "{r1}", "{r2}", "{bam}"]},
            },
        )
    local_fq = local_align.local_align_dir("S1", arm) / "fastq"
    assert str(local_fq / "S1_R1.fastq.gz") in seen[0]
    assert str(local_fq / "S1_R2.fastq.gz") in seen[0]


def test_methylgrapher_align_and_local_extract(
    tmp_path: Path, local_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from methyl_worker import methylgrapher_wgbs_runner as mg
    from test_methylgrapher_wgbs import _touch_bundle

    monkeypatch.setenv("METHYL_METHYLGRAPHER_DRY_RUN", "1")
    cfg = _touch_bundle(tmp_path / "assets")
    sample_dir = tmp_path / "work" / "samples" / "S1" / "align.pangenome_wgbs.methylgrapher"
    sample_dir.mkdir(parents=True)
    (sample_dir / "S1_R1.fastq.gz").write_bytes(b"a")
    (sample_dir / "S1_R2.fastq.gz").write_bytes(b"b")
    monkeypatch.setattr(
        runner,
        "resolve_paired_fastqs",
        lambda p, sid: (p / f"{sid}_R1.fastq.gz", p / f"{sid}_R2.fastq.gz"),
    )
    captured: dict[str, Path] = {}
    real_build = mg.build_align_command

    def wrap_build(**kwargs):
        captured["fq1"] = kwargs["fq1"]
        return real_build(**kwargs)

    monkeypatch.setattr(mg, "build_align_command", wrap_build)
    mg.run_methylgrapher_wgbs_align(
        sample_id="S1", sample_dir=sample_dir, input_json={"resolvedConfig": cfg, **STAGE}
    )
    resolved = sample_dir.resolve()
    assert captured["fq1"] == local_align.local_align_dir("S1", resolved) / "fastq" / "S1_R1.fastq.gz"

    out = mg.run_methylgrapher_wgbs_extract(
        sample_id="S1",
        sample_dir=sample_dir,
        project=str(tmp_path / "project.json"),
        input_json={"resolvedConfig": cfg, "forceRealign": True, "workDirMode": "local"},
    )
    assert out["h5Files"]
    for h5 in out["h5Files"]:
        assert Path(h5).parent == resolved and Path(h5).is_file()
    assert list(resolved.glob("*.patterns.h5"))
    manifest = Path(out["manifestPath"])
    assert manifest.parent == resolved and manifest.is_file()
    assert str(local_root) not in manifest.read_text(encoding="utf-8")
    # Align's work dir may exist on /work; extract scratch must not land there.
    assert not (resolved / "methylgrapher_work" / "linear_cpg_calls.tsv").exists()
    assert not local_align.local_extract_dir("S1", resolved).exists()
    with pytest.raises(ValueError):
        mg.extract_work_dir_mode({"workDirMode": "s3"})


def test_task_models_accept_new_params() -> None:
    base = {"sampleId": "S1", "sampleDir": "/work/samples/S1"}
    for model in (
        ParabricksFq2bamTaskInput,
        ParabricksGiraffeTaskInput,
        MethylGrapherWgbsAlignTaskInput,
        DockerAlignTaskInput,
    ):
        assert model(**base, stageFastqs="local").stageFastqs == "local"
        with pytest.raises(Exception):
            model.model_validate({**base, "stageFastqs": "remote"})
    ex = MethylGrapherWgbsExtractTaskInput(
        **base, projectPath="/work/p.json", workDirMode="local"
    )
    assert ex.workDirMode == "local"


def _walk(node, out):
    if isinstance(node, dict):
        if "action" in node:
            out.append(node)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def test_sample_prep_program_stages_every_align_and_local_extract() -> None:
    prog = json.loads(
        (REPO / "workflow_engine/domain/fixtures/sample_prep.program.json").read_text()
    )
    steps: list[dict] = []
    _walk(prog, steps)
    aligns = [
        s
        for s in steps
        if s["action"]
        in {
            "sample.parabricks_fq2bam",
            "sample.parabricks_giraffe",
            "sample.methylgrapher_wgbs_align",
            "sample.docker_align",
        }
    ]
    assert len(aligns) == 6
    assert all(s.get("with", {}).get("stageFastqs") == "local" for s in aligns)
    extracts = [s for s in steps if s["action"] == "sample.methylgrapher_wgbs_extract"]
    assert len(extracts) == 2
    assert all(s["with"]["workDirMode"] == "local" for s in extracts)


def test_fastq_archive_and_delete_are_unpinned_and_capability_free() -> None:
    for name in ("sample.archive_sample", "sample.delete_fastqs"):
        entry = find_catalog_entry(name)
        assert entry is not None
        dispatch = entry.dispatch.to_dict()
        assert "require_previous_worker" not in dispatch, name
        assert "exclusive_worker" not in dispatch, name
        assert entry.execution_mode == "in_process", name
        assert "gpu" not in entry.capability and "parabricks" not in entry.capability, name
