"""Shared helpers for in-process action handlers."""

from __future__ import annotations

from typing import Any, Dict


def resolve_reference_fasta(input_json: Dict[str, Any]) -> str:
    """Resolve linear reference FASTA for alignment/extract workers.

    Prefer an explicit task field, then site ``reference_genome.fasta``. Do **not**
    treat ``input_json.resolvedConfig`` as ``alignment_qc`` when it is the
    Parabricks / methylGrapher action slice (Universal Action Input Contract).
    """
    for key in ("referenceFasta", "reference_fasta", "genome_fasta"):
        raw = input_json.get(key)
        if raw not in (None, ""):
            return str(raw)

    from methyl_utils.action_config_resolver import load_site_manifest, resolve_action_config

    site_path = input_json.get("siteConfigPath")
    site = load_site_manifest(str(site_path) if site_path else None)
    ref_block = site.get("reference_genome") if isinstance(site.get("reference_genome"), dict) else {}
    if ref_block.get("fasta"):
        return str(ref_block["fasta"])

    # Rebuild alignment_qc / methyl_extract from site+profile layers without
    # mistaking the task's action-specific resolvedConfig for those slices.
    project = input_json.get("projectPath") or input_json.get("project")
    regulatory: Dict[str, Any] = {}
    profile_ac: Dict[str, Any] = {}
    if isinstance(input_json.get("actionConfig"), dict):
        profile_ac = dict(input_json["actionConfig"])
    if project:
        from methyl_utils import load_project

        regulatory = load_project(str(project)).get_regulatory_config()

    alignment_cfg = resolve_action_config(
        "alignment_qc", site=site, profile_action_config=profile_ac, regulatory=regulatory
    )
    methyl_cfg = resolve_action_config(
        "methyl_extract", site=site, profile_action_config=profile_ac, regulatory=regulatory
    )
    reference_raw = methyl_cfg.get("reference_fasta") or alignment_cfg.get("genome_fasta")
    if not reference_raw:
        raise RuntimeError(
            "reference genome is required in site reference_genome.fasta or "
            "profile/site actionConfig.alignment_qc / methyl_extract"
        )
    return str(reference_raw)
