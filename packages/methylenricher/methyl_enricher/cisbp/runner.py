"""
Entry point that dispatches a CIS-BP run to the configured mode handler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set, Union

from methyl_utils.analyte_profiles import cisbp_mode_label, resolve_cisbp_modes

from .registry import get_mode_handler

logger = logging.getLogger(__name__)


@dataclass
class CisbpContext:
    """Runtime context passed to CIS-BP mode handlers."""

    cache_dir: Path = field(default_factory=lambda: Path.home() / ".methyl_enricher")
    gtf: Optional[str] = None
    genome_fasta: Optional[str] = None
    gene_universe: Optional[Set[str]] = None
    dmp_detection_dir: Optional[str] = None
    background: Optional[int] = None
    cutoff: float = 0.05

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)


def run_cisbp(
    cfg,
    genes: Sequence[str],
    output_dir,
    context: Optional[CisbpContext] = None,
) -> Union[None, str, List[str]]:
    """
    Run the configured CIS-BP integration mode(s).

    Returns library label(s) to merge into enrichment results, or ``None`` when
    disabled or nothing was produced. Multiple modes (``cisbp_modes``) return a
    list of labels in run order.
    """
    if cfg is None or not getattr(cfg, "enabled", False):
        return None
    context = context or CisbpContext()
    output_dir = Path(output_dir)
    base_label = cfg.label or "CIS-BP"
    modes = resolve_cisbp_modes(cfg)

    labels: List[str] = []
    for mode in modes:
        mode_label = cisbp_mode_label(mode, base_label)
        sub_cfg = cfg
        if hasattr(cfg, "model_copy"):
            sub_cfg = cfg.model_copy(update={"mode": mode, "label": mode_label})
        else:
            sub_cfg = cfg
            setattr(sub_cfg, "mode", mode)
            setattr(sub_cfg, "label", mode_label)

        handler = get_mode_handler(mode)
        logger.info("[CIS-BP] running mode '%s' (label=%s)", mode, mode_label)
        result = handler(sub_cfg, genes, output_dir, context)
        if result:
            labels.append(result)

    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return labels
