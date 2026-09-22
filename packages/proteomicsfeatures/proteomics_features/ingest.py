"""Ingest protein abundances (DIA-NN report or panel matrix) to the shared contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from omics_features.feature_store import write_sample_features

_KIND = "abundance"


def _read_table(path: Path):
    import pandas as pd

    sep = "\t" if path.suffix.lower() in (".tsv", ".txt") else ","
    return pd.read_csv(path, sep=sep)


def parse_diann_report(report_path: str | Path) -> Dict[str, float]:
    """Aggregate a DIA-NN report.tsv to protein-group intensities (max PG quant)."""
    import pandas as pd

    df = _read_table(Path(report_path))
    prot_col = next((c for c in ("Protein.Group", "Protein.Ids", "Protein.Names") if c in df.columns), None)
    quant_col = next((c for c in ("PG.MaxLFQ", "PG.Quantity", "Precursor.Quantity") if c in df.columns), None)
    if prot_col is None or quant_col is None:
        raise RuntimeError(
            f"DIA-NN report missing protein/quant columns (have {list(df.columns)[:8]}...)"
        )
    df = df[[prot_col, quant_col]].dropna()
    df[quant_col] = pd.to_numeric(df[quant_col], errors="coerce")
    grouped = df.groupby(prot_col)[quant_col].max()
    return {str(k): float(v) for k, v in grouped.items() if v == v}  # drop NaN


def parse_sage_quant(lfq_path: str | Path) -> Dict[str, float]:
    """Aggregate a Sage ``lfq.tsv`` to protein intensities (sum over peptides/columns).

    Sage LFQ output is a wide table keyed by protein (and peptide) with one intensity
    column per run. For a single-sample run we take the protein rows and sum the numeric
    intensity column(s), summing peptide rows to the protein where a protein column exists.
    """
    import pandas as pd

    df = _read_table(Path(lfq_path))
    cols_lower = {c.lower(): c for c in df.columns}
    prot_col = next(
        (cols_lower[c] for c in ("proteins", "protein", "protein_id", "protein_group") if c in cols_lower),
        None,
    )
    if prot_col is None:
        raise RuntimeError(f"Sage lfq.tsv missing a protein column (have {list(df.columns)[:8]}...)")
    # Intensity columns: numeric columns that are not identifier/metadata columns.
    id_like = {prot_col.lower(), "peptide", "stripped_peptide", "charge", "q_value", "score", "spectral_angle"}
    intensity_cols = [
        c for c in df.columns
        if c.lower() not in id_like and pd.api.types.is_numeric_dtype(pd.to_numeric(df[c], errors="coerce"))
    ]
    if not intensity_cols:
        raise RuntimeError("Sage lfq.tsv has no numeric intensity columns")
    work = df[[prot_col] + intensity_cols].copy()
    for c in intensity_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)
    work["_intensity"] = work[intensity_cols].sum(axis=1)
    grouped = work.groupby(prot_col)["_intensity"].sum()
    return {str(k): float(v) for k, v in grouped.items() if v > 0}


def parse_panel_matrix(
    panel_path: str | Path,
    sample_id: str,
    *,
    source: str = "open",
) -> Dict[str, float]:
    """Extract one sample's protein->value map from an Olink NPX / SomaScan RFU / open matrix.

    Supports long format (columns for sample, feature, value) and wide format
    (rows = samples indexed by an id column, columns = features).
    """
    import pandas as pd

    df = _read_table(Path(panel_path))
    cols_lower = {c.lower(): c for c in df.columns}

    # Long format: sample + feature + value columns (Olink NPX export is typically long).
    sample_col = next((cols_lower[c] for c in ("sampleid", "sample_id", "sample") if c in cols_lower), None)
    feature_col = next(
        (cols_lower[c] for c in ("olinkid", "assay", "uniprot", "seqid", "feature_id", "protein", "target") if c in cols_lower),
        None,
    )
    value_col = next((cols_lower[c] for c in ("npx", "rfu", "value", "quantity", "intensity") if c in cols_lower), None)

    if sample_col and feature_col and value_col:
        sub = df[df[sample_col].astype(str) == str(sample_id)]
        if sub.empty:
            raise RuntimeError(f"sample {sample_id!r} not found in panel {panel_path}")
        out: Dict[str, float] = {}
        for _, row in sub.iterrows():
            try:
                out[str(row[feature_col])] = float(row[value_col])
            except (ValueError, TypeError):
                continue
        return out

    # Wide format: first column is the sample id, remaining columns are features.
    id_col = sample_col or df.columns[0]
    row = df[df[id_col].astype(str) == str(sample_id)]
    if row.empty:
        raise RuntimeError(f"sample {sample_id!r} not found in panel {panel_path}")
    r = row.iloc[0]
    out = {}
    for c in df.columns:
        if c == id_col:
            continue
        try:
            out[str(c)] = float(r[c])
        except (ValueError, TypeError):
            continue
    return out


def register_sample_abundance(
    *,
    sample_dir: str | Path,
    sample_id: str,
    source: str = "diann",
    panel_path: Optional[str | Path] = None,
    panel_format: str = "open",
) -> Dict[str, Any]:
    """Normalize a proteomics quantifier output into ``{sample_id}.abundance.h5``."""
    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    src = str(source).lower()
    if src in ("diann", "dia"):
        report = sample_path / f"{sample_id}.diann" / "report.tsv"
        if not report.is_file():
            raise RuntimeError(f"DIA-NN report not found for {sample_id}: {report}")
        values = parse_diann_report(report)
        source_label = "diann"
    elif src in ("sage", "dda"):
        lfq = sample_path / f"{sample_id}.sage" / "lfq.tsv"
        if not lfq.is_file():
            raise RuntimeError(f"Sage lfq.tsv not found for {sample_id}: {lfq}")
        values = parse_sage_quant(lfq)
        source_label = "sage"
    elif src == "panel":
        if not panel_path:
            raise RuntimeError("panel ingest requires panel_path")
        values = parse_panel_matrix(panel_path, sample_id, source=panel_format)
        source_label = f"panel:{panel_format}"
    else:
        raise RuntimeError(f"unknown proteomics source: {source!r}")

    result = write_sample_features(
        sample_dir=sample_path,
        sample_id=sample_id,
        kind=_KIND,
        values=values,
        source=source_label,
    )
    return {
        "sampleId": str(sample_id),
        "abundanceH5": result["featureH5"],
        "n_proteins": result["n_features"],
        "source": source_label,
    }
