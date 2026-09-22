"""Process-pack modality pairing and study-owned analyte gates."""

from __future__ import annotations

import pytest

from methyl_utils.modality_gate import (
    enforce_pack_pairing,
    enforce_study_primary_analyte,
    infer_profile_family,
    infer_program_family,
    refuse_methyl_action_for_modality,
)


def test_infer_families() -> None:
    assert infer_program_family("samd_research.program.json") == "methylation"
    assert infer_program_family("full_lifecycle") == "methylation"
    assert infer_program_family("sample_prep_rnaseq.program.json") == "rnaseq"
    assert infer_program_family("proteomics_study_lifecycle") == "proteomics"
    assert infer_profile_family("samd_pivotal") == "methylation"
    assert infer_profile_family("rnaseq_research") == "rnaseq"


def test_enforce_pack_pairing_mismatch() -> None:
    with pytest.raises(ValueError, match="Process-pack mismatch"):
        enforce_pack_pairing(
            {
                "program_path": "rnaseq_study_lifecycle.program.json",
                "pipelineProfile": "samd_research",
            }
        )


def test_enforce_pack_pairing_ok() -> None:
    enforce_pack_pairing(
        {
            "program_path": "samd_research.program.json",
            "pipelineProfile": "samd_research",
            "regulatory": {"primary_modality": "methylation", "primary_analyte": "cfdna"},
        }
    )


def test_enforce_study_primary_analyte_required_for_methyl() -> None:
    with pytest.raises(ValueError, match="primary_analyte"):
        enforce_study_primary_analyte(
            {
                "pipelineProfile": "samd_research",
                "regulatory": {"primary_modality": "methylation"},
            }
        )


def test_enforce_study_primary_analyte_skips_rnaseq() -> None:
    enforce_study_primary_analyte(
        {
            "pipelineProfile": "rnaseq_research",
            "regulatory": {"primary_modality": "rnaseq"},
        }
    )


def test_refuse_methyl_action_under_rnaseq() -> None:
    with pytest.raises(ValueError, match="methylation-pack-only"):
        refuse_methyl_action_for_modality("pipeline.centroid", "rnaseq")
    refuse_methyl_action_for_modality("pipeline.centroid", "methylation")
    refuse_methyl_action_for_modality("pipeline.rna_de_select", "rnaseq")
