"""
Pluggable registry of CIS-BP integration modes.

Each handler has the signature ``run(cfg, genes, output_dir, context) -> Optional[str]``
and returns the library label to merge into enrichment results (or ``None`` when
nothing was produced). New modes (1B annotate, 1C motif_scan) plug in here.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from . import annotate, motif_scan
from .gene_sets import resolve_gmt, run_gene_set_ora

ModeHandler = Callable[..., Optional[str]]


def _run_gene_sets(cfg, genes: Sequence[str], output_dir, context) -> Optional[str]:  # noqa: ANN001
    label = cfg.label or "CIS-BP"
    gene_universe = context.gene_universe or {str(g).strip() for g in genes if str(g).strip()}
    gmt, _gmt_path = resolve_gmt(
        cfg,
        cache_dir=context.cache_dir,
        gene_universe=gene_universe,
        gtf=context.gtf,
        genome_fasta=context.genome_fasta,
    )
    background = cfg.background_size or context.background or 20000
    n_terms, _path = run_gene_set_ora(
        genes=genes,
        gmt=gmt,
        output_dir=output_dir,
        label=label,
        background=background,
        cutoff=context.cutoff,
    )
    return label if n_terms > 0 else None


_MODE_HANDLERS: Dict[str, ModeHandler] = {
    "gene_sets": _run_gene_sets,
    "annotate": annotate.run,
    "motif_scan": motif_scan.run,
}


def available_modes() -> List[str]:
    return sorted(_MODE_HANDLERS)


def get_mode_handler(mode: str) -> ModeHandler:
    key = (mode or "gene_sets").strip().lower()
    if key not in _MODE_HANDLERS:
        valid = ", ".join(available_modes())
        raise ValueError(f"Unknown CIS-BP mode '{mode}'. Valid modes: {valid}.")
    return _MODE_HANDLERS[key]
