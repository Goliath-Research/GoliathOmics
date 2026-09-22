"""Proteomics adapter over the shared omics_features seam."""

from .ingest import (
    parse_diann_report,
    parse_panel_matrix,
    parse_sage_quant,
    register_sample_abundance,
)
from .de_select import run_protein_de_select

__all__ = [
    "parse_diann_report",
    "parse_panel_matrix",
    "parse_sage_quant",
    "register_sample_abundance",
    "run_protein_de_select",
]
