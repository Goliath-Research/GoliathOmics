"""Tests for methyl_domain tagged types and schema export."""

from __future__ import annotations

import json

import pytest

from methyl_domain.helpers import (
    MethylDetectionRef_from_detector_output,
    MethylGroup_from_project,
    build_stratified_cohort_draw,
    groups_from_mc_run_dir,
)
from methyl_domain.schema_export import check_domain_schema_drift, export_all_domain_schemas
from methyl_domain.types import (
    DOMAIN_MODEL_BY_TYPE,
    DOMAIN_TYPE_NAMES,
    ExtractionQcRef,
    MethylGroup,
    MethylSampleRef,
    parse_domain_value,
    to_tagged_json,
)


def test_domain_type_names_match_model_registry():
    assert set(DOMAIN_TYPE_NAMES) == set(DOMAIN_MODEL_BY_TYPE)


def test_extraction_qc_ref_parse_roundtrip():
    payload = {
        "$type": "ExtractionQcRef",
        "qcPath": "/work/qc/extraction.json",
        "overallPass": True,
    }
    parsed = parse_domain_value(payload)
    assert isinstance(parsed, ExtractionQcRef)
    assert parsed.qcPath == "/work/qc/extraction.json"
    assert parsed.overallPass is True


def test_tagged_json_roundtrip():
    sample = MethylSampleRef(sampleId="S1", sampleDir="/work/samples/S1")
    payload = to_tagged_json(sample)
    assert payload["$type"] == "MethylSampleRef"
    parsed = parse_domain_value(payload)
    assert isinstance(parsed, MethylSampleRef)
    assert parsed.sampleId == "S1"


def test_stratified_cohort_draw_has_type():
    draw = build_stratified_cohort_draw(
        run_id="feature_run_0001",
        phase="feature",
        project_path="/work/run/project.json",
        groups=[
            MethylGroup(label="healthy", role="control", trainCsv="/t.csv", count=2)
        ],
        comparisons=[],
        seed=42,
        train_fraction=0.8,
        task_config={
            "runId": "feature_run_0001",
            "phase": "feature",
            "iteration": 0,
            "layout": "binary",
            "trainFraction": 0.8,
            "seed": 42,
            "projectJson": "/work/run/project.json",
            "runDir": "/work/run",
            "monteCarloRunsRoot": "/work/run/monte_carlo_runs",
        },
    )
    assert draw["$type"] == "StratifiedCohortDraw"
    assert draw["runId"] == "feature_run_0001"
    assert draw["taskConfig"]["runId"] == "feature_run_0001"
    assert draw["groups"][0]["$type"] == "MethylGroup"


def test_groups_from_mc_run_dir_binary(tmp_path):
    run_dir = tmp_path / "run_0001"
    run_dir.mkdir()
    (run_dir / "train_control.csv").write_text("sample\nA\nB\n", encoding="utf-8")
    (run_dir / "val_control.csv").write_text("sample\nC\n", encoding="utf-8")
    (run_dir / "train_disease.csv").write_text("sample\nD\n", encoding="utf-8")
    (run_dir / "val_disease.csv").write_text("sample\nE\nF\n", encoding="utf-8")
    project = {
        "controls": {"label": "healthy", "groups": [{"label": "healthy"}]},
        "diseases": {"label": "PCa", "groups": [{"label": "PCa"}]},
        "comparisons": [{"control_group": "healthy", "disease_group": "PCa"}],
    }
    project_path = run_dir / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    groups = groups_from_mc_run_dir(run_dir, project_path, layout="binary")
    assert len(groups) == 2
    assert groups[0].label == "healthy"
    assert groups[0].role == "control"
    assert groups[0].count == 2
    assert groups[1].label == "PCa"


def test_detection_ref_from_detector_output():
    ref = MethylDetectionRef_from_detector_output(
        {"nDmps": 120, "dmpCsvPath": "/work/dmps.csv"},
        comparison_label="healthy_vs_PCa",
        control_group="healthy",
        disease_group="PCa",
    )
    payload = to_tagged_json(ref)
    assert payload["$type"] == "MethylDetectionRef"
    assert payload["nDmps"] == 120


def test_domain_schema_export_and_drift_check(tmp_path):
    export_all_domain_schemas(schemas_root=tmp_path, write=True)
    assert check_domain_schema_drift(schemas_root=tmp_path) == []
    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert "MethylSampleRef" in registry["types"]
    assert (tmp_path / "domain_program.schema.json").is_file()


def test_committed_domain_schemas_match_repo() -> None:
    """Committed schemas/domain/* must match in-code domain models."""
    drift = check_domain_schema_drift()
    assert drift == [], "\n".join(drift)
