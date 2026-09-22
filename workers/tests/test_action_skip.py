"""Tests for signature-based action skip/replay."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from methyl_domain.action_result import ActionExecutionRecord, atomic_write_action_result, manifest_path_for
from pydantic import BaseModel

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.action_execution import execution_result_from_output, validate_input
from methyl_worker.action_skip import (
    _enrich_prepare_freeze_replay_paths,
    compute_action_revision,
    compute_input_signature,
    maybe_skip_action,
    record_action_execution,
    verify_artifacts,
)
from methyl_worker.handlers import execute_task
from methyl_worker.task_validation import strip_runtime_input
from methyl_worker.task_models.validation_models import StabilitySummary, ValidationStabilityOutput


def _write_study_project(path: Path, tmp_path: Path) -> None:
    (tmp_path / "s.csv").write_text("S1\n", encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "output_base": str(tmp_path),
                "project_name": "Study",
                "chromosomes": ["1"],
                "contexts": ["CG"],
                "group1": {"label": "g1", "sample_paths": [str(tmp_path / "s.csv")]},
                "group2": {"label": "g2", "sample_paths": [str(tmp_path / "s.csv")]},
            }
        ),
        encoding="utf-8",
    )


def test_compute_input_signature_stable_for_same_payload() -> None:
    entry = find_catalog_entry("validation.stability")
    assert entry is not None

    class FakeInput(BaseModel):
        projectPath: str

    model = FakeInput(projectPath="/work/p/project.json")
    payload = {"projectPath": "/work/p/project.json"}
    assert compute_input_signature(entry, payload, model) == compute_input_signature(
        entry, payload, model
    )


def test_verify_artifacts_detects_size_change(tmp_path: Path) -> None:
    from methyl_domain.action_result import ArtifactRef

    path = tmp_path / "out.json"
    path.write_text("{}", encoding="utf-8")
    ref = ArtifactRef(path=str(path), bytes=2)
    assert verify_artifacts([ref]) is True
    path.write_text("{}\n", encoding="utf-8")
    assert verify_artifacts([ref]) is False


def test_verify_artifacts_ignores_action_result_envelope_size_drift(tmp_path: Path) -> None:
    from methyl_domain.action_result import ArtifactRef

    envelope = tmp_path / ".action_results" / "pipeline_centroid.1_CG_all.json"
    envelope.parent.mkdir(parents=True)
    envelope.write_text("{}", encoding="utf-8")
    h5 = tmp_path / "1-CG.h5"
    h5.write_bytes(b"x" * 10)
    refs = [
        ArtifactRef(path=str(envelope), bytes=2),
        ArtifactRef(path=str(h5), bytes=10),
    ]
    envelope.write_text('{"schema_version": "1.1", "grown": true}', encoding="utf-8")
    assert verify_artifacts(refs) is True


def test_maybe_skip_replays_when_manifest_matches(tmp_path: Path) -> None:
    entry = find_catalog_entry("validation.stability")
    assert entry is not None

    mc_root = tmp_path / "monte_carlo_runs"
    stability_dir = mc_root / "stability"
    stability_dir.mkdir(parents=True)
    summary_path = stability_dir / "stability_summary.json"
    summary_path.write_text("{}", encoding="utf-8")

    project = tmp_path / "configs" / "project.json"
    _write_study_project(project, tmp_path)

    input_json = {
        "projectPath": str(project),
        "monteCarloRunsRoot": str(mc_root),
        "outputDir": str(stability_dir),
        "resolvedConfig": {"n_iterations": 2, "stability_dmp_freq": 0.7},
    }

    input_model = validate_input(entry, strip_runtime_input(input_json))
    output = ValidationStabilityOutput(
        status="ok",
        outputDir=str(stability_dir),
        summary=StabilitySummary(summary_json_path=str(summary_path), n_iterations=2),
    )
    record_action_execution(entry, input_json, input_model, execution_result_from_output(output))

    assert manifest_path_for(stability_dir, entry.action_name, "default").is_file()
    skipped = maybe_skip_action(entry, input_json)
    assert skipped is not None
    assert skipped.output.status == "skipped"


def test_maybe_skip_works_with_workflow_node_key(tmp_path: Path) -> None:
    """workflowNodeKey is runtime-only; must not break validate_input inside maybe_skip_action."""
    entry = find_catalog_entry("validation.stability")
    assert entry is not None

    mc_root = tmp_path / "monte_carlo_runs"
    stability_dir = mc_root / "stability"
    stability_dir.mkdir(parents=True)
    summary_path = stability_dir / "stability_summary.json"
    summary_path.write_text("{}", encoding="utf-8")

    project = tmp_path / "project.json"
    _write_study_project(project, tmp_path)

    task_input = {
        "projectPath": str(project),
        "monteCarloRunsRoot": str(mc_root),
        "outputDir": str(stability_dir),
        "resolvedConfig": {"n_iterations": 1},
    }
    input_model = validate_input(entry, strip_runtime_input(task_input))
    output = ValidationStabilityOutput(
        status="ok",
        outputDir=str(stability_dir),
        summary=StabilitySummary(summary_json_path=str(summary_path), n_iterations=1),
    )
    record_action_execution(entry, task_input, input_model, execution_result_from_output(output))

    skip_input = {**task_input, "workflowNodeKey": "stability", "forceRerun": False}
    skipped = maybe_skip_action(entry, skip_input)
    assert skipped is not None
    assert skipped.output.status == "skipped"


def test_force_rerun_bypasses_skip(tmp_path: Path) -> None:
    entry = find_catalog_entry("validation.stability")
    assert entry is not None
    stability_dir = tmp_path / "stability"
    stability_dir.mkdir()
    input_json = {
        "projectPath": str(tmp_path / "missing.json"),
        "outputDir": str(stability_dir),
        "forceRerun": True,
    }
    assert maybe_skip_action(entry, input_json) is None


def test_plan_iterations_skip_blocked_by_legacy_mc_run_project(tmp_path: Path) -> None:
    from methyl_worker.task_models.validation_models import ValidationPlanTaskOutput

    entry = find_catalog_entry("validation.plan_iterations")
    assert entry is not None

    mc_root = tmp_path / "Study" / "monte_carlo_runs"
    legacy_run = mc_root / "run_0002"
    legacy_run.mkdir(parents=True)
    (legacy_run / "project.json").write_text(
        json.dumps({"project_name": "run_0002", "step_config": {"detection": {"alpha": 0.05}}}),
        encoding="utf-8",
    )

    project = tmp_path / "configs" / "project.json"
    _write_study_project(project, tmp_path)

    input_json = {
        "projectPath": str(project),
        "featureIterations": 10,
        "resolvedConfig": {"n_iterations": 10},
    }
    input_model = validate_input(entry, strip_runtime_input(input_json))
    output = ValidationPlanTaskOutput(status="ok", n_iterations=10, iterations=[])
    record_action_execution(entry, input_json, input_model, execution_result_from_output(output))

    assert manifest_path_for(mc_root, entry.action_name, "default").is_file()
    skipped = maybe_skip_action(entry, input_json)
    assert skipped is None


def test_centroid_skip_blocked_when_manifest_has_no_artifacts(tmp_path: Path) -> None:
    """A centroid manifest with result_code 0 but empty artifacts must not replay as skipped."""
    from methyl_worker.task_models.pipeline_models import CentroidTaskOutput

    entry = find_catalog_entry("pipeline.centroid")
    assert entry is not None

    out_dir = tmp_path / "monte_carlo_runs" / "run_0001" / "centroids" / "controls" / "healthy" / "all"
    out_dir.mkdir(parents=True)

    input_json = {
        "tool": "methyl-centroid",
        "projectPath": str(tmp_path / "project.json"),
        "group": "all",
        "chromosome": "1",
        "context": "CG",
        "outputDir": str(out_dir),
    }
    input_model = validate_input(entry, strip_runtime_input(input_json))
    # Simulate the false-success case: no centroid_h5_path -> no artifacts recorded.
    output = CentroidTaskOutput(status="ok", output_dir=str(out_dir), centroid_h5_path=None)
    record_action_execution(entry, input_json, input_model, execution_result_from_output(output))

    assert manifest_path_for(out_dir, entry.action_name, "1_CG_all").is_file()
    assert maybe_skip_action(entry, input_json) is None


def test_centroid_skip_replays_when_hdf5_artifact_present(tmp_path: Path) -> None:
    """With a real HDF5 recorded, centroid skip/replay proceeds normally."""
    from methyl_worker.task_models.pipeline_models import CentroidTaskOutput

    entry = find_catalog_entry("pipeline.centroid")
    assert entry is not None

    out_dir = tmp_path / "monte_carlo_runs" / "run_0001" / "centroids" / "controls" / "healthy" / "all"
    out_dir.mkdir(parents=True)
    h5 = out_dir / "1-CG.h5"
    h5.write_bytes(b"centroid")

    input_json = {
        "tool": "methyl-centroid",
        "projectPath": str(tmp_path / "project.json"),
        "group": "all",
        "chromosome": "1",
        "context": "CG",
        "outputDir": str(out_dir),
    }
    input_model = validate_input(entry, strip_runtime_input(input_json))
    output = CentroidTaskOutput(status="ok", output_dir=str(out_dir), centroid_h5_path=str(h5))
    record_action_execution(entry, input_json, input_model, execution_result_from_output(output))

    skipped = maybe_skip_action(entry, input_json)
    assert skipped is not None
    assert skipped.output.status == "skipped"


def test_normalize_task_input_preserves_force_rerun() -> None:
    from methyl_worker.task_validation import normalize_task_input

    normalized = normalize_task_input(
        "validation.plan_iterations",
        "validation.plan-iterations",
        {
            "projectPath": "/work/p/project.json",
            "featureIterations": 10,
            "forceRerun": True,
            "workflowNodeKey": "plan_iterations",
            "extraTemplateField": "dropped",
        },
    )
    assert normalized.get("forceRerun") is True
    assert normalized.get("workflowNodeKey") == "plan_iterations"
    assert "extraTemplateField" not in normalized


def test_execute_task_honors_force_rerun_after_normalize(tmp_path: Path, monkeypatch) -> None:
    """Engine path: normalize_task_input must not strip forceRerun before maybe_skip_action."""
    from methyl_worker.task_validation import normalize_task_input

    entry = find_catalog_entry("validation.stability")
    assert entry is not None
    stability_dir = tmp_path / "stability"
    stability_dir.mkdir(parents=True)
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    revision = compute_action_revision(entry)
    record = ActionExecutionRecord(
        action_name=entry.action_name,
        capability=entry.capability,
        started_at_utc=datetime.now(timezone.utc).replace(microsecond=0),
        finished_at_utc=datetime.now(timezone.utc).replace(microsecond=0),
        duration_ms=1,
        action_revision=revision,
        input_signature="sig",
        output_signature="outsig",
        task_output={"status": "ok", "outputDir": str(stability_dir), "summary": {}},
        artifacts=[],
    )
    atomic_write_action_result(
        manifest_path_for(stability_dir, entry.action_name, "default"),
        record,
    )

    raw = {
        "projectPath": str(project),
        "monteCarloRunsRoot": str(tmp_path / "mc"),
        "outputDir": str(stability_dir),
        "forceRerun": True,
    }
    normalized = normalize_task_input(entry.action_name, entry.capability, raw)
    assert normalized.get("forceRerun") is True

    run_calls: list = []

    class FakeAction:
        def execute(self, _input_json, *, handle=None):
            run_calls.append(1)
            return execution_result_from_output(
                ValidationStabilityOutput(
                    status="ok",
                    outputDir=str(stability_dir),
                    summary=StabilitySummary(),
                )
            )

    monkeypatch.setattr(
        "methyl_worker.handlers.build_action_from_catalog",
        lambda _entry, _mod: FakeAction(),
    )

    result = execute_task(entry.capability, entry.action_name, normalized)
    assert run_calls, "forceRerun should bypass skip and execute handler"
    assert result.output.status == "ok"


def test_execute_task_skips_validation_stability_when_manifest_exists(tmp_path: Path, monkeypatch) -> None:
    entry = find_catalog_entry("validation.stability")
    assert entry is not None
    mc_root = tmp_path / "monte_carlo_runs"
    stability_dir = mc_root / "stability"
    stability_dir.mkdir(parents=True)
    summary_path = stability_dir / "stability_summary.json"
    summary_path.write_text("{}", encoding="utf-8")

    project = tmp_path / "project.json"
    _write_study_project(project, tmp_path)

    revision = compute_action_revision(entry)
    input_sig = "abc123"
    output_sig = "def456"
    started = datetime.now(timezone.utc).replace(microsecond=0)
    record = ActionExecutionRecord(
        action_name=entry.action_name,
        capability=entry.capability,
        started_at_utc=started,
        finished_at_utc=started,
        duration_ms=1,
        action_revision=revision,
        input_signature=input_sig,
        output_signature=output_sig,
        task_output={
            "status": "ok",
            "outputDir": str(stability_dir),
            "summary": {"summary_json_path": str(summary_path), "n_iterations": 1},
        },
        artifacts=[],
    )
    manifest = manifest_path_for(stability_dir, entry.action_name, "default")
    atomic_write_action_result(manifest, record)

    monkeypatch.setattr(
        "methyl_worker.action_skip.compute_input_signature",
        lambda *_args, **_kwargs: input_sig,
    )
    monkeypatch.setattr(
        "methyl_worker.action_skip.compute_output_signature",
        lambda *_args, **_kwargs: output_sig,
    )
    monkeypatch.setattr("methyl_worker.action_skip.verify_artifacts", lambda _arts: True)

    result = execute_task(
        entry.capability,
        entry.action_name,
        {
            "projectPath": str(project),
            "monteCarloRunsRoot": str(mc_root),
            "outputDir": str(stability_dir),
            "resolvedConfig": {"n_iterations": 1},
        },
    )
    assert result.output.status == "skipped"


def _write_shorthand_production_project(path: Path, tmp_path: Path) -> None:
    """Minimal control/disease project with comparisons shorthand (not an explicit list)."""
    (tmp_path / "healthy.csv").write_text("S1\n", encoding="utf-8")
    (tmp_path / "mci.csv").write_text("S2\n", encoding="utf-8")
    (tmp_path / "ad.csv").write_text("S3\n", encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "project_name": "Healthy_vs_AD_Stages",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": ["healthy.csv"]}],
                },
                "diseases": {
                    "label": "AD",
                    "groups": [
                        {
                            "label": "AD",
                            "stages": [
                                {"label": "MCI", "sample_paths": ["mci.csv"]},
                                {"label": "AD", "sample_paths": ["ad.csv"]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
                "chromosomes": ["1"],
                "contexts": ["CG"],
            }
        ),
        encoding="utf-8",
    )


def test_enrich_prepare_freeze_replay_paths_shorthand_comparisons(tmp_path: Path) -> None:
    """CAAS replay expands control_vs_each_disease and uses detections/{control}/{disease}."""
    from methyl_utils import load_project

    from methyl_worker.handler_helpers import production_centroid_detect_dirs

    production_project = tmp_path / "production" / "project.json"
    _write_shorthand_production_project(production_project, tmp_path)

    prod_cfg = load_project(str(production_project))
    c1, c2, det = production_centroid_detect_dirs(prod_cfg)
    assert c1 is not None and c2 is not None and det is not None

    comparisons = prod_cfg.get_comparisons()
    assert comparisons, "shorthand must expand via get_comparisons()"
    control = comparisons[0].control_group
    disease = comparisons[0].disease_group
    assert c1 == prod_cfg.get_centroid_dir("control", control)
    assert c2 == prod_cfg.get_centroid_dir("disease", disease)
    assert det == prod_cfg.get_detection_output_dir(control, disease)
    assert det.endswith(f"/detections/{control}/{disease}")
    # Regression: never the old detections/{label} layout
    assert not det.endswith(f"/detections/{disease}")

    out = _enrich_prepare_freeze_replay_paths({"projectPath": str(production_project)})
    assert out["centroid1Dir"] == c1
    assert out["centroid2Dir"] == c2
    assert out["detectOutDir"] == det


def test_production_centroid_detect_dirs_accepts_group1_group2_fallback() -> None:
    """Soft fallback when comparison objects expose legacy group1/group2 attrs."""
    from types import SimpleNamespace

    from methyl_worker.handler_helpers import production_centroid_detect_dirs

    cmp0 = SimpleNamespace(group1="healthy", group2="disease", control_group=None, disease_group=None)
    prod_cfg = SimpleNamespace(
        get_comparisons=lambda: [cmp0],
        get_centroid_dir=lambda side, label: f"/centroids/{side}/{label}",
        get_detection_output_dir=lambda c, d: f"/out/detections/{c}/{d}",
    )
    c1, c2, det = production_centroid_detect_dirs(prod_cfg)
    assert c1 == "/centroids/control/healthy"
    assert c2 == "/centroids/disease/disease"
    assert det == "/out/detections/healthy/disease"


def test_production_centroid_detect_dirs_rejects_raw_string_iteration() -> None:
    """Raw comparisons string must not be iterated as comparison objects."""
    from types import SimpleNamespace

    from methyl_worker.handler_helpers import production_centroid_detect_dirs

    # Simulate the old bug surface: comparisons is a str; get_comparisons expands.
    prod_cfg = SimpleNamespace(
        comparisons="control_vs_each_disease",
        get_comparisons=lambda: [
            SimpleNamespace(control_group="all", disease_group="MCI"),
        ],
        get_centroid_dir=lambda side, label: f"/c/{side}/{label}",
        get_detection_output_dir=lambda c, d: f"/d/{c}/{d}",
    )
    c1, c2, det = production_centroid_detect_dirs(prod_cfg)
    assert (c1, c2, det) == ("/c/control/all", "/c/disease/MCI", "/d/all/MCI")
