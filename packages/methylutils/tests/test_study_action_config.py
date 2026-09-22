"""Study Guardrails overlay is a typed editor surface, not a loose JSON bag."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from methyl_alignment_qc.models.config import AlignmentQCConfig, CoreGuardrailsConfig
from methyl_extraction_qc.models.config import ExtractionQCConfig
from methyl_utils.action_config_resolver import deep_merge
from methyl_utils.study_action_config import (
    FULL_SCHEMA_ID,
    OVERLAY_SCHEMA_ID,
    SCHEMA_ID,
    SamplePrepGuardrails,
    StudyActionConfigOverlay,
    assert_full_core_window,
    compose_guardrails,
    compose_study_guardrails,
    overlay_from_edited_effective,
    published_sample_prep_guardrails,
    site_action_config_from_edited_full,
    slice_guardrails,
    sparse_overlay_diff,
)
from methyl_validation.schema_export import generate_schema_dict


def test_overlay_accepts_empty_object():
    assert StudyActionConfigOverlay.model_validate({}).model_dump(exclude_none=True) == {}


def test_overlay_rejects_workflow_identity_fields():
    with pytest.raises(ValidationError):
        StudyActionConfigOverlay.model_validate(
            {"alignment_qc": {"sample_paths": ["/work/samples/S001"], "output_dir": "/tmp"}}
        )
    with pytest.raises(ValidationError):
        StudyActionConfigOverlay.model_validate(
            {"extraction_qc": {"sample_paths": ["/work/samples/S001"]}}
        )


def test_overlay_rejects_unknown_action_keys():
    with pytest.raises(ValidationError):
        StudyActionConfigOverlay.model_validate({"validation": {"stability_dmp_freq": 0.5}})


def test_overlay_knobs_have_no_baked_numeric_defaults():
    dumped = StudyActionConfigOverlay.model_validate(
        {"alignment_qc": {"core_guardrails": {}}}
    ).model_dump()
    core = dumped["alignment_qc"]["core_guardrails"]
    assert core["median_insert_min_bp"] is None
    assert core["max_gc_dropout"] is None
    published = CoreGuardrailsConfig().model_dump()
    assert published["median_insert_min_bp"] == 150
    assert published["max_gc_dropout"] == 5.0


def test_full_alignment_qc_schema_is_not_the_editor_schema():
    overlay = generate_schema_dict(StudyActionConfigOverlay, title="StudyActionConfigOverlay")
    full = generate_schema_dict(AlignmentQCConfig, title="AlignmentQCConfig")
    extraction = generate_schema_dict(ExtractionQCConfig, title="ExtractionQCConfig")

    assert overlay.get("required", []) == []
    assert "sample_paths" not in overlay["properties"]
    assert "output_dir" not in overlay["properties"]
    assert set(overlay["properties"]) == {"alignment_qc", "extraction_qc"}

    aq = overlay["$defs"]["AlignmentQcOverlay"]["properties"]
    assert "sample_paths" not in aq
    assert "output_dir" not in aq
    assert "genome_fasta" not in aq
    assert "core_guardrails" in aq

    core = overlay["$defs"]["CoreGuardrailsOverlay"]["properties"]["median_insert_min_bp"]
    assert core.get("default") is None
    int_schema = next(item for item in core["anyOf"] if item.get("type") == "integer")
    assert int_schema.get("minimum") == 1

    assert "sample_paths" in full.get("required", [])
    assert "output_dir" in full.get("required", [])
    assert "sample_paths" in extraction["properties"]


def test_slice_guardrails_strips_identity_and_other_actions():
    sliced = slice_guardrails(
        {
            "alignment_qc": {
                "sample_paths": ["/work/samples/S001"],
                "output_dir": "/tmp/qc",
                "genome_fasta": "/work/genomes/hg38.fa",
                "core_guardrails": {"median_insert_min_bp": 140},
            },
            "extraction_qc": {
                "sample_paths": ["/work/samples/S001"],
                "guardrails": {"max_discard_fraction": 0.8},
            },
            "validation": {"stability_dmp_freq": 0.5},
        }
    )
    assert sliced == {
        "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140}},
        "extraction_qc": {"guardrails": {"max_discard_fraction": 0.8}},
    }


def test_unchanged_effective_yields_empty_overlay():
    inherited = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}}
    assert sparse_overlay_diff(inherited, inherited) == {}


def test_changed_leaf_only_is_the_overlay():
    inherited = {
        "alignment_qc": {
            "core_guardrails": {"median_insert_min_bp": 150, "max_gc_dropout": 5.0}
        }
    }
    edited = {
        "alignment_qc": {
            "core_guardrails": {"median_insert_min_bp": 150, "max_gc_dropout": 6.0}
        }
    }
    assert sparse_overlay_diff(inherited, edited) == {
        "alignment_qc": {"core_guardrails": {"max_gc_dropout": 6.0}}
    }


def test_cleared_inherited_leaf_emits_null():
    inherited = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}}
    edited = {"alignment_qc": {}}
    overlay = sparse_overlay_diff(inherited, edited)
    assert overlay == {"alignment_qc": {"core_guardrails": None}}
    assert "core_guardrails" not in deep_merge(inherited, overlay)["alignment_qc"]


def test_sparse_diff_roundtrip_merge():
    inherited = {
        "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150, "max_gc_dropout": 5.0}},
        "extraction_qc": {"guardrails": {"max_discard_fraction": 0.9}},
    }
    edited = {
        "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140, "max_gc_dropout": 5.0}},
        "extraction_qc": {"guardrails": {"max_discard_fraction": 0.9}},
    }
    overlay = sparse_overlay_diff(inherited, edited)
    assert deep_merge(inherited, overlay) == edited


def test_compose_shows_inherited_values_not_empty_overlay():
    view = compose_study_guardrails(
        site_action_config={
            "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150, "max_gc_dropout": 5.0}}
        },
        profile_action_config={"alignment_qc": {}},
        study_overlay={},
    )
    assert view["schema_id"] == SCHEMA_ID
    assert view["overlay"] == {}
    assert view["effective"]["alignment_qc"]["core_guardrails"]["median_insert_min_bp"] == 150
    assert view["inherited"] == view["effective"]


def test_compose_later_layer_wins_and_null_clears():
    view = compose_study_guardrails(
        site_action_config={"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}},
        profile_action_config={"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 160}}},
        procedure_action_config={"alignment_qc": {"core_guardrails": {"max_gc_dropout": 6.0}}},
        study_overlay={"alignment_qc": {"core_guardrails": {"median_insert_min_bp": None}}},
    )
    core = view["effective"]["alignment_qc"]["core_guardrails"]
    assert "median_insert_min_bp" not in core
    assert core["max_gc_dropout"] == 6.0


def test_overlay_from_edited_preserves_non_guardrail_keys():
    inherited = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}}
    edited = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140}}}
    stored = overlay_from_edited_effective(
        inherited=inherited,
        edited_effective=edited,
        existing_action_config={
            "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 155}},
            "validation": {"stability_dmp_freq": 0.5},
        },
    )
    assert stored["validation"] == {"stability_dmp_freq": 0.5}
    assert stored["alignment_qc"] == {"core_guardrails": {"median_insert_min_bp": 140}}


def test_overlay_from_edited_unchanged_drops_guardrail_keys():
    inherited = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}}
    stored = overlay_from_edited_effective(
        inherited=inherited,
        edited_effective=inherited,
        existing_action_config={
            "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140}},
            "validation": {"stability_dmp_freq": 0.5},
        },
    )
    assert "alignment_qc" not in stored
    assert stored == {"validation": {"stability_dmp_freq": 0.5}}


def test_full_model_has_published_window_and_no_identity():
    dumped = SamplePrepGuardrails().model_dump()
    core = dumped["alignment_qc"]["core_guardrails"]
    assert core["median_insert_min_bp"] == 150
    assert core["max_gc_dropout"] == 5.0
    assert dumped["extraction_qc"]["guardrails"]["max_discard_fraction"] == 0.9
    assert "sample_paths" not in dumped["alignment_qc"]
    assert "output_dir" not in dumped["alignment_qc"]
    published = published_sample_prep_guardrails()
    assert published["alignment_qc"]["core_guardrails"]["min_pf_percent"] == 90.0
    with pytest.raises(ValidationError):
        SamplePrepGuardrails.model_validate(
            {"alignment_qc": {"sample_paths": ["/work/samples/S001"]}}
        )


def test_full_schema_is_not_alignment_qc_runtime():
    full = generate_schema_dict(SamplePrepGuardrails, title="SamplePrepGuardrails")
    runtime = generate_schema_dict(AlignmentQCConfig, title="AlignmentQCConfig")
    assert set(full["properties"]) == {"alignment_qc", "extraction_qc"}
    assert "sample_paths" not in full["$defs"]["AlignmentQcFull"]["properties"]
    core = full["$defs"]["CoreGuardrailsFull"]["properties"]["median_insert_min_bp"]
    assert core.get("default") == 150
    assert "sample_paths" in runtime.get("required", [])


def test_assert_full_core_window_rejects_partial():
    with pytest.raises(ValueError, match="full published window"):
        assert_full_core_window({"alignment_qc": {"core_guardrails": {"max_gc_dropout": 6.0}}})
    assert_full_core_window(published_sample_prep_guardrails())


def test_site_action_config_from_edited_preserves_other_keys():
    stored = site_action_config_from_edited_full(
        edited_effective=published_sample_prep_guardrails(),
        existing_action_config={
            "alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140}},
            "validation": {"stability_dmp_freq": 0.5},
        },
    )
    assert stored["validation"] == {"stability_dmp_freq": 0.5}
    assert stored["alignment_qc"]["core_guardrails"]["median_insert_min_bp"] == 150
    with pytest.raises(ValueError, match="full published window"):
        site_action_config_from_edited_full(
            edited_effective={"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 150}}},
            existing_action_config={},
        )


def test_compose_guardrails_layers_and_schema_ids():
    site = published_sample_prep_guardrails()
    profile = {"alignment_qc": {"core_guardrails": {"max_gc_dropout": 6.0}}}
    procedure = {"alignment_qc": {"cycle_screening": {"fallback_trim_front": 5}}}
    study = {"alignment_qc": {"core_guardrails": {"median_insert_min_bp": 140}}}

    site_view = compose_guardrails(site_action_config=site, through_layer="site")
    assert site_view["schema_id"] == FULL_SCHEMA_ID
    assert site_view["inherited"] == {}
    assert site_view["effective"]["alignment_qc"]["core_guardrails"]["median_insert_min_bp"] == 150

    profile_view = compose_guardrails(
        site_action_config=site, profile_action_config=profile, through_layer="profile"
    )
    assert profile_view["schema_id"] == OVERLAY_SCHEMA_ID
    assert profile_view["effective"]["alignment_qc"]["core_guardrails"]["max_gc_dropout"] == 6.0
    assert profile_view["effective"]["alignment_qc"]["core_guardrails"]["median_insert_min_bp"] == 150

    proc_view = compose_guardrails(
        site_action_config=site,
        profile_action_config=profile,
        procedure_action_config=procedure,
        through_layer="procedure",
    )
    assert proc_view["effective"]["alignment_qc"]["cycle_screening"]["fallback_trim_front"] == 5

    study_view = compose_guardrails(
        site_action_config=site,
        profile_action_config=profile,
        procedure_action_config=procedure,
        study_overlay=study,
        through_layer="study",
    )
    assert study_view["schema_id"] == SCHEMA_ID
    assert study_view["effective"]["alignment_qc"]["core_guardrails"]["median_insert_min_bp"] == 140
