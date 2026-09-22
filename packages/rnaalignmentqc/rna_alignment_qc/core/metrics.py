"""Parse RNA-Seq quantifier outputs into a normalized metrics dict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _parse_star_log(path: Path) -> Dict[str, Any]:
    """Parse STAR Log.final.out into input reads, unique/multimap rates, mapping rate."""
    metrics: Dict[str, Any] = {}
    input_reads: Optional[int] = None
    unique_pct: Optional[float] = None
    multi_pct: Optional[float] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "|" not in raw:
            continue
        key, _, value = raw.partition("|")
        key = key.strip()
        value = value.strip()
        try:
            if key == "Number of input reads":
                input_reads = int(value)
            elif key == "Uniquely mapped reads %":
                unique_pct = float(value.rstrip("%"))
            elif key == "% of reads mapped to multiple loci":
                multi_pct = float(value.rstrip("%"))
        except ValueError:
            continue
    if input_reads is not None:
        metrics["input_reads"] = input_reads
    if unique_pct is not None:
        metrics["uniquely_mapped_rate"] = round(unique_pct / 100.0, 6)
    if unique_pct is not None and multi_pct is not None:
        metrics["mapping_rate"] = round((unique_pct + multi_pct) / 100.0, 6)
    return metrics


def _parse_kallisto_run_info(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    metrics: Dict[str, Any] = {}
    processed = data.get("n_processed")
    pseudo = data.get("n_pseudoaligned")
    if processed is not None:
        metrics["input_reads"] = int(processed)
    if data.get("p_pseudoaligned") is not None:
        metrics["pseudoalignment_rate"] = round(float(data["p_pseudoaligned"]) / 100.0, 6)
    elif processed and pseudo is not None and int(processed) > 0:
        metrics["pseudoalignment_rate"] = round(int(pseudo) / int(processed), 6)
    return metrics


def _count_genes_detected_from_counts(path: Path) -> int:
    n = 0
    with open(path, encoding="utf-8") as handle:
        header = handle.readline()
        del header
        for row in handle:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                if float(parts[1]) > 0:
                    n += 1
            except ValueError:
                continue
    return n


def _count_genes_detected_from_abundance(path: Path) -> int:
    """Count transcripts with est_counts > 0 (proxy for detected features)."""
    n = 0
    with open(path, encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
        try:
            est_idx = header.index("est_counts")
        except ValueError:
            est_idx = 3
        for row in handle:
            parts = row.rstrip("\n").split("\t")
            if len(parts) <= est_idx:
                continue
            try:
                if float(parts[est_idx]) > 0:
                    n += 1
            except ValueError:
                continue
    return n


def collect_rna_metrics(sample_dir: Path, sample_id: str) -> Dict[str, Any]:
    """Gather available metrics from STAR/kallisto outputs under the sample dir."""
    metrics: Dict[str, Any] = {}
    quant_mode: Optional[str] = None

    star_log = sample_dir / f"{sample_id}.star" / "Log.final.out"
    if not star_log.is_file():
        found = list(sample_dir.glob(f"{sample_id}*Log.final.out"))
        star_log = found[0] if found else star_log
    if star_log.is_file():
        quant_mode = "star"
        metrics.update(_parse_star_log(star_log))

    run_info = sample_dir / f"{sample_id}.kallisto" / "run_info.json"
    if run_info.is_file():
        quant_mode = quant_mode or "kallisto"
        metrics.update(_parse_kallisto_run_info(run_info))

    gene_counts = sample_dir / f"{sample_id}.gene_counts.tsv"
    abundance = sample_dir / f"{sample_id}.kallisto" / "abundance.tsv"
    if gene_counts.is_file():
        quant_mode = quant_mode or "star"
        metrics["genes_detected"] = _count_genes_detected_from_counts(gene_counts)
    elif abundance.is_file():
        quant_mode = quant_mode or "kallisto"
        metrics["genes_detected"] = _count_genes_detected_from_abundance(abundance)

    metrics["quant_mode"] = quant_mode
    return metrics
