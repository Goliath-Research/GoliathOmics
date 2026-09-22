"""Unit tests for proteomics ingest (DIA-NN report + panel matrices) and QC."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from proteomics_features.ingest import parse_diann_report, register_sample_abundance
from proteomics_qc.core import process_sample_proteomics_qc


def test_parse_diann_report(tmp_path: Path) -> None:
    d = tmp_path / "s1" / "s1.diann"
    d.mkdir(parents=True)
    pd.DataFrame(
        {"Protein.Group": ["P1", "P2", "P1"], "PG.MaxLFQ": [10.0, 20.0, 30.0]}
    ).to_csv(d / "report.tsv", sep="\t", index=False)
    values = parse_diann_report(d / "report.tsv")
    assert values["P1"] == 30.0  # max per protein group
    assert values["P2"] == 20.0


def test_register_diann_and_qc(tmp_path: Path) -> None:
    d = tmp_path / "s1"
    (d / "s1.diann").mkdir(parents=True)
    pd.DataFrame(
        {"Protein.Group": [f"P{i}" for i in range(20)], "PG.MaxLFQ": [100.0 + i for i in range(20)]}
    ).to_csv(d / "s1.diann" / "report.tsv", sep="\t", index=False)
    result = register_sample_abundance(sample_dir=str(d), sample_id="s1", source="diann")
    assert result["n_proteins"] == 20
    assert result["source"] == "diann"
    qc = process_sample_proteomics_qc(d, "s1", config={"min_proteins_identified": 10})
    assert json.loads(Path(qc).read_text())["guardrails"]["overall_pass"] is True


def test_panel_npx_long_ingest(tmp_path: Path) -> None:
    panel = tmp_path / "npx.csv"
    rows = []
    for sid in ("a", "b"):
        for p in ("P1", "P2", "P3"):
            rows.append({"SampleID": sid, "Assay": p, "NPX": 5.0})
    pd.DataFrame(rows).to_csv(panel, index=False)
    (tmp_path / "a").mkdir()
    result = register_sample_abundance(
        sample_dir=str(tmp_path / "a"), sample_id="a", source="panel", panel_path=str(panel), panel_format="npx"
    )
    assert result["n_proteins"] == 3
    assert result["source"] == "panel:npx"


def test_sage_lfq_ingest(tmp_path: Path) -> None:
    from proteomics_features.ingest import parse_sage_quant

    d = tmp_path / "s1"
    (d / "s1.sage").mkdir(parents=True)
    pd.DataFrame(
        {
            "proteins": ["P1", "P1", "P2", "P3"],
            "peptide": ["AAA", "BBB", "CCC", "DDD"],
            "charge": [2, 2, 3, 2],
            "file_0.mzML": [100.0, 50.0, 200.0, 0.0],
        }
    ).to_csv(d / "s1.sage" / "lfq.tsv", sep="\t", index=False)
    values = parse_sage_quant(d / "s1.sage" / "lfq.tsv")
    assert values["P1"] == 150.0  # summed peptides
    assert values["P2"] == 200.0
    assert "P3" not in values  # zero intensity dropped

    result = register_sample_abundance(sample_dir=str(d), sample_id="s1", source="sage")
    assert result["n_proteins"] == 2
    assert result["source"] == "sage"


def test_panel_wide_ingest(tmp_path: Path) -> None:
    panel = tmp_path / "wide.csv"
    pd.DataFrame(
        {"SampleId": ["a", "b"], "P1": [1.0, 2.0], "P2": [3.0, 4.0]}
    ).to_csv(panel, index=False)
    (tmp_path / "a").mkdir()
    result = register_sample_abundance(
        sample_dir=str(tmp_path / "a"), sample_id="a", source="panel", panel_path=str(panel), panel_format="open"
    )
    assert result["n_proteins"] == 2
