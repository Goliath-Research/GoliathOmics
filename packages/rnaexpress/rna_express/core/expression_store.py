"""Per-sample RNA-Seq expression contract: normalize quantifier outputs to expression.h5.

Canonical layout (via ``omics_features.feature_store``):

    {sample_id}.expression.h5
        /feature_id  (variable-length UTF-8 strings, sorted)
        /value       (float64, raw counts or summed est_counts)
        /tpm         (float64 aux, TPM where available else NaN)
        attrs: source (quant_mode), kind, n_features, sample_id

Legacy files (pre-shared store) used ``gene_id`` / ``count``; readers accept both
via ``omics_features.feature_store.read_feature_datasets``.

STAR (``pbrun rna_fq2bam``) gives per-gene counts directly; kallisto gives
transcript-level abundances that we aggregate to genes via a tx2gene map.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from omics_features.feature_store import (
    feature_h5_path,
    find_feature_h5,
    read_sample_features,
    write_sample_features,
)

_KIND = "expression"


def expression_h5_path(sample_dir: str | Path, sample_id: str) -> Path:
    return feature_h5_path(sample_dir, sample_id, kind=_KIND)


def _read_gene_counts_tsv(path: Path) -> Dict[str, float]:
    counts: Dict[str, float] = {}
    with open(path, encoding="utf-8") as handle:
        header = handle.readline()
        del header
        for row in handle:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                counts[parts[0]] = counts.get(parts[0], 0.0) + float(parts[1])
            except ValueError:
                continue
    return counts


def _load_tx2gene(path: Optional[str | Path]) -> Dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    mapping: Dict[str, str] = {}
    for row in p.read_text(encoding="utf-8").splitlines():
        parts = row.rstrip("\n").split("\t")
        if len(parts) >= 2 and parts[0]:
            mapping[parts[0]] = parts[1]
    return mapping


def _strip_version(feature_id: str) -> str:
    return feature_id.split(".", 1)[0] if "." in feature_id else feature_id


def _aggregate_kallisto(
    abundance_path: Path, tx2gene: Dict[str, str]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Aggregate transcript est_counts + tpm to gene level (fallback: keep transcript id)."""
    counts: Dict[str, float] = {}
    tpms: Dict[str, float] = {}
    with open(abundance_path, encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        try:
            tid_idx = header.index("target_id")
        except ValueError:
            tid_idx = 0
        try:
            est_idx = header.index("est_counts")
        except ValueError:
            est_idx = 3
        try:
            tpm_idx = header.index("tpm")
        except ValueError:
            tpm_idx = 4
        for row in handle:
            parts = row.rstrip("\n").split("\t")
            if len(parts) <= max(tid_idx, est_idx, tpm_idx):
                continue
            tid = parts[tid_idx]
            gene = tx2gene.get(tid) or tx2gene.get(_strip_version(tid)) or tid
            try:
                counts[gene] = counts.get(gene, 0.0) + float(parts[est_idx])
                tpms[gene] = tpms.get(gene, 0.0) + float(parts[tpm_idx])
            except ValueError:
                continue
    return counts, tpms


def register_sample_expression(
    *,
    sample_dir: str | Path,
    sample_id: str,
    tx2gene_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Write ``{sample_id}.expression.h5`` from whichever quantifier output is present."""
    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    gene_counts_tsv = sample_path / f"{sample_id}.gene_counts.tsv"
    abundance = sample_path / f"{sample_id}.kallisto" / "abundance.tsv"

    quant_mode: str
    counts: Dict[str, float]
    tpms: Dict[str, float]
    if gene_counts_tsv.is_file():
        quant_mode = "star"
        counts = _read_gene_counts_tsv(gene_counts_tsv)
        tpms = {}
    elif abundance.is_file():
        quant_mode = "kallisto"
        tx2gene = _load_tx2gene(tx2gene_path)
        counts, tpms = _aggregate_kallisto(abundance, tx2gene)
    else:
        raise RuntimeError(
            f"register_expression found no quantifier output for {sample_id}: expected "
            f"{gene_counts_tsv} or {abundance}"
        )

    # Delegate storage to the shared omics_features contract (feature_id/value + tpm aux).
    result = write_sample_features(
        sample_dir=sample_path,
        sample_id=sample_id,
        kind=_KIND,
        values=counts,
        source=quant_mode,
        aux={"tpm": tpms} if tpms else None,
    )
    return {
        "sampleId": str(sample_id),
        "expressionH5": result["featureH5"],
        "n_genes": result["n_features"],
        "quantMode": quant_mode,
    }


def read_sample_expression(sample_dir: str | Path, sample_id: str) -> Tuple[np.ndarray, np.ndarray, str]:
    """Return (gene_ids, counts, quant_mode) for a registered sample."""
    feature_ids, values, source = read_sample_features(sample_dir, sample_id, kind=_KIND)
    return feature_ids, values, source


def find_expression_h5(sample_path: str | Path) -> Optional[Path]:
    """Locate an expression.h5 under a sample directory (any sample id)."""
    return find_feature_h5(sample_path, kind=_KIND)
