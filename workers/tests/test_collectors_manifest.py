"""Manifest-first collector behavior for pipeline CLI actions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_domain.action_result import atomic_write_json, manifest_path_for
from methyl_worker.collectors import (
    CentroidLegacyCollector,
    DetectorLegacyCollector,
    EnricherLegacyCollector,
    GeneSelectLegacyCollector,
    ManifestFirstCollector,
)
from methyl_worker.task_models.pipeline_models import (
    CentroidTaskOutput,
    DetectorTaskOutput,
    EnricherTaskOutput,
    GeneSelectTaskOutput,
)


def test_manifest_first_collector_reads_detector_manifest(tmp_path: Path) -> None:
    out_dir = tmp_path / "detections"
    out_dir.mkdir()
    input_json = {"chromosome": "21", "context": "CG", "group": "pca1", "outputDir": str(out_dir)}
    manifest = manifest_path_for(out_dir, "pipeline.detector", "21_CG_pca1")
    atomic_write_json(
        manifest,
        {
            "schema_version": "1.0",
            "status": "ok",
            "action_name": "pipeline.detector",
            "chromosome": "21",
            "context": "CG",
            "group": "pca1",
            "output_dir": str(out_dir),
            "n_statistical_dmps": 100,
            "n_biological_dmps": 42,
            "result_code": 0,
        },
    )
    collector = ManifestFirstCollector(
        output_model=DetectorTaskOutput,
        resolve_output_dir=lambda inp: inp.get("outputDir"),
        legacy_collect=DetectorLegacyCollector(),
    )
    payload = collector.collect(input_json, action_name="pipeline.detector")
    assert payload["n_statistical_dmps"] == 100
    assert payload["manifest_path"] == str(manifest)


def test_manifest_first_collector_falls_back_to_centroid_legacy(tmp_path: Path) -> None:
    out_dir = tmp_path / "centroids"
    out_dir.mkdir()
    h5 = out_dir / "21-CG.h5"
    h5.write_bytes(b"stub")
    input_json = {
        "chromosome": "21",
        "context": "CG",
        "group": "healthy",
        "outputDir": str(out_dir),
    }
    collector = ManifestFirstCollector(
        output_model=CentroidTaskOutput,
        resolve_output_dir=lambda inp: inp.get("outputDir"),
        legacy_collect=CentroidLegacyCollector(),
    )
    payload = collector.collect(input_json, action_name="pipeline.centroid")
    assert payload["centroid_h5_path"] == str(h5)
    assert payload.get("manifest_path") is None


def test_enricher_legacy_collector_reads_completeness_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    root = tmp_path / "prod" / "enricher"
    root.mkdir(parents=True)
    manifest_path = root / "enricher_completeness.json"
    manifest_path.write_text(
        json.dumps({"all_complete": True, "comparisons": {"healthy_vs_pca1": {"complete": True}}}),
        encoding="utf-8",
    )

    def _fake_root(_project: Path) -> Path:
        return root

    monkeypatch.setattr(
        "methyl_enricher.enricher_completeness.production_enricher_root",
        _fake_root,
    )
    collector = EnricherLegacyCollector()
    payload = collector.collect(
        {"projectPath": str(project), "comparison": "healthy_vs_pca1"},
        action_name="pipeline.enricher",
    )
    assert payload["all_complete"] is True
    assert payload["n_comparisons"] == 1


@pytest.mark.parametrize("schema_version", ["1.1", "1.2"])
def test_manifest_first_collector_ignores_worker_idempotency_envelope(
    tmp_path: Path, schema_version: str
) -> None:
    """Idempotency envelopes wrap task_output; collectors must not treat them as CLI outputs."""
    run_dir = tmp_path / "run_0001"
    stability = run_dir / "gene_stability"
    stability.mkdir(parents=True)
    (stability / "gene_featurecuts_metrics.json").write_text(
        json.dumps({"selected_k": 50, "balanced_accuracy": 0.8333}),
        encoding="utf-8",
    )
    (stability / "genes-classifier.csv").write_text("gene\n", encoding="utf-8")
    manifest = manifest_path_for(run_dir, "pipeline.gene_select", "default")
    atomic_write_json(
        manifest,
        {
            "schema_version": schema_version,
            "action_name": "pipeline.gene_select",
            "capability": "methyl-gene-select",
            "started_at_utc": "2026-06-26T00:30:27Z",
            "finished_at_utc": "2026-06-26T00:32:52Z",
            "duration_ms": 1,
            "result_code": 0,
            "exit_code": 0,
            "manifest_path": str(manifest),
            "artifacts": [],
            "action_revision": "abc",
            "input_signature": "in",
            "output_signature": "out",
            "content_key": "deadbeef",
            "hyperparam_set_id": "hpset",
            "skipped": False,
            "skip_reason": None,
            "task_output": {"selected_k": 99, "run_dir": str(run_dir)},
        },
    )
    input_json = {"projectPath": str(run_dir / "project.json"), "runDir": str(run_dir)}
    collector = ManifestFirstCollector(
        output_model=GeneSelectTaskOutput,
        resolve_output_dir=lambda inp: inp.get("runDir"),
        legacy_collect=GeneSelectLegacyCollector(),
    )
    payload = collector.collect(input_json, action_name="pipeline.gene_select")
    assert payload["selected_k"] == 50
    assert payload.get("manifest_path") is None
