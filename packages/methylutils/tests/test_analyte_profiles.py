"""Tests for analyte-driven step_config profiles."""

from methyl_utils.analyte_profiles import (
    analyte_token,
    cisbp_mode_label,
    is_cfdna_analyte,
    merge_step_config,
    normalize_primary_analyte,
    profile_for_analyte,
    resolve_cisbp_modes,
    should_apply_analyte_profile,
)
from methyl_utils.pipeline_config import ProjectConfig, load_project


def test_normalize_primary_analyte():
    assert normalize_primary_analyte("Plasma cfDNA") == "cfdna"
    assert normalize_primary_analyte("buffy") == "buffy_coat"
    assert normalize_primary_analyte("plant") == "plant_tissue"
    assert normalize_primary_analyte("leaf") == "plant_tissue"
    assert normalize_primary_analyte("plant-tissue") == "plant_tissue"


def test_analyte_token_and_is_cfdna_analyte():
    assert analyte_token("Buffy_Coat") == "buffy_coat"
    assert analyte_token("cfDNA") == "cfdna"
    assert analyte_token("CF-DNA") == "cf_dna"
    assert is_cfdna_analyte("plasma") is True
    assert is_cfdna_analyte("cell_free_dna") is True
    assert is_cfdna_analyte("CELL-FREE-DNA") is True
    assert is_cfdna_analyte("buffy_coat") is False


def test_plant_tissue_profile_opens_non_cpg_qc():
    align = merge_step_config("alignment_qc", {}, "plant_tissue")
    assert align["bisulfite_conversion"]["enabled"] is True
    assert align["bisulfite_conversion"]["max_non_cpg_methylation_pct"] == 100.0
    frag = merge_step_config("fragmentomics", {}, "plant_tissue")
    assert frag["enabled"] is False
    extraction = merge_step_config("extraction_qc", {}, "plant_tissue")
    assert extraction["guardrails"]["max_chh_methylation_level"] == 1.0
    assert extraction["guardrails"]["max_chg_methylation_level"] == 1.0
    enr = merge_step_config("enricher", {}, "plant_tissue")
    assert enr["library_preset"] == "plant-stress-core"
    assert enr["organism"] == "Arabidopsis_thaliana"


def test_cfdna_profile_fills_missing_keys():
    user = {"genome_fasta": "/custom.fa"}
    merged = merge_step_config("alignment_qc", user, "cfdna")
    assert merged["genome_fasta"] == "/custom.fa"
    assert merged["auto_profile_from_analyte"] is True
    assert merged["fragmentomics"]["profile"] == "cfdna"
    assert merged["bisulfite_conversion"]["enabled"] is True
    assert merged["alignment_guardrails"]["enabled"] is True
    assert merged["alignment_guardrails"]["min_mapping_rate"] == 0.98


def test_buffy_profile_enables_alignment_guardrails():
    merged = merge_step_config("alignment_qc", {}, "buffy_coat")
    assert merged["alignment_guardrails"]["enabled"] is True
    assert merged["alignment_guardrails"]["flagstat_enabled"] is True


def test_user_override_wins_over_profile():
    user = {"fragmentomics": {"enabled": False}}
    merged = merge_step_config("alignment_qc", user, "cfdna")
    assert merged["fragmentomics"]["enabled"] is False


def test_buffy_disables_fragmentomics_step():
    merged = merge_step_config("fragmentomics", {}, "buffy_coat")
    assert merged["enabled"] is False


def test_should_apply_opt_out():
    assert should_apply_analyte_profile({"primary_analyte": "cfdna"}) is True
    assert should_apply_analyte_profile(
        {"primary_analyte": "cfdna", "auto_apply_analyte_profile": False}
    ) is False


def test_resolve_action_config_applies_analyte_profile():
    from methyl_utils.action_config_resolver import resolve_action_config

    cfg = resolve_action_config(
        "alignment_qc",
        profile_action_config={},
        regulatory={"primary_analyte": "cfdna"},
    )
    assert cfg["auto_profile_from_analyte"] is True
    frag = resolve_action_config(
        "fragmentomics",
        profile_action_config={},
        regulatory={"primary_analyte": "cfdna"},
    )
    assert frag["enabled"] is True
    enr = resolve_action_config(
        "enricher",
        profile_action_config={},
        regulatory={"primary_analyte": "cfdna"},
    )
    assert enr["cisbp"]["enabled"] is True
    assert enr["cisbp"]["cisbp_modes"] == ["gene_sets", "motif_scan", "annotate"]


def test_cisbp_mode_label_and_resolve():
    assert cisbp_mode_label("gene_sets") == "CIS-BP"
    assert cisbp_mode_label("motif_scan") == "CIS-BP-motif"

    class _Cfg:
        cisbp_modes = ["annotate", "gene_sets", "motif_scan"]
        mode = "gene_sets"

    assert resolve_cisbp_modes(_Cfg()) == ["gene_sets", "motif_scan", "annotate"]
