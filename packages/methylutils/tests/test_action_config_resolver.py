"""Precedence and routing tests for the shared action config resolver.

These lock the merge "spine" that sits upstream of every typed action: a bug
here mis-feeds every downstream action with type-valid but wrong values that the
Pydantic models cannot catch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.action_config_resolver import (
    deep_merge,
    load_profile_action_config,
    load_resolved_config,
    load_site_manifest,
    resolve_action_config,
    resolve_for_project,
    resolve_from_task_input,
    resolve_proteomics_reference,
    resolve_rna_reference,
    site_slice_for_action,
)


# --------------------------------------------------------------------------- #
# deep_merge
# --------------------------------------------------------------------------- #
def test_deep_merge_overlay_wins_and_recurses():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    overlay = {"a": 2, "nested": {"y": 20, "z": 30}}
    merged = deep_merge(base, overlay)
    assert merged == {"a": 2, "nested": {"x": 1, "y": 20, "z": 30}}


def test_deep_merge_does_not_mutate_base():
    base = {"nested": {"x": 1}}
    overlay = {"nested": {"y": 2}}
    deep_merge(base, overlay)
    assert base == {"nested": {"x": 1}}, "base dict must not be mutated"


def test_deep_merge_replaces_lists_not_concatenates():
    base = {"items": [1, 2, 3]}
    overlay = {"items": [9]}
    assert deep_merge(base, overlay)["items"] == [9]


def test_deep_merge_scalar_overrides_dict_and_vice_versa():
    # Overlay scalar replaces a base dict entirely.
    assert deep_merge({"k": {"a": 1}}, {"k": 5})["k"] == 5
    # Overlay dict replaces a base scalar entirely.
    assert deep_merge({"k": 5}, {"k": {"a": 1}})["k"] == {"a": 1}


def test_deep_merge_null_deletes_leaf_key():
    """JSON null clears a site/profile cap (uncap / unset)."""
    base = {"max_dmps": 1000, "max_genes": 200}
    assert deep_merge(base, {"max_dmps": None}) == {"max_genes": 200}


def test_deep_merge_null_deletes_nested_key():
    base = {"validation": {"stability_gene_featurecuts_max_dmps": 1000, "n_iterations": 50}}
    merged = deep_merge(base, {"validation": {"stability_gene_featurecuts_max_dmps": None}})
    assert "stability_gene_featurecuts_max_dmps" not in merged["validation"]
    assert merged["validation"]["n_iterations"] == 50


def test_resolve_action_config_instance_null_clears_site_cap():
    site = {"actionConfig": {"gene_selection": {"max_dmps": 1000, "max_genes": 200}}}
    merged = resolve_action_config(
        "gene_selection",
        site=site,
        instance_override={"max_dmps": None},
    )
    assert "max_dmps" not in merged
    assert merged["max_genes"] == 200


# --------------------------------------------------------------------------- #
# resolve_action_config layer precedence
# --------------------------------------------------------------------------- #
def test_resolve_action_config_layer_precedence_same_key():
    """site < profile section < program override < instance override, per-key."""
    site = {"actionConfig": {"detection": {"knob": "site", "site_only": 1}}}
    profile = {"detection": {"knob": "profile", "profile_only": 2}}
    program = {"knob": "program", "program_only": 3}
    instance = {"knob": "instance", "instance_only": 4}

    merged = resolve_action_config(
        "detection",
        site=site,
        profile_action_config=profile,
        program_override=program,
        instance_override=instance,
    )
    # Highest layer (instance) wins on the shared key.
    assert merged["knob"] == "instance"
    # Untouched keys from every layer survive.
    assert merged["site_only"] == 1
    assert merged["profile_only"] == 2
    assert merged["program_only"] == 3
    assert merged["instance_only"] == 4


def test_resolve_action_config_program_beats_profile_when_no_instance():
    site = {"actionConfig": {"mapper": {"knob": "site"}}}
    profile = {"mapper": {"knob": "profile"}}
    program = {"knob": "program"}
    merged = resolve_action_config(
        "mapper",
        site=site,
        profile_action_config=profile,
        program_override=program,
    )
    assert merged["knob"] == "program"


def test_resolve_action_config_profile_beats_site():
    site = {"actionConfig": {"enricher": {"top": 10}}}
    profile = {"enricher": {"top": 150}}
    merged = resolve_action_config("enricher", site=site, profile_action_config=profile)
    assert merged["top"] == 150


def test_resolve_action_config_analyte_fills_missing_keys_last():
    """Analyte profile fills gaps but must not override explicit resolved keys."""
    # cfdna analyte enables fragmentomics; explicit profile disabling it wins.
    merged = resolve_action_config(
        "fragmentomics",
        profile_action_config={"fragmentomics": {"enabled": False}},
        regulatory={"primary_analyte": "cfdna"},
    )
    assert merged["enabled"] is False


def test_resolve_action_config_no_analyte_when_regulatory_empty():
    merged = resolve_action_config(
        "fragmentomics",
        profile_action_config={},
        regulatory={},
    )
    # No analyte layer applied -> stays empty (no invented defaults).
    assert merged == {}


# --------------------------------------------------------------------------- #
# site_slice_for_action path derivation
# --------------------------------------------------------------------------- #
def test_site_slice_explicit_action_config_wins():
    site = {"actionConfig": {"mapper": {"gtf": "/explicit.gtf"}}}
    assert site_slice_for_action(site, "mapper") == {"gtf": "/explicit.gtf"}


def test_site_slice_mapper_derives_gtf_and_home():
    site = {
        "annotation": {"gtf": "/ref/genes.gtf"},
        "caches": {"methyl_mapper": "/cache/mm"},
    }
    out = site_slice_for_action(site, "mapper")
    assert out["gtf"] == "/ref/genes.gtf"
    assert out["methyl_mapper_home"] == "/cache/mm"


def test_site_slice_alignment_qc_and_methyl_extract_fasta():
    site = {"reference_genome": {"fasta": "/ref/genome.fa"}}
    aln = site_slice_for_action(site, "alignment_qc")
    assert aln["genome_fasta"] == "/ref/genome.fa"
    assert aln["reference_fasta"] == "/ref/genome.fa"
    ext = site_slice_for_action(site, "methyl_extract")
    assert ext["reference_fasta"] == "/ref/genome.fa"
    assert ext["genome_fasta"] == "/ref/genome.fa"


def test_site_slice_methyl_extract_action_config_keeps_reference_fasta():
    """actionConfig.methyl_extract knobs must not drop reference_genome.fasta."""
    site = {
        "reference_genome": {"fasta": "/ref/genome.fa"},
        "actionConfig": {
            "methyl_extract": {
                "extract_contexts": ["CG", "CHG", "CHH"],
                "threads": 10,
            }
        },
    }
    ext = site_slice_for_action(site, "methyl_extract")
    assert ext["extract_contexts"] == ["CG", "CHG", "CHH"]
    assert ext["threads"] == 10
    assert ext["reference_fasta"] == "/ref/genome.fa"
    assert ext["genome_fasta"] == "/ref/genome.fa"


def test_resolve_action_config_methyl_extract_keeps_genome_with_profile_knobs():
    site = {
        "reference_genome": {"fasta": "/ref/genome.fa"},
        "actionConfig": {"methyl_extract": {"extract_contexts": ["CG"]}},
    }
    profile = {
        "methyl_extract": {
            "extract_contexts": ["CG", "CHG", "CHH"],
            "threads": 10,
            "read_level": {"enabled": True, "tile_size": 4},
        }
    }
    merged = resolve_action_config(
        "methyl_extract",
        site=site,
        profile_action_config=profile,
    )
    assert merged["extract_contexts"] == ["CG", "CHG", "CHH"]
    assert merged["threads"] == 10
    assert merged["read_level"] == {"enabled": True, "tile_size": 4}
    assert merged["reference_fasta"] == "/ref/genome.fa"


def test_site_slice_pangenome_linear_fasta_preferred():
    site = {
        "reference_genome": {"fasta": "/ref/linear.fa"},
        "pangenome_genome": {"linear_ref_fasta": "/ref/pangenome_linear.fa"},
    }
    aln = site_slice_for_action(site, "alignment_qc")
    assert aln["genome_fasta"] == "/ref/pangenome_linear.fa"
    ext = site_slice_for_action(site, "methyl_extract")
    assert ext["reference_fasta"] == "/ref/pangenome_linear.fa"
    assert ext["genome_fasta"] == "/ref/pangenome_linear.fa"


def test_site_slice_enricher_cache_path():
    site = {"caches": {"string_edges": "/cache/string_edges"}}
    out = site_slice_for_action(site, "enricher")
    assert out["network_refinement"]["cache_path"] == "/cache/string_edges"


def test_site_slice_unknown_action_is_empty():
    assert site_slice_for_action({"reference_genome": {"fasta": "/x"}}, "classifier") == {}


# --------------------------------------------------------------------------- #
# resolve_from_task_input (worker claim-time hot path)
# --------------------------------------------------------------------------- #
def test_resolve_from_task_input_resolved_config_passthrough():
    out = resolve_from_task_input(
        "detection", {"resolvedConfig": {"alpha": 0.01, "min_coverage": 4}}
    )
    assert out == {"alpha": 0.01, "min_coverage": 4}


def test_resolve_from_task_input_resolved_config_with_step_override():
    out = resolve_from_task_input(
        "detection",
        {
            "resolvedConfig": {"alpha": 0.05, "min_coverage": 4},
            "stepOverride": {"alpha": 0.01},
        },
    )
    assert out["alpha"] == 0.01
    assert out["min_coverage"] == 4


def test_resolve_from_task_input_rebuild_from_action_config_slice():
    out = resolve_from_task_input(
        "detection",
        {
            "actionConfig": {"detection": {"alpha": 0.02}},
            "siteConfig": {},
        },
    )
    assert out["alpha"] == 0.02


def test_resolve_from_task_input_rebuild_merges_site_paths():
    out = resolve_from_task_input(
        "mapper",
        {
            "actionConfig": {"mapper": {"csv_pattern": "dmps-*.csv"}},
            "siteConfig": {"annotation": {"gtf": "/ref/genes.gtf"}},
        },
    )
    assert out["csv_pattern"] == "dmps-*.csv"
    assert out["gtf"] == "/ref/genes.gtf"


def test_resolve_from_task_input_step_override_wins_in_rebuild():
    out = resolve_from_task_input(
        "detection",
        {
            "actionConfig": {"detection": {"alpha": 0.05}},
            "siteConfig": {},
            "stepOverride": {"alpha": 0.001},
        },
    )
    assert out["alpha"] == 0.001


# --------------------------------------------------------------------------- #
# load_resolved_config / resolve_for_project routing
# --------------------------------------------------------------------------- #
def test_load_resolved_config_from_dict():
    out = load_resolved_config("enricher", resolved_config={"top": 150})
    assert out == {"top": 150}


def test_load_resolved_config_override_dict_wins_over_path(tmp_path: Path):
    cfg = tmp_path / "resolved.json"
    cfg.write_text(json.dumps({"top": 150, "cutoff": 0.05}), encoding="utf-8")
    out = load_resolved_config(
        "enricher",
        resolved_config_path=cfg,
        step_override={"top": 10},
    )
    assert out["top"] == 10
    assert out["cutoff"] == 0.05


def test_load_resolved_config_rejects_non_object(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        load_resolved_config("enricher", resolved_config_path=bad)


def test_resolve_for_project_routes_to_baked_config_when_present():
    """When a baked slice is present, no project/env fallback is consulted."""
    sentinel = object()  # would raise if the resolver touched project methods

    out = resolve_for_project(
        "enricher",
        sentinel,
        resolved_config={"top": 7},
    )
    assert out == {"top": 7}


def test_resolve_for_project_falls_back_to_env(monkeypatch, tmp_path: Path):
    """No baked config -> resolve from site/profile env (routing branch)."""
    site = tmp_path / "site.json"
    site.write_text(
        json.dumps({"actionConfig": {"enricher": {"top": 99}}}), encoding="utf-8"
    )

    class _Project:
        def get_regulatory_config(self):
            return {}

    out = resolve_for_project("enricher", _Project(), site_path=site)
    assert out["top"] == 99


def test_load_site_manifest_missing_file_returns_empty(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("METHYL_SITE_CONFIG", raising=False)
    assert load_site_manifest(tmp_path / "missing.json") == {}


def test_load_profile_action_config_missing_name_or_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("METHYL_PROFILE", raising=False)
    assert load_profile_action_config(None) == {}
    assert load_profile_action_config(tmp_path / "no-such.profile.json") == {}


def test_load_profile_action_config_reads_action_config(tmp_path: Path):
    profile = tmp_path / "demo.profile.json"
    profile.write_text(
        json.dumps({"pipelineProfile": "demo", "actionConfig": {"detection": {"alpha": 0.03}}}),
        encoding="utf-8",
    )
    assert load_profile_action_config(profile) == {"detection": {"alpha": 0.03}}


def test_site_slice_parabricks_methyl_extract_and_omics_refs():
    site = {
        "parabricks": {"bwa_threads": 8},
        "methyl_extract": {"min_coverage": 5},
        "rna_reference": {"star_index_dir": "/rna/star", "gtf": "/rna/genes.gtf"},
        "proteomics_reference": {"protein_fasta": "/prot/proteins.fa"},
        "annotation": {},
        "caches": {},
    }
    assert site_slice_for_action(site, "parabricks")["bwa_threads"] == 8
    assert site_slice_for_action(site, "methyl_extract")["min_coverage"] == 5
    rna = site_slice_for_action(site, "rna_align")
    assert rna["star_index_dir"] == "/rna/star"
    assert rna["gtf"] == "/rna/genes.gtf"
    assert rna["bwa_threads"] == 8  # parabricks defaults merged for rna_align
    prot = site_slice_for_action(site, "proteomics_quant")
    assert prot["protein_fasta"] == "/prot/proteins.fa"


def test_resolve_rna_and_proteomics_reference_helpers():
    site = {
        "rna_reference": {"kallisto_index": "/rna/kallisto.idx", "unused": ""},
        "proteomics_reference": {"spectral_library": "/prot/lib.tsv"},
    }
    assert resolve_rna_reference(site) == {"kallisto_index": "/rna/kallisto.idx"}
    assert resolve_proteomics_reference(site) == {"spectral_library": "/prot/lib.tsv"}
    assert resolve_rna_reference({"rna_reference": "bad"}) == {}
    assert resolve_proteomics_reference({"proteomics_reference": []}) == {}


def test_resolve_from_task_input_loads_profile_when_action_config_absent(tmp_path: Path, monkeypatch):
    profile = tmp_path / "mc.profile.json"
    profile.write_text(
        json.dumps({"actionConfig": {"detection": {"alpha": 0.07}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_PROFILE", str(profile))
    out = resolve_from_task_input("detection", {"siteConfig": {}})
    assert out["alpha"] == 0.07
