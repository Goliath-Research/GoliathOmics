"""Unit tests for the RNA expression contract, matrix loader, and DE gene selection."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from rna_express.core.de_select import select_de_genes
from rna_express.core.expression_store import (
    read_sample_expression,
    register_sample_expression,
)
from rna_express.core.matrix import load_expression_matrix
from rna_express.models.config import RnaDeSelectConfig


def _write_star_sample(sample_dir: Path, sample_id: str, counts: dict) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    lines = ["gene_id\tcount\n"] + [f"{g}\t{int(c)}\n" for g, c in counts.items()]
    (sample_dir / f"{sample_id}.gene_counts.tsv").write_text("".join(lines), encoding="utf-8")


def _write_kallisto_sample(sample_dir: Path, sample_id: str, rows) -> None:
    out = sample_dir / f"{sample_id}.kallisto"
    out.mkdir(parents=True, exist_ok=True)
    header = "target_id\tlength\teff_length\test_counts\ttpm\n"
    body = "".join(f"{t}\t100\t80\t{ec}\t{tpm}\n" for (t, ec, tpm) in rows)
    (out / "abundance.tsv").write_text(header + body, encoding="utf-8")


def test_register_star_and_read(tmp_path: Path) -> None:
    d = tmp_path / "s1"
    _write_star_sample(d, "s1", {"G1": 10, "G2": 0, "G3": 5})
    result = register_sample_expression(sample_dir=str(d), sample_id="s1")
    assert result["quantMode"] == "star"
    assert result["n_genes"] == 3
    genes, counts, mode = read_sample_expression(str(d), "s1")
    assert mode == "star"
    assert set(genes) == {"G1", "G2", "G3"}
    assert counts.sum() == 15


def test_register_kallisto_tx2gene_aggregation(tmp_path: Path) -> None:
    d = tmp_path / "k1"
    _write_kallisto_sample(d, "k1", [("T1.1", 4, 10.0), ("T2.1", 6, 20.0), ("T3.1", 2, 5.0)])
    tx2gene = tmp_path / "tx2gene.tsv"
    tx2gene.write_text("T1.1\tGENEA\nT2.1\tGENEA\nT3.1\tGENEB\n", encoding="utf-8")
    result = register_sample_expression(
        sample_dir=str(d), sample_id="k1", tx2gene_path=str(tx2gene)
    )
    assert result["quantMode"] == "kallisto"
    genes, counts, _ = read_sample_expression(str(d), "k1")
    by_gene = dict(zip(genes, counts))
    assert by_gene["GENEA"] == 10.0  # 4 + 6
    assert by_gene["GENEB"] == 2.0


def test_matrix_and_de_selection_recovers_signal(tmp_path: Path) -> None:
    genes = [f"G{i}" for i in range(30)]
    rng = np.random.default_rng(1)
    dirs = []
    labels = []
    for grp in (0, 1):
        for k in range(6):
            sid = f"s{grp}_{k}"
            base = rng.poisson(40, size=len(genes)).astype(float)
            if grp == 1:
                base[:5] += 300  # first 5 genes strongly up in disease
            _write_star_sample(tmp_path / sid, sid, dict(zip(genes, base)))
            register_sample_expression(sample_dir=str(tmp_path / sid), sample_id=sid)
            dirs.append(str(tmp_path / sid))
            labels.append(grp)

    X, gene_ids, sample_ids = load_expression_matrix(dirs, transform="logcpm")
    assert X.shape == (12, 30)
    assert len(sample_ids) == 12
    panel = select_de_genes(X, np.array(labels), gene_ids, config=RnaDeSelectConfig(max_genes=5))
    top = set(panel["gene_id"].tolist())
    # At least 4 of the 5 planted DE genes should be recovered.
    assert len({f"G{i}" for i in range(5)} & top) >= 4
