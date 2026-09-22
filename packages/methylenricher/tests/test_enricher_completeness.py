"""Tests for enricher completeness helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from methyl_enricher.enricher_completeness import (
    COMPLETENESS_MANIFEST_FILENAME,
    CompletenessReport,
    RetryPolicy,
    assess_completeness,
    classify_enrich_error,
    completeness_manifest_path,
    enrich_one_library,
    is_cisbp_library_label,
    is_retryable,
    merge_library_results,
    primary_enrichment_rows,
    production_enricher_root,
    sanitize_enrichment_df,
    EnrichErrorKind,
)


def test_classify_enrich_error_429():
    assert classify_enrich_error(Exception("status code: 429")) == EnrichErrorKind.RATE_LIMITED


def test_is_cisbp_library_label():
    assert is_cisbp_library_label("CIS-BP")
    assert is_cisbp_library_label("CIS-BP-annotate")
    assert is_cisbp_library_label("CIS-BP-motif")
    assert not is_cisbp_library_label("ChEA_2022")


def test_sanitize_enrichment_df_clamps_zero_pvalues():
    import numpy as np

    df = pd.DataFrame(
        {
            "Term": ["a", "b"],
            "P-value": [0.0, 1e-12],
            "Adjusted P-value": [0.0, 1e-10],
            "Odds Ratio": [10.0, 5.0],
            "Combined Score": [float("inf"), 100.0],
        }
    )
    out = sanitize_enrichment_df(df)
    assert (out["P-value"] > 0).all()
    assert (out["Adjusted P-value"] > 0).all()
    assert np.isfinite(out.loc[0, "Combined Score"])


def test_merge_cisbp_appended_after_primary_libraries(tmp_path: Path):
    kegg = "KEGG_2021_Human"
    pd.DataFrame(
        {
            "Term": ["pathway_a"],
            "Adjusted P-value": [0.05],
            "P-value": [0.01],
            "Odds Ratio": [2.0],
            "library": [kegg],
        }
    ).to_csv(tmp_path / f"enrich_{kegg}.csv", index=False)
    pd.DataFrame(
        {
            "Term": ["MYC"],
            "Adjusted P-value": [1e-300],
            "P-value": [1e-300],
            "Odds Ratio": [100.0],
            "library": ["CIS-BP"],
        }
    ).to_csv(tmp_path / "enrich_CIS-BP.csv", index=False)

    merged = merge_library_results(tmp_path, [kegg, "CIS-BP"], cutoff=0.05)
    assert merged.iloc[0]["library"] == kegg
    assert merged.iloc[-1]["library"] == "CIS-BP"

    top = pd.read_csv(tmp_path / "enrichment_top_q0.05.csv")
    assert (top["library"] != "CIS-BP").all()
    assert primary_enrichment_rows(merged).iloc[0]["library"] == kegg


def test_merge_cisbp_only_does_not_write_top_hits(tmp_path: Path):
    """When all Enrichr libraries fail, CIS-BP must not populate enrichment_top_q*.csv."""
    pd.DataFrame(
        {
            "Term": ["MYC"],
            "Adjusted P-value": [1e-300],
            "P-value": [1e-300],
            "Odds Ratio": [100.0],
            "library": ["CIS-BP"],
        }
    ).to_csv(tmp_path / "enrich_CIS-BP.csv", index=False)

    merged = merge_library_results(tmp_path, ["KEGG_2021_Human", "CIS-BP"], cutoff=0.05)
    assert len(merged) == 1
    assert merged.iloc[0]["library"] == "CIS-BP"
    assert not (tmp_path / "enrichment_top_q0.05.csv").is_file()


def test_merge_library_results_from_csvs(tmp_path: Path):
    lib = "KEGG_2021_Human"
    df = pd.DataFrame(
        {
            "Term": ["pathway_a"],
            "Adjusted P-value": [0.01],
            "P-value": [0.001],
            "Odds Ratio": [2.0],
        }
    )
    df.to_csv(tmp_path / f"enrich_{lib}.csv", index=False)
    merged = merge_library_results(tmp_path, [lib], cutoff=0.05)
    assert len(merged) == 1
    assert (tmp_path / "enrichment_merged.csv").is_file()


def test_production_enricher_root_not_control_subdir(tmp_path: Path):
    """Manifest and queue must live under enricher/, not enricher/<control_group>/."""
    sample_csv = tmp_path / "PCa1.csv"
    sample_csv.write_text("sample_id\ns1\n", encoding="utf-8")
    healthy_csv = tmp_path / "healthy.csv"
    healthy_csv.write_text("sample_id\nh1\n", encoding="utf-8")
    prod_dir = tmp_path / "production"
    proj = {
        "project_name": "production",
        "output_base": str(tmp_path),
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": [str(healthy_csv)]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [
                {
                    "label": "PCa",
                    "stages": [{"label": "PCa1", "sample_paths": [str(sample_csv)]}],
                }
            ],
        },
        "comparisons": "control_vs_each_disease",
    }
    prod_dir.mkdir(parents=True)
    project_json = prod_dir / "project.json"
    project_json.write_text(json.dumps(proj), encoding="utf-8")

    root = production_enricher_root(project_json)
    assert root == prod_dir / "enricher"
    assert root != prod_dir / "enricher" / "all"

    manifest = completeness_manifest_path(project_json)
    assert manifest == prod_dir / "enricher" / COMPLETENESS_MANIFEST_FILENAME

    from methyl_enricher.ensure_complete import write_project_completeness_manifest

    write_project_completeness_manifest(
        project_json,
        {
            "PCa_PCa1": CompletenessReport(
                output_dir=str(prod_dir / "enricher" / "all" / "PCa_PCa1"),
                expected_libraries=["KEGG_2021_Human"],
                present_libraries=["KEGG_2021_Human"],
                missing_libraries=[],
                modules_required=False,
                modules_present=False,
                complete=True,
            )
        },
    )
    assert manifest.is_file()
    assert not (prod_dir / "enricher" / "all" / COMPLETENESS_MANIFEST_FILENAME).is_file()


def test_assess_completeness_detects_missing(tmp_path: Path):
    libs = ["KEGG_2021_Human", "Reactome_2022"]
    (tmp_path / "enrich_KEGG_2021_Human.csv").write_text("Term\nx\n", encoding="utf-8")
    report = assess_completeness(tmp_path, libs, modules_required=False)
    assert not report.complete
    assert "Reactome_2022" in report.missing_libraries


def test_enrich_one_library_retries_then_succeeds(tmp_path: Path):
    policy = RetryPolicy(max_retries=2, base_seconds=0.01, max_seconds=0.05, inter_library_delay_seconds=0)
    mock_df = pd.DataFrame(
        {
            "Term": ["t1"],
            "Adjusted P-value": [0.01],
            "P-value": [0.001],
            "Odds Ratio": [1.5],
        }
    )
    mock_enr = MagicMock()
    mock_enr.results = mock_df

    calls = {"n": 0}

    def fake_enrichr(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("status code: 429")
        return mock_enr

    with patch("gseapy.enrichr", side_effect=fake_enrichr):
        res = enrich_one_library(
            "KEGG_2021_Human",
            ["BRCA1", "TP53"],
            tmp_path,
            policy=policy,
        )
    assert res.success
    assert res.attempts == 2
    assert is_retryable(EnrichErrorKind.RATE_LIMITED)
