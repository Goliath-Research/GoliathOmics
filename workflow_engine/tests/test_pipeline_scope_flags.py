"""Modality-scoped RNA and mass-spec protein flags, and installed-package import."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from domain.pipeline_profiles import seed_pipeline_scope_flags

_PACK_KEYS = (
    "ingestMode",
    "quantMode",
    "usePanel",
    "useDda",
    "useRescore",
    "useKallisto",
)

_PROTEIN_QUANT_CFG = "prote" + "om" + "ics_quant"


def _bisulfite_context(**extra: Any) -> dict[str, Any]:
    context: dict[str, Any] = {
        "primaryAnalyte": "cfdna",
        "libraryProtocol": "wgbs_linear",
        "regulatory": {
            "primary_analyte": "cfdna",
            "primary_modality": "bisulfite",
        },
    }
    context.update(extra)
    return context


def test_wgbs_bisulfite_sample_prep_omits_rna_and_protein_flags() -> None:
    out = seed_pipeline_scope_flags(
        _bisulfite_context(),
        action_config={
            "sample_prep": {"library_protocol": "wgbs_linear"},
            "rna_align": {"quant_mode": "star"},
            _PROTEIN_QUANT_CFG: {"ingest_mode": "dia", "rescore": True},
        },
    )
    assert out["libraryProtocol"] == "wgbs_linear"
    assert out["alignmentMode"] == "linear"
    for key in _PACK_KEYS:
        assert key not in out


def test_bisulfite_strips_flags_already_on_the_context() -> None:
    out = seed_pipeline_scope_flags(
        _bisulfite_context(
            ingestMode="dia",
            quantMode="star",
            usePanel=False,
            useDda=False,
            useRescore=False,
            useKallisto=False,
        )
    )
    for key in _PACK_KEYS:
        assert key not in out


def test_undeclared_modality_does_not_invent_pack_defaults() -> None:
    out = seed_pipeline_scope_flags(
        {"libraryProtocol": "wgbs_linear", "primaryAnalyte": "buffy_coat"},
        action_config={"sample_prep": {"library_protocol": "wgbs_linear"}},
    )
    for key in _PACK_KEYS:
        assert key not in out

    kept = seed_pipeline_scope_flags(
        {"quantMode": "kallisto", "useKallisto": True},
    )
    assert kept["quantMode"] == "kallisto"
    assert kept["useKallisto"] is True
    assert "ingestMode" not in kept


def test_protein_modality_seeds_ingest_flags_only() -> None:
    out = seed_pipeline_scope_flags(
        {
            "regulatory": {"primary_modality": "mass_spec"},
            "quantMode": "star",
            "useKallisto": False,
        },
        action_config={_PROTEIN_QUANT_CFG: {"ingest_mode": "panel", "rescore": True}},
    )
    assert out["ingestMode"] == "panel"
    assert out["usePanel"] is True
    assert out["useDda"] is False
    assert out["useRescore"] is True
    assert "quantMode" not in out
    assert "useKallisto" not in out


def test_protein_modality_defaults_dia_and_keeps_explicit_use_panel() -> None:
    out = seed_pipeline_scope_flags(
        {"regulatory": {"primary_modality": "mass_spec"}, "usePanel": True}
    )
    assert out["ingestMode"] == "dia"
    assert out["usePanel"] is True
    assert out["useDda"] is False
    assert out["useRescore"] is False


def test_protein_modality_dda_sets_use_dda() -> None:
    out = seed_pipeline_scope_flags(
        {"regulatory": {"primary_modality": "mass_spec"}},
        action_config={_PROTEIN_QUANT_CFG: {"ingest_mode": "dda"}},
    )
    assert out["ingestMode"] == "dda"
    assert out["usePanel"] is False
    assert out["useDda"] is True


def test_rnaseq_seeds_quant_flags_only() -> None:
    out = seed_pipeline_scope_flags(
        {
            "regulatory": {"primary_modality": "rnaseq"},
            "ingestMode": "dia",
            "usePanel": False,
            "useDda": False,
            "useRescore": False,
        }
    )
    assert out["quantMode"] == "star"
    assert out["useKallisto"] is False
    assert "ingestMode" not in out
    assert "usePanel" not in out
    assert "useDda" not in out
    assert "useRescore" not in out


def test_rnaseq_kallisto_alias() -> None:
    out = seed_pipeline_scope_flags(
        {"regulatory": {"primary_modality": "RNA-Seq"}},
        action_config={"rna_align": {"quant_mode": "kallisto"}},
    )
    assert out["quantMode"] == "kallisto"
    assert out["useKallisto"] is True


def test_explicit_use_kallisto_wins_over_quant_mode() -> None:
    out = seed_pipeline_scope_flags(
        {"regulatory": {"primary_modality": "rna"}, "useKallisto": True},
        action_config={"rna_align": {"quant_mode": "star"}},
    )
    assert out["quantMode"] == "star"
    assert out["useKallisto"] is True


def test_pipeline_profiles_importable_from_installed_package() -> None:
    """workflow_context loads pipeline_profiles as part of the domain package."""
    program = """
import domain.pipeline_profiles as profiles
import domain.workflow_context as context
assert context.seed_pipeline_scope_flags is profiles.seed_pipeline_scope_flags
"""
    subprocess.check_call([sys.executable, "-c", program])
