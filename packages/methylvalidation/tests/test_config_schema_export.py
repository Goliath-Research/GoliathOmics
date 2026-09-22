"""Drift checks for committed pipeline config JSON Schema artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_validation.config_schema_registry import list_config_schema_specs
from methyl_validation.schema_export import (
    check_config_schema_drift,
    export_all_config_schemas,
    repo_schemas_config_dir,
    schema_to_canonical_json,
    generate_schema_dict,
)


def test_committed_config_schemas_match_pydantic_models():
    drift = check_config_schema_drift()
    assert drift == [], "Schema drift:\n" + "\n".join(drift)


def test_schema_export_is_deterministic(tmp_path: Path):
    root = tmp_path / "schemas"
    export_all_config_schemas(schemas_root=root, write=True)
    first_files = sorted(root.rglob("*.schema.json"))
    first = {p.read_text(encoding="utf-8") for p in first_files}
    export_all_config_schemas(schemas_root=root, write=True)
    second = {
        p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.schema.json"))
    }
    assert first == second
    assert len(first_files) == len(list_config_schema_specs())


def test_generate_schema_dict_sets_json_schema_meta():
    from methyl_validation.config import RegulatoryLifecycleConfig

    schema = generate_schema_dict(RegulatoryLifecycleConfig, title="RegulatoryLifecycleConfig")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "RegulatoryLifecycleConfig"
    text = schema_to_canonical_json(schema)
    assert text.endswith("\n")


def test_validation_step_schema_has_no_runner_required_fields():
    from pydantic import ValidationError

    from methyl_validation.config import ValidationStepConfig

    schema = generate_schema_dict(ValidationStepConfig, title="ValidationStepConfig")
    props = schema.get("properties", {})
    assert schema.get("required", []) == []
    assert "samples_base_path" not in props
    assert "base_project" not in props
    assert "train_fraction" in props
    assert "backend_profiles" in props

    # Backend knobs live under backend_profiles; constraints still propagate there.
    ecdf_bins = schema["$defs"]["EcdfBackendParams"]["properties"]["ecdf_aggregated_n_bins"]
    assert ecdf_bins.get("minimum") == 8
    assert ecdf_bins.get("maximum") == 512
    with pytest.raises(ValidationError):
        ValidationStepConfig.model_validate(
            {
                "backend_profiles": {
                    "ecdf": {"enabled": True, "params": {"ecdf_aggregated_n_bins": 3}},
                    "tabular_sklearn": {"enabled": False, "params": {}},
                    "generative_hybrid": {"enabled": False, "params": {}},
                }
            }
        )


def test_methyl_extract_step_config_documents_chrom_parallel():
    from methyl_utils.methyl_extract_config import MethylExtractStepConfig

    cfg = MethylExtractStepConfig(chrom_parallel=2, max_rss_gb=32, threads=10)
    dumped = cfg.model_dump(exclude_none=True)
    assert dumped["chrom_parallel"] == 2
    assert dumped["max_rss_gb"] == 32
    assert dumped["threads"] == 10


def test_progression_step_schema_artifact_registered():
    root = repo_schemas_config_dir()
    path = root / "progression.schema.json"
    assert path.is_file(), "run methyl-export-config-schemas to create progression.schema.json"
    import json

    schema = json.loads(path.read_text(encoding="utf-8"))
    assert schema["title"] == "ProgressionStepConfig"
    assert "enabled" in schema.get("properties", {})


def test_check_mode_fails_on_stale_artifact(tmp_path: Path):
    root = tmp_path / "schemas"
    export_all_config_schemas(schemas_root=root, write=True)
    specs = list_config_schema_specs()
    target = root / specs[0].filename
    target.write_text('{"stale": true}\n', encoding="utf-8")
    drift = check_config_schema_drift(schemas_root=root)
    assert any("stale schema artifact" in msg for msg in drift)


def test_study_action_config_overlay_schema_is_the_guardrails_editor():
    from methyl_alignment_qc.models.config import AlignmentQCConfig
    from methyl_utils.study_action_config import (
        FULL_SCHEMA_ID,
        OVERLAY_SCHEMA_ID,
        SCHEMA_ID,
        SamplePrepGuardrails,
        StudyActionConfigOverlay,
    )

    overlay = generate_schema_dict(StudyActionConfigOverlay, title="StudyActionConfigOverlay")
    full = generate_schema_dict(AlignmentQCConfig, title="AlignmentQCConfig")
    site = generate_schema_dict(SamplePrepGuardrails, title="SamplePrepGuardrails")
    assert overlay.get("required", []) == []
    assert set(overlay["properties"]) == {"alignment_qc", "extraction_qc"}
    assert "sample_paths" not in overlay["$defs"]["AlignmentQcOverlay"]["properties"]
    assert overlay["$defs"]["CoreGuardrailsOverlay"]["properties"]["median_insert_min_bp"].get(
        "default"
    ) is None
    assert "sample_paths" in full.get("required", [])
    assert site["$defs"]["CoreGuardrailsFull"]["properties"]["median_insert_min_bp"].get("default") == 150
    specs = {spec.schema_id: spec for spec in list_config_schema_specs()}
    assert SCHEMA_ID in specs
    assert specs[SCHEMA_ID].filename == "study_action_config_overlay.schema.json"
    assert OVERLAY_SCHEMA_ID in specs
    assert specs[OVERLAY_SCHEMA_ID].filename == "sample_prep_guardrails_overlay.schema.json"
    assert FULL_SCHEMA_ID in specs
    assert specs[FULL_SCHEMA_ID].filename == "sample_prep_guardrails.schema.json"


def test_repo_schemas_config_dir_exists_after_bootstrap():
    root = repo_schemas_config_dir()
    assert root.is_dir(), (
        f"Missing {root}; run: methyl-export-config-schemas from repo root with .venv active"
    )
    assert any(root.rglob("*.schema.json"))
