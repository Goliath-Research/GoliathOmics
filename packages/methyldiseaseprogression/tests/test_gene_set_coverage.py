"""Unit tests for progression gene-set coverage config normalization and profiles."""

from __future__ import annotations

import pandas as pd

from methyl_disease_progression.gene_set_coverage import (
    _dict_from_categories,
    build_gene_set_fractions_json_payload,
    gene_set_fractions_summary,
    load_gene_set_profile,
    normalize_progression_gene_set_config,
)


def test_normalize_maps_nested_top_n_universe() -> None:
    raw = {
        "gene_set_metrics": {
            "enabled": True,
            "gene_universe": "mapper_top_n",
            "top_n": 250,
            "output_basename": "custom.csv",
        }
    }
    out = normalize_progression_gene_set_config(raw)
    assert out["gene_set_metrics_enabled"] is True
    assert out["gene_set_denominator"] == "top_n"
    assert out["gene_set_top_n"] == 250
    # ".csv" suffix stripped from output basename.
    assert out["_gene_set_output_basename"] == "custom"


def test_normalize_defaults_output_basename_for_nested_block() -> None:
    out = normalize_progression_gene_set_config({"gene_set_metrics": {"enabled": False}})
    assert out["_gene_set_output_basename"] == "stage_gene_set_fractions"


def test_dict_from_categories_filters_invalid_entries() -> None:
    categories = [
        {"id": "hallmark", "genes": ["BRCA1", " tp53 ", ""]},
        {"id": "no_genes"},  # missing genes -> skipped
        {"genes": ["X"]},  # missing id -> skipped
        "not-a-dict",
    ]
    result = _dict_from_categories(categories)
    assert result == {"hallmark": ["BRCA1", "tp53"]}


def test_load_gene_set_profile_from_inline_categories() -> None:
    cfg = {
        "_gene_set_profile_categories": [
            {"id": "cat1", "genes": ["BRCA1", "TP53"]},
        ]
    }
    profile, source = load_gene_set_profile(cfg)
    assert profile == {"cat1": {"BRCA1", "TP53"}}
    assert source == "inline:gene_set_profile.categories"


def test_load_gene_set_profile_empty_when_nothing_configured() -> None:
    profile, source = load_gene_set_profile({})
    assert profile == {}
    assert source is None


def test_summary_reports_disabled_for_empty_frame() -> None:
    summary = gene_set_fractions_summary(pd.DataFrame(), {"disease_context": "prostate_cancer"})
    assert summary["enabled"] is False
    assert summary["rows"] == 0
    assert summary["disease_context"] == "prostate_cancer"


def test_json_payload_roundtrips_rows() -> None:
    df = pd.DataFrame(
        [
            {"stage_index": 0, "category_id": "cat1", "fraction": 0.25},
            {"stage_index": 1, "category_id": "cat1", "fraction": 0.5},
        ]
    )
    payload = build_gene_set_fractions_json_payload(df)
    assert payload["n_rows"] == 2
    assert payload["rows"][0]["fraction"] == 0.25
    assert build_gene_set_fractions_json_payload(pd.DataFrame()) == {"rows": []}
