"""Unit tests for content-addressed action store (CAAS)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from methyl_domain.action_result import (
    ActionExecutionRecord,
    ArtifactRef,
    artifact_ref_for,
    caas_entry_dir,
    caas_entry_manifest_path,
    instance_ledger_path,
)
from methyl_domain.content_store import (
    append_instance_ledger,
    caas_enabled,
    commit_artifacts_to_store,
    link_entry_into_place,
    read_caas_entry,
    resolve_caas_root,
    resolve_project_root,
    verify_entry_artifacts,
)
from methyl_domain.sample_content_store import (
    resolve_sample_caas_root,
    resolve_sample_id,
    sample_caas_enabled,
    sample_caas_enabled_for_action,
)


def _record(
    *,
    artifacts: list[ArtifactRef],
    input_sig: str = "in-sig",
    output_sig: str = "out-sig",
) -> ActionExecutionRecord:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return ActionExecutionRecord(
        action_name="validation.stability",
        capability="validation.stability",
        started_at_utc=now,
        finished_at_utc=now,
        duration_ms=1,
        artifacts=artifacts,
        action_revision="rev1",
        input_signature=input_sig,
        output_signature=output_sig,
        content_key="content-key-abc",
        hyperparam_set_id="hpset-1",
        task_output={"status": "ok"},
    )


def test_caas_enabled_defaults_on(monkeypatch) -> None:
    monkeypatch.delenv("METHYL_CAAS_ENABLED", raising=False)
    assert caas_enabled({}) is True
    assert caas_enabled({"caasEnabled": True}) is True
    assert caas_enabled({"caasEnabled": False}) is False
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "0")
    assert caas_enabled({}) is False
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "false")
    assert caas_enabled({}) is False
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    assert caas_enabled({}) is True


def test_sample_caas_enabled_defaults_on(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("METHYL_SAMPLE_CAAS_ENABLED", raising=False)
    monkeypatch.setenv("METHYL_SAMPLES_BASE", str(tmp_path))
    assert sample_caas_enabled() is True
    assert sample_caas_enabled_for_action("sample.parabricks_fq2bam") is True
    assert sample_caas_enabled({"sampleCaasEnabled": False}) is False
    assert sample_caas_enabled({"caasEnabled": False}) is False
    monkeypatch.setenv("METHYL_SAMPLE_CAAS_ENABLED", "0")
    assert sample_caas_enabled() is False
    assert sample_caas_enabled({"sampleCaasEnabled": True}) is True
    monkeypatch.delenv("METHYL_SAMPLE_CAAS_ENABLED", raising=False)
    root = resolve_caas_root(
        {"sampleId": "S1"},
        action_name="sample.parabricks_fq2bam",
    )
    assert root == (tmp_path / "S1").resolve()


def test_commit_wrong_output_dir_still_keeps_sample_bam(tmp_path: Path, monkeypatch) -> None:
    """Sample CAAS is a ledger: BAM stays a regular file at sampleDir, never moves to configs/."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    bam = sample_dir / "S1.bam"
    bam.write_bytes(b"BAMDATA")
    configs = tmp_path / "projects" / "study" / "configs"
    configs.mkdir(parents=True)

    record = _record(
        artifacts=[ArtifactRef(path=str(bam), bytes=7)],
        input_sig="align-sig",
        output_sig="align-out",
    )
    committed = commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "align-key",
        record,
        output_dir=configs,
    )
    _assert_in_place_product(bam, b"BAMDATA")
    assert not list(configs.rglob("*.bam"))
    entry = caas_entry_dir(sample_dir, "sample.parabricks_fq2bam", "align-key")
    assert (entry / "manifest.json").is_file()
    assert not (entry / "S1.bam").exists()
    assert committed.artifacts[0].path == str(bam)


def test_second_commit_does_not_relocate_sample_bam(
    tmp_path: Path, monkeypatch
) -> None:
    """A later sample commit must not move or copy the in-place BAM."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    bam = sample_dir / "S1.bam"
    bam.write_bytes(b"BAM-V1")
    (sample_dir / "S1.fastq.gz").write_bytes(b"FQ")

    first = _record(
        artifacts=[ArtifactRef(path=str(bam), bytes=6)],
        input_sig="align-a",
        output_sig="align-a",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-a",
        first,
        output_dir=sample_dir,
    )
    _assert_in_place_product(bam, b"BAM-V1")

    second = _record(
        artifacts=[artifact_ref_for(bam)],
        input_sig="align-b",
        output_sig="align-b",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-b",
        second,
        output_dir=sample_dir,
    )

    _assert_in_place_product(bam, b"BAM-V1")
    rec_a = read_caas_entry(sample_dir, "sample.parabricks_fq2bam", "key-a")
    assert rec_a is not None
    assert verify_entry_artifacts(rec_a)
    linked = link_entry_into_place(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-a",
        output_dir=sample_dir,
    )
    assert linked is not None
    _assert_in_place_product(bam, b"BAM-V1")
    assert not (caas_entry_dir(sample_dir, "sample.parabricks_fq2bam", "key-a") / "S1.bam").exists()


def test_is_durable_caas_blob_does_not_require_regular_file(tmp_path: Path) -> None:
    from methyl_domain.content_store import _is_durable_caas_blob

    blob = tmp_path / "samples" / "S1" / ".caas" / "sample.parabricks_fq2bam" / "key-a" / "S1.bam"
    product = tmp_path / "samples" / "S1" / "S1.bam"
    assert not _is_durable_caas_blob(product)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"BAM")
    assert _is_durable_caas_blob(blob)
    blob.unlink()
    assert _is_durable_caas_blob(blob), "must stay durable after unlink so restore skip still runs"


def test_resolve_project_root_from_mc_run_dir(tmp_path: Path) -> None:
    project_root = tmp_path / "Study"
    run_dir = project_root / "monte_carlo_runs" / "run_0001"
    run_dir.mkdir(parents=True)
    resolved = resolve_project_root({"runDir": str(run_dir)})
    assert resolved == project_root.resolve()


def test_shared_output_dir_uses_flat_commit(tmp_path: Path, monkeypatch) -> None:
    """Sibling chromosome files in one centroids/all dir must not tree-steal each other."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    out_dir = project_root / "monte_carlo_runs" / "_centroid_seed" / "centroids" / "all"
    out_dir.mkdir(parents=True)
    chr1 = out_dir / "1-CG.h5"
    chr1.write_bytes(b"one")
    chr2 = out_dir / "2-CG.h5"
    chr2.write_bytes(b"two")

    record = _record(
        artifacts=[ArtifactRef(path=str(chr1), bytes=3)],
        input_sig="sig-chr1",
        output_sig="out-chr1",
    )
    commit_artifacts_to_store(
        project_root,
        "pipeline.centroid",
        "key-chr1",
        record,
        output_dir=out_dir,
    )
    assert chr1.is_symlink()
    assert not os.path.isabs(os.readlink(chr1))
    assert chr1.resolve().is_file()
    # Sibling must remain a real file (not moved into chr1's CAAS entry).
    assert chr2.is_file() and not chr2.is_symlink()
    assert chr2.read_bytes() == b"two"


def test_commit_and_relink_uses_relative_symlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    stability_dir = project_root / "monte_carlo_runs" / "stability"
    stability_dir.mkdir(parents=True)
    summary = stability_dir / "stability_summary.json"
    summary.write_text("{}", encoding="utf-8")

    record = _record(artifacts=[ArtifactRef(path=str(summary), bytes=2)])
    commit_artifacts_to_store(
        project_root,
        "validation.stability",
        "content-key-rel",
        record,
        output_dir=stability_dir,
    )
    assert summary.is_symlink()
    target = os.readlink(summary)
    assert not os.path.isabs(target), f"expected relative symlink, got {target!r}"
    assert summary.resolve().is_file()


def test_ensure_symlink_concurrent_replace(tmp_path: Path) -> None:
    """Parallel workers must not FileExistsError when relinking the same product path."""
    from concurrent.futures import ThreadPoolExecutor

    from methyl_domain.content_store import _ensure_symlink

    blob_a = tmp_path / "store" / "a.h5"
    blob_b = tmp_path / "store" / "b.h5"
    blob_a.parent.mkdir(parents=True)
    blob_a.write_bytes(b"a")
    blob_b.write_bytes(b"b")
    link = tmp_path / "out" / "1-CG.h5"
    link.parent.mkdir(parents=True)

    def once(blob: Path) -> None:
        _ensure_symlink(link, blob)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(once, [blob_a, blob_b] * 20))
    assert link.is_symlink()
    assert link.resolve().is_file()


def test_commit_and_relink_directory_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    stability_dir = project_root / "monte_carlo_runs" / "stability"
    stability_dir.mkdir(parents=True)
    summary = stability_dir / "stability_summary.json"
    summary.write_text("{}", encoding="utf-8")

    record = _record(artifacts=[ArtifactRef(path=str(summary), bytes=2)])
    committed = commit_artifacts_to_store(
        project_root,
        "validation.stability",
        "content-key-abc",
        record,
        output_dir=stability_dir,
    )
    entry_dir = caas_entry_dir(project_root, "validation.stability", "content-key-abc")
    assert (entry_dir / "stability_summary.json").is_file()
    assert caas_entry_manifest_path(project_root, "validation.stability", "content-key-abc").is_file()
    assert summary.is_symlink()
    assert summary.resolve().parent == entry_dir.resolve()

    other_dir = project_root / "monte_carlo_runs" / "stability_alt"
    other_dir.mkdir(parents=True)
    other_summary = other_dir / "stability_summary.json"
    linked = link_entry_into_place(
        project_root,
        "validation.stability",
        "content-key-abc",
        output_dir=other_dir,
    )
    assert linked is not None
    assert other_summary.is_symlink()
    assert committed.content_key == "content-key-abc"


def test_cross_instance_reuse_same_content_key(tmp_path: Path) -> None:
    project_root = tmp_path / "Study"
    out_a = project_root / "monte_carlo_runs" / "stability"
    out_a.mkdir(parents=True)
    artifact = out_a / "stability_summary.json"
    artifact.write_text('{"a": 1}', encoding="utf-8")

    record = _record(artifacts=[ArtifactRef(path=str(artifact), bytes=len(artifact.read_bytes()))])
    commit_artifacts_to_store(
        project_root,
        "validation.stability",
        "key-shared",
        record,
        output_dir=out_a,
    )

    out_b = project_root / "other" / "stability"
    out_b.mkdir(parents=True)
    target = out_b / "stability_summary.json"
    linked = link_entry_into_place(
        project_root,
        "validation.stability",
        "key-shared",
        output_dir=out_b,
    )
    assert linked is not None
    assert target.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_config_change_forks_new_entry(tmp_path: Path) -> None:
    project_root = tmp_path / "Study"
    out_dir = project_root / "stability"
    out_dir.mkdir(parents=True)
    artifact = out_dir / "stability_summary.json"
    artifact.write_text("v1", encoding="utf-8")

    record_v1 = _record(
        artifacts=[ArtifactRef(path=str(artifact), bytes=2)],
        input_sig="sig-v1",
        output_sig="out-v1",
    )
    commit_artifacts_to_store(project_root, "validation.stability", "key-v1", record_v1, output_dir=out_dir)

    if artifact.is_symlink():
        artifact.unlink()
    artifact.write_text("v2", encoding="utf-8")
    record_v2 = _record(
        artifacts=[ArtifactRef(path=str(artifact), bytes=2)],
        input_sig="sig-v2",
        output_sig="out-v2",
    )
    commit_artifacts_to_store(project_root, "validation.stability", "key-v2", record_v2, output_dir=out_dir)

    assert caas_entry_dir(project_root, "validation.stability", "key-v1").is_dir()
    assert caas_entry_dir(project_root, "validation.stability", "key-v2").is_dir()
    assert (caas_entry_dir(project_root, "validation.stability", "key-v1") / "stability_summary.json").read_text() == "v1"
    assert (caas_entry_dir(project_root, "validation.stability", "key-v2") / "stability_summary.json").read_text() == "v2"


def test_instance_ledger_records_action_run_key(tmp_path: Path) -> None:
    project_root = tmp_path / "Study"
    append_instance_ledger(
        project_root,
        "hpset-abc",
        action_name="pipeline.centroid",
        run_key="1_CG_all",
        content_key="ck-123",
    )
    ledger_path = instance_ledger_path(project_root, "hpset-abc")
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert payload["hyperparam_set_id"] == "hpset-abc"
    assert payload["entries"]["pipeline.centroid:1_CG_all"]["content_key"] == "ck-123"


def test_plan_iterations_preserves_run_relative_paths(tmp_path: Path, monkeypatch) -> None:
    """Same-basename files under run_####/ must not collapse into one CAAS blob."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    mc_root = project_root / "monte_carlo_runs"
    mc_root.mkdir(parents=True)
    # Unowned sibling forces flat (non-tree) commit — the plan_iterations case.
    (mc_root / "queue").mkdir()
    (mc_root / "action_run_log.jsonl").write_text("{}\n", encoding="utf-8")

    artifacts: list[ArtifactRef] = []
    for i in (1, 2):
        run_dir = mc_root / f"run_{i:04d}"
        run_dir.mkdir()
        project = run_dir / "project.json"
        project.write_text(json.dumps({"run": i}), encoding="utf-8")
        train = run_dir / "train_control.csv"
        train.write_text(f"sample\ns{i}\n", encoding="utf-8")
        artifacts.append(ArtifactRef(path=str(project), bytes=project.stat().st_size))
        artifacts.append(ArtifactRef(path=str(train), bytes=train.stat().st_size))

    record = _record(
        artifacts=artifacts,
        input_sig="sig-plan",
        output_sig="out-plan",
    )
    record = record.model_copy(update={"action_name": "validation.plan_iterations"})
    commit_artifacts_to_store(
        project_root,
        "validation.plan_iterations",
        "key-plan",
        record,
        output_dir=mc_root,
    )

    entry_dir = caas_entry_dir(project_root, "validation.plan_iterations", "key-plan")
    assert (entry_dir / "run_0001" / "project.json").is_file()
    assert (entry_dir / "run_0002" / "project.json").is_file()
    assert json.loads((entry_dir / "run_0001" / "project.json").read_text()) == {"run": 1}
    assert json.loads((entry_dir / "run_0002" / "project.json").read_text()) == {"run": 2}

    for i in (1, 2):
        link = mc_root / f"run_{i:04d}" / "project.json"
        assert link.is_symlink()
        assert not os.path.isabs(os.readlink(link))
        assert json.loads(link.read_text(encoding="utf-8")) == {"run": i}
        train_link = mc_root / f"run_{i:04d}" / "train_control.csv"
        assert train_link.is_symlink()
        assert train_link.read_text(encoding="utf-8").startswith("sample")


def test_resolve_blob_rejects_stale_symlink_escaping_entry(tmp_path: Path) -> None:
    """Stale CAAS entry symlinks must not escape entry_dir or crash relink."""
    from methyl_domain.content_store import _relink_artifacts_from_entry, _resolve_blob_in_entry

    entry = tmp_path / "Study" / ".caas" / "validation_plan_iterations" / "key"
    entry.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"x": 1}', encoding="utf-8")
    (entry / "project.json").symlink_to(outside)

    assert _resolve_blob_in_entry(tmp_path / "monte_carlo_runs" / "run_0001" / "project.json", entry) is None
    # Relink must skip the escaping symlink without raising.
    relinked = _relink_artifacts_from_entry(
        [ArtifactRef(path=str(tmp_path / "monte_carlo_runs" / "run_0001" / "project.json"), bytes=1)],
        entry,
        output_dir=tmp_path / "monte_carlo_runs",
    )
    assert relinked == []


def test_caas_skip_relinks_after_product_tree_wipe(tmp_path: Path, monkeypatch) -> None:
    """Manifests must record CAAS blob paths so verify/relink works without product files."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    stability_dir = project_root / "monte_carlo_runs" / "stability"
    stability_dir.mkdir(parents=True)
    summary = stability_dir / "stability_summary.json"
    summary.write_text('{"n": 1}', encoding="utf-8")

    record = _record(
        artifacts=[ArtifactRef(path=str(summary), bytes=summary.stat().st_size)],
        input_sig="sig-wipe",
        output_sig="out-wipe",
    )
    committed = commit_artifacts_to_store(
        project_root,
        "validation.stability",
        "key-wipe",
        record,
        output_dir=stability_dir,
    )
    entry_dir = caas_entry_dir(project_root, "validation.stability", "key-wipe")
    assert all(Path(a.path).is_relative_to(entry_dir.resolve()) for a in committed.artifacts)

    import shutil

    shutil.rmtree(stability_dir)
    stability_dir.mkdir(parents=True)

    from methyl_domain.content_store import read_caas_entry, verify_entry_artifacts

    entry = read_caas_entry(project_root, "validation.stability", "key-wipe")
    assert entry is not None
    assert verify_entry_artifacts(entry)
    linked = link_entry_into_place(
        project_root,
        "validation.stability",
        "key-wipe",
        output_dir=stability_dir,
    )
    assert linked is not None
    assert (stability_dir / "stability_summary.json").is_symlink()
    assert json.loads((stability_dir / "stability_summary.json").read_text()) == {"n": 1}


def test_plan_iterations_recommit_through_existing_caas_symlinks_does_not_steal(
    tmp_path: Path, monkeypatch
) -> None:
    """A second content-key must not flatten/steal blobs from the first via symlink resolve()."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    mc_root = project_root / "monte_carlo_runs"
    mc_root.mkdir(parents=True)
    (mc_root / "queue").mkdir()
    (mc_root / "action_run_log.jsonl").write_text("{}\n", encoding="utf-8")

    artifacts_v1: list[ArtifactRef] = []
    for i in (1, 2):
        run_dir = mc_root / f"run_{i:04d}"
        run_dir.mkdir()
        project = run_dir / "project.json"
        project.write_text(json.dumps({"run": i, "v": 1}), encoding="utf-8")
        train = run_dir / "train_control.csv"
        train.write_text(f"sample\ns{i}\n", encoding="utf-8")
        artifacts_v1.append(ArtifactRef(path=str(project), bytes=project.stat().st_size))
        artifacts_v1.append(ArtifactRef(path=str(train), bytes=train.stat().st_size))

    record_v1 = _record(
        artifacts=artifacts_v1,
        input_sig="sig-plan-v1",
        output_sig="out-plan-v1",
    ).model_copy(
        update={
            "action_name": "validation.plan_iterations",
            "task_output": {
                "iterations": [
                    {"projectPath": str(mc_root / "run_0001" / "project.json")},
                    {"projectPath": str(mc_root / "run_0002" / "project.json")},
                ]
            },
        }
    )
    commit_artifacts_to_store(
        project_root,
        "validation.plan_iterations",
        "key-plan-v1",
        record_v1,
        output_dir=mc_root,
    )
    entry_v1 = caas_entry_dir(project_root, "validation.plan_iterations", "key-plan-v1")
    assert (entry_v1 / "run_0001" / "project.json").is_file()
    assert (entry_v1 / "run_0002" / "project.json").is_file()

    # Simulate a fresh plan under a new content key while product paths are still CAAS symlinks.
    artifacts_v2: list[ArtifactRef] = []
    for i in (1, 2):
        project = mc_root / f"run_{i:04d}" / "project.json"
        assert project.is_symlink()
        # Writers unlink the symlink then write a real file (prepare_path_for_write).
        project.unlink()
        project.write_text(json.dumps({"run": i, "v": 2}), encoding="utf-8")
        train = mc_root / f"run_{i:04d}" / "train_control.csv"
        train.unlink()
        train.write_text(f"sample\ns{i}-v2\n", encoding="utf-8")
        artifacts_v2.append(ArtifactRef(path=str(project), bytes=project.stat().st_size))
        artifacts_v2.append(ArtifactRef(path=str(train), bytes=train.stat().st_size))

    record_v2 = _record(
        artifacts=artifacts_v2,
        input_sig="sig-plan-v2",
        output_sig="out-plan-v2",
    ).model_copy(
        update={
            "action_name": "validation.plan_iterations",
            "task_output": {
                "iterations": [
                    {"projectPath": str(mc_root / "run_0001" / "project.json")},
                    {"projectPath": str(mc_root / "run_0002" / "project.json")},
                ]
            },
        }
    )
    commit_artifacts_to_store(
        project_root,
        "validation.plan_iterations",
        "key-plan-v2",
        record_v2,
        output_dir=mc_root,
    )

    entry_v2 = caas_entry_dir(project_root, "validation.plan_iterations", "key-plan-v2")
    assert (entry_v2 / "run_0001" / "project.json").is_file()
    assert (entry_v2 / "run_0002" / "project.json").is_file()
    assert json.loads((entry_v2 / "run_0001" / "project.json").read_text()) == {"run": 1, "v": 2}
    # Sibling content-key must remain intact (no steal via resolve()+move).
    assert (entry_v1 / "run_0001" / "project.json").is_file()
    assert json.loads((entry_v1 / "run_0001" / "project.json").read_text()) == {"run": 1, "v": 1}
    for i in (1, 2):
        link = mc_root / f"run_{i:04d}" / "project.json"
        assert link.is_symlink()
        assert json.loads(link.read_text(encoding="utf-8")) == {"run": i, "v": 2}


def test_plan_iterations_commit_recovers_run_layout_from_caas_symlink_paths(
    tmp_path: Path, monkeypatch
) -> None:
    """Artifact paths that already resolve into an old CAAS key must keep run_#### layout."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    project_root = tmp_path / "Study"
    mc_root = project_root / "monte_carlo_runs"
    mc_root.mkdir(parents=True)
    (mc_root / "queue").mkdir()

    old_entry = caas_entry_dir(project_root, "validation.plan_iterations", "old-key")
    artifacts: list[ArtifactRef] = []
    for i in (1, 2):
        blob_dir = old_entry / f"run_{i:04d}"
        blob_dir.mkdir(parents=True)
        blob = blob_dir / "project.json"
        blob.write_text(json.dumps({"run": i}), encoding="utf-8")
        logical = mc_root / f"run_{i:04d}" / "project.json"
        logical.parent.mkdir(parents=True, exist_ok=True)
        logical.symlink_to(os.path.relpath(blob, start=logical.parent))
        # Bug reproduction: collectors/planners sometimes recorded the resolved CAAS path.
        artifacts.append(ArtifactRef(path=str(blob.resolve()), bytes=blob.stat().st_size))

    record = _record(
        artifacts=artifacts,
        input_sig="sig-resolved-caas",
        output_sig="out-resolved-caas",
    ).model_copy(
        update={
            "action_name": "validation.plan_iterations",
            "task_output": {
                "iterations": [
                    {"projectPath": str((old_entry / "run_0001" / "project.json").resolve())},
                    {"projectPath": str((old_entry / "run_0002" / "project.json").resolve())},
                ]
            },
        }
    )
    commit_artifacts_to_store(
        project_root,
        "validation.plan_iterations",
        "new-key",
        record,
        output_dir=mc_root,
    )
    new_entry = caas_entry_dir(project_root, "validation.plan_iterations", "new-key")
    assert (new_entry / "run_0001" / "project.json").is_file()
    assert (new_entry / "run_0002" / "project.json").is_file()
    # Must copy, not steal, from the old content key.
    assert (old_entry / "run_0001" / "project.json").is_file()
    assert (old_entry / "run_0002" / "project.json").is_file()
    assert not (old_entry / "run_0001" / "project.json").is_symlink()
    assert not (old_entry / "run_0002" / "project.json").is_symlink()


def test_relink_restores_canonical_paths_when_output_dir_prefix_differs(tmp_path: Path) -> None:
    """Regression: dest_link = output_dir/rel used to skip when not in canon_abs.

    Align records ``/lambda/nfs/Work/samples/{id}/{id}.bam`` while output_dir may
    be ``/work/samples/{id}``. Skipping dest_link left the product path empty.
    """
    from methyl_domain.content_store import _relink_artifacts_from_entry

    sample_dir = tmp_path / "work" / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    entry = sample_dir / ".caas" / "sample_parabricks_fq2bam" / "key-1"
    entry.mkdir(parents=True)
    blob = entry / "S1.bam"
    blob.write_bytes(b"BAMDATA")
    tar_blob = entry / "S1.qc-metrics.tar"
    tar_blob.write_bytes(b"TAR")

    product_bam = sample_dir / "S1.bam"
    product_tar = sample_dir / "S1.qc-metrics.tar"
    alias = tmp_path / "lambda" / "nfs" / "Work" / "samples" / "S1"
    alias.mkdir(parents=True)

    _relink_artifacts_from_entry(
        [
            ArtifactRef(path=str(blob), bytes=7),
            ArtifactRef(path=str(tar_blob), bytes=3),
        ],
        entry,
        output_dir=alias,
        canonical_paths=[product_bam, product_tar],
        task_output={
            "bamPath": str(product_bam),
            "qcMetricsTar": str(product_tar),
        },
    )
    assert product_bam.is_symlink()
    assert product_bam.read_bytes() == b"BAMDATA"
    assert product_tar.is_symlink()
    assert product_tar.read_bytes() == b"TAR"
    # Do not invent a BAM under the mismatched output_dir prefix.
    assert not (alias / "S1.bam").exists()


def test_paths_equivalent_dual_mount_prefixes() -> None:
    from methyl_domain.content_store import _paths_equivalent

    assert _paths_equivalent(
        Path("/work/samples/S1/S1.bam"),
        Path("/lambda/nfs/Work/samples/S1/S1.bam"),
    )
    assert not _paths_equivalent(
        Path("/work/samples/S1/S1.bam"),
        Path("/work/projects/study/configs/S1.bam"),
    )


def test_restore_sample_align_products_relinks_bam_and_tar(tmp_path: Path) -> None:
    from methyl_domain.sample_content_store import restore_sample_align_products

    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    key = sample_dir / ".caas" / "sample_parabricks_fq2bam" / "abc123"
    key.mkdir(parents=True)
    (key / "S1.bam").write_bytes(b"BAM")
    (key / "S1.qc-metrics.tar").write_bytes(b"TAR")
    (key / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.2",
                "action_name": "sample.parabricks_fq2bam",
                "capability": "parabricks.fq2bam",
                "started_at_utc": "2026-08-21T00:00:00Z",
                "finished_at_utc": "2026-08-21T00:00:00Z",
                "duration_ms": 1,
                "result_code": 0,
                "exit_code": 0,
                "artifacts": [
                    {"path": str(key / "S1.bam"), "kind": "file", "bytes": 3},
                    {"path": str(key / "S1.qc-metrics.tar"), "kind": "file", "bytes": 3},
                ],
                "task_output": {
                    "bamPath": str(sample_dir / "S1.bam"),
                    "qcMetricsTar": str(sample_dir / "S1.qc-metrics.tar"),
                },
            }
        ),
        encoding="utf-8",
    )
    restored = restore_sample_align_products(sample_dir, "S1")
    assert (sample_dir / "S1.bam").is_file()
    assert (sample_dir / "S1.bam").read_bytes() == b"BAM"
    assert (sample_dir / "S1.qc-metrics.tar").read_bytes() == b"TAR"
    assert restored


def _write_align_products(sample_dir: Path, sample_id: str = "S1") -> tuple[Path, Path, Path]:
    bam = sample_dir / f"{sample_id}.bam"
    tar = sample_dir / f"{sample_id}.qc-metrics.tar"
    meta = sample_dir / f"{sample_id}.json"
    bam.write_bytes(b"BAMDATA")
    tar.write_bytes(b"TAR")
    meta.write_text("{}", encoding="utf-8")
    return bam, tar, meta


def _align_record(
    artifacts: list[ArtifactRef],
    *,
    task_output: dict | None = None,
    input_sig: str = "align-sig",
) -> ActionExecutionRecord:
    rec = _record(artifacts=artifacts, input_sig=input_sig, output_sig=input_sig)
    update: dict = {"action_name": "sample.parabricks_fq2bam"}
    if task_output is not None:
        update["task_output"] = task_output
    return rec.model_copy(update=update)


def _assert_relative_product_symlink(path: Path, payload: bytes) -> None:
    assert path.is_symlink(), f"{path} should be a product symlink"
    assert not os.path.isabs(os.readlink(path)), f"{path} should use a relative target"
    assert path.read_bytes() == payload
    assert ".caas" in path.resolve().parts


def _assert_in_place_product(path: Path, payload: bytes) -> None:
    assert path.is_file() and not path.is_symlink(), f"{path} must stay a regular product file"
    assert path.read_bytes() == payload
    assert ".caas" not in path.parts


def test_product_link_destinations_falls_back_when_canons_are_blobs(tmp_path: Path) -> None:
    """Durable-blob canonical paths must still restore output_dir/{id}.bam."""
    from methyl_domain.content_store import _product_link_destinations

    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    blob = sample_dir / ".caas" / "sample_parabricks_fq2bam" / "key-a" / "S1.bam"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"BAMDATA")
    dests = _product_link_destinations(
        blob,
        Path("S1.bam"),
        output_dir=sample_dir,
        canonical_paths=[blob],
    )
    assert dests == [sample_dir / "S1.bam"]


def test_product_link_destinations_flat_blob_also_restores_sample_dir_basename(
    tmp_path: Path,
) -> None:
    """Nested AN000 product paths must not skip sampleDir/{basename} restore."""
    from methyl_domain.content_store import _product_link_destinations

    sample_dir = tmp_path / "samples" / "S1"
    lane = sample_dir / "AN000_flowcellA"
    lane.mkdir(parents=True)
    blob = sample_dir / ".caas" / "sample_download_fastq" / "key-a" / "S1_1.fastq.gz"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"FQ")
    dests = _product_link_destinations(
        blob,
        Path("S1_1.fastq.gz"),
        output_dir=sample_dir,
        canonical_paths=[lane / "S1_1.fastq.gz"],
    )
    assert lane / "S1_1.fastq.gz" in dests
    assert sample_dir / "S1_1.fastq.gz" in dests


def test_restore_sample_fastq_products_relinks_even_pair(tmp_path: Path) -> None:
    from methyl_domain.sample_content_store import restore_sample_fastq_products

    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    key = sample_dir / ".caas" / "sample_download_fastq" / "abc123"
    key.mkdir(parents=True)
    (key / "S1_1.fastq.gz").write_bytes(b"R1")
    (key / "S1_2.fastq.gz").write_bytes(b"R2")
    (key / "manifest.json").write_text(
        '{"schema_version":"1.2","action_name":"sample.download_fastq",'
        '"result_code":0,"artifacts":[],"task_output":{'
        '"fastqFiles":[]}}',
        encoding="utf-8",
    )
    restored = restore_sample_fastq_products(sample_dir, "S1")
    assert restored
    assert (sample_dir / "S1_1.fastq.gz").read_bytes() == b"R1"
    assert (sample_dir / "S1_2.fastq.gz").read_bytes() == b"R2"
    assert (sample_dir / "S1_1.fastq.gz").is_symlink()


def test_sample_commit_leaves_align_products_in_place(
    tmp_path: Path, monkeypatch
) -> None:
    """New sample CAAS commits must not move BAM/tar/json into .caas."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    bam, tar, meta = _write_align_products(sample_dir)
    (sample_dir / "S1_1.fastq.gz").write_bytes(b"FQ")

    first = _align_record(
        [
            ArtifactRef(path=str(bam), bytes=7),
            ArtifactRef(path=str(tar), bytes=3),
            ArtifactRef(path=str(meta), bytes=2),
        ],
        task_output={
            "bamPath": str(bam),
            "qcMetricsTar": str(tar),
            "jsonPath": str(meta),
        },
        input_sig="align-a",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-a",
        first,
        output_dir=sample_dir,
    )
    _assert_in_place_product(bam, b"BAMDATA")
    _assert_in_place_product(tar, b"TAR")
    _assert_in_place_product(meta, b"{}")
    entry = caas_entry_dir(sample_dir, "sample.parabricks_fq2bam", "key-a")
    assert (entry / "manifest.json").is_file()
    assert not (entry / "S1.bam").exists()


def test_legacy_blob_recommit_still_restores_stripped_sample_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """Re-commit of already-harvested .caas blobs still restores product links."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    entry = caas_entry_dir(sample_dir, "sample.parabricks_fq2bam", "key-legacy")
    entry.mkdir(parents=True)
    blob_bam = entry / "S1.bam"
    blob_tar = entry / "S1.qc-metrics.tar"
    blob_meta = entry / "S1.json"
    blob_bam.write_bytes(b"BAMDATA")
    blob_tar.write_bytes(b"TAR")
    blob_meta.write_text("{}", encoding="utf-8")

    second = _align_record(
        [
            artifact_ref_for(blob_bam),
            artifact_ref_for(blob_tar),
            artifact_ref_for(blob_meta),
        ],
        task_output={
            "bamPath": str(blob_bam),
            "qcMetricsTar": str(blob_tar),
            "jsonPath": str(blob_meta),
        },
        input_sig="align-b",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-legacy",
        second,
        output_dir=sample_dir,
    )

    bam = sample_dir / "S1.bam"
    tar = sample_dir / "S1.qc-metrics.tar"
    meta = sample_dir / "S1.json"
    _assert_relative_product_symlink(bam, b"BAMDATA")
    _assert_relative_product_symlink(tar, b"TAR")
    _assert_relative_product_symlink(meta, b"{}")
    assert blob_bam.exists() and blob_bam.is_file() and not blob_bam.is_symlink()


def test_sample_commit_without_sibling_still_leaves_products(
    tmp_path: Path, monkeypatch
) -> None:
    """Even when sampleDir is exclusively align products, do not tree-move it."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    bam, tar, meta = _write_align_products(sample_dir)

    first = _align_record(
        [
            ArtifactRef(path=str(bam), bytes=7),
            ArtifactRef(path=str(tar), bytes=3),
            ArtifactRef(path=str(meta), bytes=2),
        ],
        input_sig="align-tree-a",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-tree-a",
        first,
        output_dir=sample_dir,
    )
    _assert_in_place_product(bam, b"BAMDATA")
    _assert_in_place_product(tar, b"TAR")
    _assert_in_place_product(meta, b"{}")
    assert not (
        caas_entry_dir(sample_dir, "sample.parabricks_fq2bam", "key-tree-a") / "S1.bam"
    ).exists()


def test_skip_in_place_sample_entry_keeps_products(tmp_path: Path, monkeypatch) -> None:
    """Skip-replay of an in-place sample entry leaves FASTQ/BAM at sampleDir."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    bam, tar, meta = _write_align_products(sample_dir)
    (sample_dir / "S1_1.fastq.gz").write_bytes(b"FQ")

    first = _align_record(
        [
            ArtifactRef(path=str(bam), bytes=7),
            ArtifactRef(path=str(tar), bytes=3),
            ArtifactRef(path=str(meta), bytes=2),
        ],
        task_output={
            "bamPath": str(bam),
            "qcMetricsTar": str(tar),
            "jsonPath": str(meta),
        },
        input_sig="align-skip",
    )
    commit_artifacts_to_store(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-skip",
        first,
        output_dir=sample_dir,
    )
    linked = link_entry_into_place(
        sample_dir,
        "sample.parabricks_fq2bam",
        "key-skip",
        output_dir=sample_dir,
    )
    assert linked is not None
    _assert_in_place_product(bam, b"BAMDATA")
    _assert_in_place_product(tar, b"TAR")
    _assert_in_place_product(meta, b"{}")


def test_sample_commit_keeps_arm_leaf_products_off_sample_root(
    tmp_path: Path, monkeypatch
) -> None:
    """CAAS ledger stays on sample identity; BAM/tar stay in the aligner leaf."""
    monkeypatch.setenv("METHYL_CAAS_ENABLED", "true")
    sample_root = tmp_path / "samples" / "S1"
    arm = sample_root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    bam, tar, meta = _write_align_products(arm)
    (arm / "S1_1.fastq.gz").write_bytes(b"FQ")

    first = _align_record(
        [
            ArtifactRef(path=str(bam), bytes=7),
            ArtifactRef(path=str(tar), bytes=3),
            ArtifactRef(path=str(meta), bytes=2),
        ],
        task_output={
            "bamPath": str(bam),
            "qcMetricsTar": str(tar),
            "jsonPath": str(meta),
        },
        input_sig="align-arm-a",
    )
    commit_artifacts_to_store(
        sample_root,
        "sample.parabricks_fq2bam",
        "key-arm-a",
        first,
        output_dir=arm,
    )
    _assert_in_place_product(bam, b"BAMDATA")
    _assert_in_place_product(tar, b"TAR")
    assert not (sample_root / "S1.bam").exists()
    entry = caas_entry_dir(sample_root, "sample.parabricks_fq2bam", "key-arm-a")
    assert (entry / "manifest.json").is_file()
    assert not (entry / "S1.bam").exists()

    linked = link_entry_into_place(
        sample_root,
        "sample.parabricks_fq2bam",
        "key-arm-a",
        output_dir=arm,
    )
    assert linked is not None
    _assert_in_place_product(bam, b"BAMDATA")
    assert not (sample_root / "S1.bam").exists()


def test_restore_sample_align_products_from_parent_caas_into_arm(tmp_path: Path) -> None:
    from methyl_domain.sample_content_store import restore_sample_align_products

    sample_root = tmp_path / "samples" / "S1"
    arm = sample_root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    key = sample_root / ".caas" / "sample_parabricks_fq2bam" / "abc123"
    key.mkdir(parents=True)
    (key / "S1.bam").write_bytes(b"BAM")
    (key / "S1.qc-metrics.tar").write_bytes(b"TAR")
    (key / "S1.json").write_text("{}", encoding="utf-8")
    (key / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.2",
                "action_name": "sample.parabricks_fq2bam",
                "capability": "parabricks.fq2bam",
                "started_at_utc": "2026-08-21T00:00:00Z",
                "finished_at_utc": "2026-08-21T00:00:00Z",
                "duration_ms": 1,
                "result_code": 0,
                "exit_code": 0,
                "artifacts": [
                    {"path": str(key / "S1.bam"), "kind": "file", "bytes": 3},
                    {"path": str(key / "S1.qc-metrics.tar"), "kind": "file", "bytes": 3},
                    {"path": str(key / "S1.json"), "kind": "file", "bytes": 2},
                ],
                "task_output": {
                    "bamPath": str(arm / "S1.bam"),
                    "qcMetricsTar": str(arm / "S1.qc-metrics.tar"),
                    "jsonPath": str(arm / "S1.json"),
                },
            }
        ),
        encoding="utf-8",
    )
    restored = restore_sample_align_products(arm, "S1")
    assert restored
    assert (arm / "S1.bam").read_bytes() == b"BAM"
    assert (arm / "S1.qc-metrics.tar").read_bytes() == b"TAR"
    assert (arm / "S1.json").read_bytes() == b"{}"
    assert not (sample_root / "S1.bam").exists()


def test_resolve_sample_caas_root_arm_leaf_and_sample_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("METHYL_SAMPLES_BASE", str(tmp_path / "samples"))
    sample_root = tmp_path / "samples" / "S1"
    arm = sample_root / "align.linear.parabricks"
    assert resolve_sample_caas_root({"sampleRoot": str(sample_root)}) == sample_root.resolve()
    assert resolve_sample_caas_root({"sampleDir": str(arm)}) == sample_root.resolve()
    assert resolve_sample_id({"sampleDir": str(arm)}) == "S1"
    assert resolve_sample_caas_root({"sampleId": "S1"}) == sample_root.resolve()
    assert resolve_sample_id({"sampleDir": str(sample_root)}) == "S1"

