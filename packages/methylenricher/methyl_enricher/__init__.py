"""
MethylEnricher - Gene enrichment analysis for methylation DMPs

This package provides tools for performing Over-Representation Analysis (ORA)
on gene lists derived from Differentially Methylated Positions (DMPs).
"""

from .enricher import run_enrichment, EnrichmentAnalyzer
from .module_pipeline import run_module_pipeline
from .pathway_normalizer import PathwayNormalizer

__version__ = "0.1.0"
__all__ = [
    "run_enrichment",
    "EnrichmentAnalyzer",
    "run_module_pipeline",
    "PathwayNormalizer",
]

