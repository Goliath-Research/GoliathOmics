"""Build gene-select runtime config from pipeline project JSON."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from ..caps import load_mc_config_gene_caps

_GENE_SELECTION_ALIASES: Dict[str, str] = {
    "target_balanced_accuracy": "stability_target_balanced_accuracy",
    "min_selected_genes": "stability_min_selected_genes",
    "max_genes": "stability_gene_featurecuts_max_genes",
    "max_dmps": "stability_gene_featurecuts_max_dmps",
    "dmp_source": "stability_gene_featurecuts_dmp_source",
    "biomarker_filter_enabled": "stability_gene_biomarker_filter_enabled",
    "biomarker_mode": "stability_gene_biomarker_mode",
    "biomarker_region_hits": "stability_gene_region_hits",
    "biomarker_top_genes": "stability_gene_biomarker_top_genes",
    "biomarker_ppi_top_hubs": "stability_gene_biomarker_ppi_top_hubs",
    "biomarker_min_degree": "stability_gene_biomarker_min_degree",
}

_VALIDATION_GENE_KEYS = (
    "stability_gene_featurecuts_enabled",
    "stability_gene_featurecuts_max_genes",
    "stability_gene_featurecuts_max_dmps",
    "stability_gene_featurecuts_dmp_source",
    "stability_gene_biomarker_filter_enabled",
    "stability_gene_biomarker_mode",
    "stability_gene_region_hits",
    "stability_gene_biomarker_top_genes",
    "stability_gene_biomarker_ppi_top_hubs",
    "stability_gene_biomarker_min_degree",
    "stability_gene_biomarker_ppi_cache_path",
    "stability_target_balanced_accuracy",
    "stability_min_selected_genes",
    "gene_featurecuts_target_ba",
    "gene_featurecuts_min_genes",
    "gene_featurecuts_max_genes",
    "gene_featurecuts_loci_source",
)

# MC snapshot / study overlays may use either canonical or alias names.
_MC_OVERLAY_KEYS = (
    "stability_gene_featurecuts_max_genes",
    "stability_gene_featurecuts_max_dmps",
    "stability_gene_featurecuts_dmp_source",
    "stability_gene_biomarker_filter_enabled",
    "stability_target_balanced_accuracy",
    "stability_min_selected_genes",
    "gene_featurecuts_target_ba",
    "gene_featurecuts_min_genes",
    "gene_featurecuts_max_genes",
    "gene_featurecuts_loci_source",
)


def _load_raw_action_config(project_path: Union[str, Path]) -> Dict[str, Any]:
    """Read study ``actionConfig`` from disk so explicit JSON null can clear caps."""
    try:
        raw = json.loads(Path(project_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    ac = raw.get("actionConfig") if isinstance(raw, dict) else None
    return dict(ac) if isinstance(ac, dict) else {}


def _apply_nullable_overlay(payload: Dict[str, Any], overlay: Mapping[str, Any], keys: Sequence[str]) -> None:
    """Apply overlay keys; ``null`` removes a previously merged site/profile cap."""
    for key in keys:
        if key not in overlay:
            continue
        val = overlay[key]
        if val is None:
            payload.pop(key, None)
        else:
            payload[key] = val


def _apply_gene_selection_aliases(payload: Dict[str, Any], gene_sel: Mapping[str, Any]) -> None:
    """Map gene_selection aliases; ``null`` clears the destination (same as deep_merge)."""
    for src, dst in _GENE_SELECTION_ALIASES.items():
        if src not in gene_sel:
            continue
        val = gene_sel[src]
        if val is None:
            payload.pop(dst, None)
            continue
        payload[dst] = val


def build_gene_select_config(
    project_path: Union[str, Path],
    *,
    run_dir: Optional[Union[str, Path]] = None,
    **overrides: Any,
) -> SimpleNamespace:
    """
    Merge site/profile gene_selection + validation, then study/MC overlays.

    Prefer baked ``resolvedConfig`` (null already deleted by ``deep_merge``). Legacy study
    ``actionConfig`` / MC snapshot ``null`` still clears caps via nullable overlay.
    """
    project = load_project(project_path)
    payload: Dict[str, Any] = {"stability_gene_featurecuts_enabled": True}

    gene_sel = dict(resolve_for_project("gene_selection", project))
    _apply_gene_selection_aliases(payload, gene_sel)

    validation = dict(resolve_for_project("validation", project))
    for key in _VALIDATION_GENE_KEYS:
        if key in validation:
            if validation[key] is None:
                payload.pop(key, None)
            else:
                payload[key] = validation[key]

    # Legacy study-local project.json actionConfig (explicit null clears site caps).
    study_ac = _load_raw_action_config(project_path)
    _apply_gene_selection_aliases(payload, dict(study_ac.get("gene_selection") or {}))
    _apply_nullable_overlay(payload, dict(study_ac.get("validation") or {}), _VALIDATION_GENE_KEYS)

    # MC snapshot for DomainProgram / methyl-validation iterations.
    if run_dir is not None:
        mc = load_mc_config_gene_caps(run_dir)
        _apply_nullable_overlay(payload, mc, _MC_OVERLAY_KEYS)
        # Alias gene_featurecuts_max_genes → stability cap when only the former is set.
        if "gene_featurecuts_max_genes" in mc and "stability_gene_featurecuts_max_genes" not in mc:
            _apply_nullable_overlay(
                payload, {"stability_gene_featurecuts_max_genes": mc.get("gene_featurecuts_max_genes")},
                ("stability_gene_featurecuts_max_genes",),
            )

    # CLI overrides: non-null set; callers that need uncapped omit the flag (do not pass null).
    payload.update({k: v for k, v in overrides.items() if v is not None})
    return SimpleNamespace(**payload)
