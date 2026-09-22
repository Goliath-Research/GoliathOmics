"""Tests for universal CAAS eligibility, path remap signatures, and FOREACH bundles."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_domain.foreach_bundle import (
    commit_iteration_bundle,
    compute_iteration_bundle_key,
    read_iteration_bundle,
    try_short_circuit_foreach_iteration,
)
from methyl_worker.action_catalog import (
    find_catalog_entry,
    idempotency_enabled_for,
    idempotency_opt_out_reason_for,
)
from methyl_worker.action_skip import (
    _canonicalize_abs_path,
    artifacts_from_output,
    compute_input_signature,
    resolve_action_output_dir,
)
from pydantic import BaseModel


def test_default_on_validation_model_mc() -> None:
    entry = find_catalog_entry("validation.model_mc")
    assert entry is not None
    assert idempotency_enabled_for(entry) is True
    assert idempotency_opt_out_reason_for(entry) is None


def test_sample_align_caas_default_on(monkeypatch) -> None:
    monkeypatch.delenv("METHYL_SAMPLE_CAAS_ENABLED", raising=False)
    entry = find_catalog_entry("sample.download_fastq")
    assert entry is not None
    assert idempotency_enabled_for(entry) is True
    assert idempotency_opt_out_reason_for(entry) is None
    align = find_catalog_entry("sample.parabricks_fq2bam")
    assert align is not None
    assert idempotency_enabled_for(align) is True
    assert idempotency_opt_out_reason_for(align) is None


def test_destructive_sample_hard_opt_out() -> None:
    entry = find_catalog_entry("sample.delete_fastqs")
    assert entry is not None
    assert idempotency_opt_out_reason_for(entry) == "destructive"


def test_fs_stat_opt_out() -> None:
    entry = find_catalog_entry("workflow.fs_stat")
    assert entry is not None
    assert idempotency_enabled_for(entry) is False
    assert idempotency_opt_out_reason_for(entry) == "mtime_sensitive"


def test_catalog_export_includes_idempotency_fields() -> None:
    entry = find_catalog_entry("pipeline.centroid")
    assert entry is not None
    payload = entry.to_catalog_dict()
    assert payload["idempotency_enabled"] is True
    sample = find_catalog_entry("sample.delete_bam")
    assert sample is not None
    sp = sample.to_catalog_dict()
    assert sp["idempotency_enabled"] is False
    assert sp["idempotency_opt_out_reason"] == "destructive"


def test_dual_mount_paths_share_canonical_form() -> None:
    a = _canonicalize_abs_path("/lambda/nfs/Work/projects/demo/x.h5", {})
    b = _canonicalize_abs_path("/work/projects/demo/x.h5", {})
    assert a == b == "/work/projects/demo/x.h5"


def test_path_remap_unifies_signature(tmp_path: Path, monkeypatch) -> None:
    entry = find_catalog_entry("validation.stability")
    assert entry is not None

    class FakeInput(BaseModel):
        projectPath: str
        outputDir: str

    work = tmp_path / "work"
    alt = tmp_path / "alt"
    work.mkdir()
    # Simulate dual roots pointing at same relative tree via remap.
    project = work / "project.json"
    project.write_text(
        json.dumps(
            {
                "output_base": str(work),
                "project_name": "Study",
                "path_remap": {str(alt): str(work)},
                "chromosomes": ["1"],
                "contexts": ["CG"],
                "group1": {"label": "g1", "sample_paths": []},
                "group2": {"label": "g2", "sample_paths": []},
            }
        ),
        encoding="utf-8",
    )
    out_a = work / "out"
    out_b = alt / "out"
    out_a.mkdir()
    out_b.mkdir(parents=True)

    model_a = FakeInput(projectPath=str(project), outputDir=str(out_a))
    model_b = FakeInput(projectPath=str(project), outputDir=str(out_b))
    sig_a = compute_input_signature(
        entry,
        {
            "projectPath": str(project),
            "outputDir": str(out_a),
            "resolvedConfig": {"path_remap": {str(alt): str(work)}},
        },
        model_a,
    )
    sig_b = compute_input_signature(
        entry,
        {
            "projectPath": str(project),
            "outputDir": str(out_b),
            "resolvedConfig": {"path_remap": {str(alt): str(work)}},
        },
        model_b,
    )
    assert sig_a == sig_b


def test_centroid_artifact_harvest_from_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "centroids"
    out.mkdir()
    h5 = out / "1-CG.h5"
    h5.write_bytes(b"data")
    refs = artifacts_from_output(
        {"status": "ok", "output_dir": str(out), "centroid_h5_path": str(h5)},
        action_name="pipeline.centroid",
    )
    assert any(Path(r.path).name == "1-CG.h5" for r in refs)


def test_foreach_bundle_roundtrip(tmp_path: Path) -> None:
    key = compute_iteration_bundle_key(
        foreach_node_key="mc_iterations",
        collection_var="iterations",
        iteration_index=0,
        item_payload={"runDir": "/work/p/monte_carlo_runs/run_0001"},
        child_action_revisions=["pipeline.centroid:abc"],
    )
    commit_iteration_bundle(
        tmp_path,
        key,
        foreach_node_key="mc_iterations",
        collection_var="iterations",
        iteration_index=0,
        item_payload={"runDir": "/work/p/monte_carlo_runs/run_0001"},
    )
    hit = read_iteration_bundle(tmp_path, key)
    assert hit is not None
    assert hit["content_key"] == key
    again = try_short_circuit_foreach_iteration(
        tmp_path,
        foreach_node_key="mc_iterations",
        collection_var="iterations",
        iteration_index=0,
        item_payload={"runDir": "/work/p/monte_carlo_runs/run_0001"},
        child_action_revisions=["pipeline.centroid:abc"],
    )
    assert again is not None


def test_sample_caas_explicit_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SAMPLE_CAAS_ENABLED", "0")
    entry = find_catalog_entry("sample.download_fastq")
    assert entry is not None
    assert idempotency_enabled_for(entry) is False
    assert idempotency_opt_out_reason_for(entry) == "sample_caas_disabled"
    align = find_catalog_entry("sample.parabricks_fq2bam")
    assert align is not None
    assert idempotency_enabled_for(align, {"sampleCaasEnabled": True}) is True
    assert idempotency_enabled_for(align, {"sampleCaasEnabled": False}) is False
    assert idempotency_enabled_for(align, {"caasEnabled": False}) is False
    delete = find_catalog_entry("sample.delete_fastqs")
    assert delete is not None
    assert idempotency_enabled_for(delete) is False


def test_sample_align_output_dir_is_sample_dir_not_project_configs(tmp_path: Path) -> None:
    """Align/prep bind sampleDir; CAAS must not treat study projectPath.parent as output."""
    entry = find_catalog_entry("sample.parabricks_fq2bam")
    assert entry is not None
    sample_dir = tmp_path / "samples" / "S1"
    sample_dir.mkdir(parents=True)
    configs = tmp_path / "projects" / "study" / "configs"
    configs.mkdir(parents=True)
    project = configs / "project.json"
    project.write_text("{}", encoding="utf-8")
    resolved = resolve_action_output_dir(
        entry,
        {
            "sampleId": "S1",
            "sampleDir": str(sample_dir),
            "projectPath": str(project),
        },
    )
    assert resolved == sample_dir.resolve()
    assert resolved != configs.resolve()
