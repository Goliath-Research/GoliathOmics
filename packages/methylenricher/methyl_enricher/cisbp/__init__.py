"""
CIS-BP transcription-factor motif integration for MethylEnricher.

CIS-BP (https://cisbp.ccbr.utoronto.ca) is a catalog of transcription factors
and their DNA-binding motifs (PWMs). It is *not* an Enrichr-style gene-set
service, so this subpackage adapts CIS-BP into the enricher in a pluggable way.

Modes (selected via ``step_config.enricher.cisbp.mode``):
  * ``gene_sets``  (1A, implemented): TF -> target-gene over-representation.
  * ``annotate``   (1B):              annotate result TFs with CIS-BP metadata.
  * ``motif_scan`` (1C):              motif enrichment over DMP/DMR sequences.

The public entry point is :func:`run_cisbp`, invoked by
``EnrichmentAnalyzer.run_enrichment`` when CIS-BP is enabled.
"""

from .runner import run_cisbp, CisbpContext
from .registry import available_modes, get_mode_handler
from .context import effective_cisbp_config, resolve_cisbp_context

__all__ = [
    "run_cisbp",
    "CisbpContext",
    "available_modes",
    "get_mode_handler",
    "effective_cisbp_config",
    "resolve_cisbp_context",
]
