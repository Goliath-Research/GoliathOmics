"""goliath.application_profiles hooks the platform's neutral pipeline modules delegate to."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from methyl_worker import application_profiles as hooks

PLATFORM_HOOKS = (
    "seed_pipeline_scope_flags",
    "apply_pipeline_profile",
    "apply_pipeline_procedure",
    "validate_procedure_analyte",
    "context_modality",
    "enrich_project_context",
    "enrich_comparisons_from_project",
    "load_site_manifest",
    "PIPELINE_FLAG_DEFAULTS",
)


def test_exports_every_platform_hook() -> None:
    missing = [name for name in PLATFORM_HOOKS if not hasattr(hooks, name)]
    assert missing == []
    assert hooks.PIPELINE_FLAG_DEFAULTS


def test_scope_flags_and_modality_forward_to_omics_rules() -> None:
    omics = hooks._omics_pipeline_profiles()
    assert not hasattr(omics, "_delegate")  # never the platform's forwarding module

    context = {"primaryAnalyte": "cfdna", "regulatory": {"primary_modality": "bisulfite"}}
    assert hooks.seed_pipeline_scope_flags(context, action_config={}) == omics.seed_pipeline_scope_flags(
        context, action_config={}
    )
    assert hooks.context_modality(context) == omics.context_modality(context)


def test_refuses_to_forward_to_the_platform_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, "_pipeline_profiles_module", lambda: SimpleNamespace(_delegate=object()))
    monkeypatch.setattr(hooks, "_omics_pipeline_profiles_candidates", lambda: [])
    monkeypatch.setattr(hooks, "_OMICS_PIPELINE_PROFILES", None)
    with pytest.raises(ImportError):
        hooks.context_modality({})


def test_enrich_project_context_sets_centroid1_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    project = SimpleNamespace(
        chromosomes=["chr1"],
        contexts=None,
        get_centroid_dir=lambda role, group: f"/p/centroids/{role}/{group}",
        cohort_group_labels=lambda: ["ctrl", "dis"],
        get_regulatory_config=lambda: {"primary_analyte": "cfdna"},
    )
    monkeypatch.setattr(hooks, "_yl_utils_root", lambda: SimpleNamespace(load_project=lambda path: project))
    monkeypatch.setattr(
        hooks,
        "enrich_comparisons_from_project",
        lambda path: [{"control_group": "ctrl", "disease_group": "dis"}],
    )
    out = hooks.enrich_project_context({"projectPath": "/p", "contexts": ["CHG"]}, Path("/p/project.json"))
    assert out["centroid1Dir"] == "/p/centroids/control/ctrl"
    assert out["chromosomes"] == ["chr1"]
    assert out["contexts"] == ["CHG"]
    assert out["groups"] == [{"label": "ctrl"}, {"label": "dis"}]
    assert out["primaryAnalyte"] == "cfdna"


def test_platform_module_shadowing_ours_loads_this_repos_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    platform = SimpleNamespace(_delegate=lambda *a, **k: None, __file__=str(tmp_path / "pipeline_profiles.py"))
    monkeypatch.setattr(hooks, "_pipeline_profiles_module", lambda: platform)
    monkeypatch.setattr(hooks, "_OMICS_PIPELINE_PROFILES", None)
    omics = hooks._omics_pipeline_profiles()
    assert omics is not platform and not hasattr(omics, "_delegate")
    assert Path(omics.__file__).parts[-3:] == ("workflow_engine", "domain", "pipeline_profiles.py")
    assert hooks.context_modality({"regulatory": {"primary_modality": "bisulfite"}}) == omics.context_modality(
        {"regulatory": {"primary_modality": "bisulfite"}}
    )
