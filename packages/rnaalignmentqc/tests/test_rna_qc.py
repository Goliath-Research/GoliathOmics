"""Unit tests for RNA-Seq alignment/quantification QC guardrails."""

from __future__ import annotations

import json
from pathlib import Path

from rna_alignment_qc.core import process_sample_rna_qc


def _star_log(sample_dir: Path, sample_id: str, input_reads: int, unique_pct: float, multi_pct: float) -> None:
    star = sample_dir / f"{sample_id}.star"
    star.mkdir(parents=True, exist_ok=True)
    (star / "Log.final.out").write_text(
        f"                          Number of input reads |\t{input_reads}\n"
        f"                        Uniquely mapped reads % |\t{unique_pct}%\n"
        f"             % of reads mapped to multiple loci |\t{multi_pct}%\n",
        encoding="utf-8",
    )


def _gene_counts(sample_dir: Path, sample_id: str, n_detected: int) -> None:
    lines = ["gene_id\tcount\n"] + [f"G{i}\t{10 if i < n_detected else 0}\n" for i in range(n_detected + 5)]
    (sample_dir / f"{sample_id}.gene_counts.tsv").write_text("".join(lines), encoding="utf-8")


def test_star_qc_pass(tmp_path: Path) -> None:
    d = tmp_path / "s1"
    d.mkdir()
    _star_log(d, "s1", input_reads=6_000_000, unique_pct=85.0, multi_pct=5.0)
    _gene_counts(d, "s1", n_detected=20)
    qc = process_sample_rna_qc(
        d,
        "s1",
        config={"min_genes_detected": 10, "min_input_reads": 1_000_000, "min_mapping_rate": 0.5},
    )
    payload = json.loads(Path(qc).read_text())
    assert payload["quant_mode"] == "star"
    assert payload["guardrails"]["overall_pass"] is True


def test_star_qc_fail_low_mapping(tmp_path: Path) -> None:
    d = tmp_path / "s2"
    d.mkdir()
    _star_log(d, "s2", input_reads=6_000_000, unique_pct=10.0, multi_pct=5.0)
    _gene_counts(d, "s2", n_detected=20)
    qc = process_sample_rna_qc(
        d, "s2", config={"min_genes_detected": 10, "min_input_reads": 1_000_000, "min_mapping_rate": 0.5}
    )
    payload = json.loads(Path(qc).read_text())
    assert payload["guardrails"]["overall_pass"] is False


def test_kallisto_qc(tmp_path: Path) -> None:
    d = tmp_path / "k1"
    out = d / "k1.kallisto"
    out.mkdir(parents=True)
    (out / "run_info.json").write_text(
        json.dumps({"n_processed": 5_000_000, "n_pseudoaligned": 4_000_000, "p_pseudoaligned": 80.0}),
        encoding="utf-8",
    )
    (out / "abundance.tsv").write_text(
        "target_id\tlength\teff_length\test_counts\ttpm\n" + "".join(f"T{i}\t100\t80\t{10}\t{5}\n" for i in range(20)),
        encoding="utf-8",
    )
    qc = process_sample_rna_qc(
        d, "k1", config={"min_genes_detected": 10, "min_input_reads": 1_000_000, "min_pseudoalignment_rate": 0.5}
    )
    payload = json.loads(Path(qc).read_text())
    assert payload["quant_mode"] == "kallisto"
    assert payload["guardrails"]["overall_pass"] is True
