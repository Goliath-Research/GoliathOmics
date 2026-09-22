"""Resolve gene FeatureCuts resource caps from merged configuration layers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union


def load_mc_config_gene_caps(run_dir: Union[str, Path]) -> Dict[str, Any]:
    """Read gene FC caps from ``monte_carlo_runs/queue/mc_config.json`` when present."""
    rd = Path(run_dir)
    mc_root = rd.parent if rd.name.startswith("run_") else rd
    path = mc_root / "queue" / "mc_config.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_positive_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def resolve_gene_featurecuts_caps(
    *,
    max_genes: Optional[int] = None,
    max_dmps: Optional[int] = None,
    resolved_config: Optional[Mapping[str, Any]] = None,
    run_dir: Optional[Union[str, Path]] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Resolve gene FC caps from task input, resolved profile, and MC snapshot.

    Returns ``(None, None)`` when no layer supplies a cap — callers must not invent defaults.
    """
    genes = _coerce_positive_int(max_genes)
    dmps = _coerce_positive_int(max_dmps)

    resolved = dict(resolved_config or {})
    if genes is None:
        genes = _coerce_positive_int(
            resolved.get("max_genes") or resolved.get("stability_gene_featurecuts_max_genes")
        )
    if dmps is None:
        dmps = _coerce_positive_int(
            resolved.get("max_dmps") or resolved.get("stability_gene_featurecuts_max_dmps")
        )

    if run_dir is not None and (genes is None or dmps is None):
        mc = load_mc_config_gene_caps(run_dir)
        if genes is None:
            genes = _coerce_positive_int(
                mc.get("stability_gene_featurecuts_max_genes") or mc.get("gene_featurecuts_max_genes")
            )
        if dmps is None:
            dmps = _coerce_positive_int(mc.get("stability_gene_featurecuts_max_dmps"))

    return genes, dmps
