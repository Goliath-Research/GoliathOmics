"""
Resolve per-action configuration from site manifest, pipeline profile, and overrides.

Study manifests (project JSON) no longer carry tool parameters.

Merge order (later overlays win via deep_merge; analyte is fill-missing-only):

  site actionConfig → profile actionConfig → program/instance overlay → analyte fill-missing

JSON ``null`` in an overlay deletes that key from the merged result (clear / uncap).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional

ActionConfigKey = Literal[
    "centroid",
    "detection",
    "dmp_selection",
    "mapper",
    "enricher",
    "classifier",
    "gene_selection",
    "predictor",
    "alignment_qc",
    "extraction_qc",
    "fragmentomics",
    "methyl_extract",
    "validation",
    "progression",
    "parabricks",
    "docker_align",
    "demultiplex",
    "rna_align",
    "rna_qc",
    "rna_de_select",
    "proteomics_quant",
    "proteomics_qc",
    "protein_de_select",
    "residualize",
    "methylation_confounder_scores",
]

DEFAULT_SITE_PATH = Path("/work/site/methyl_site.json")

SITE_ENV = "METHYL_SITE_CONFIG"
PROFILE_ENV = "METHYL_PROFILE"


def deep_merge(base: Dict[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge ``overlay`` onto ``base``.

    Overlay ``None`` (JSON ``null``) removes the key from the result so operators
    can clear / uncap site or profile knobs. Nested dicts merge recursively;
    lists and other scalars replace wholesale.
    """
    out = copy.deepcopy(base)
    for key, val in overlay.items():
        if val is None:
            out.pop(key, None)
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(dict(out[key]), val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def load_site_manifest(path: str | Path | None = None) -> Dict[str, Any]:
    raw = path or os.environ.get(SITE_ENV) or str(DEFAULT_SITE_PATH)
    p = Path(str(raw)).expanduser()
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_profile_action_config(name_or_path: str | Path | None = None) -> Dict[str, Any]:
    if name_or_path is None:
        name_or_path = os.environ.get(PROFILE_ENV)
    if not name_or_path:
        return {}
    from .profile_paths import resolve_profile_path

    try:
        profile_path = resolve_profile_path(name_or_path)
    except FileNotFoundError:
        return {}
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    action = data.get("actionConfig") or data.get("step_config_overrides") or {}
    return dict(action) if isinstance(action, dict) else {}


def site_slice_for_action(site: Mapping[str, Any], action_key: str) -> Dict[str, Any]:
    """Extract action-specific defaults from a site manifest."""
    ac = site.get("actionConfig")
    # methylgrapher_wgbs: merge genome bundle paths with actionConfig knobs (image/engine).
    # Early-return of actionConfig alone would drop pangenome_wgbs_genome fill-ins when
    # operators pin only image/engine under actionConfig (or the reverse).
    if action_key == "methylgrapher_wgbs":
        out: Dict[str, Any] = {}
        wgbs = site.get("pangenome_wgbs_genome") or {}
        if isinstance(wgbs, dict) and wgbs:
            out = deep_merge(out, dict(wgbs))
        site_mg = ac.get("methylgrapher_wgbs") if isinstance(ac, dict) else None
        if isinstance(site_mg, dict):
            out = deep_merge(out, dict(site_mg))
        ref = site.get("reference_genome") or {}
        pangenome = site.get("pangenome_genome") or {}
        linear_fasta = pangenome.get("linear_ref_fasta") or ref.get("fasta")
        if not out.get("linear_ref_fasta") and linear_fasta:
            out["linear_ref_fasta"] = linear_fasta
        return out

    # Same merge as WGBS: actionConfig.methyl_extract knobs must not drop
    # reference_genome.fasta / pangenome linear_ref_fasta.
    if action_key == "methyl_extract":
        out = {}
        ref = site.get("reference_genome") or {}
        pangenome = site.get("pangenome_genome") or {}
        linear_fasta = pangenome.get("linear_ref_fasta") or ref.get("fasta")
        if linear_fasta:
            out["reference_fasta"] = linear_fasta
            out["genome_fasta"] = linear_fasta
        legacy = site.get("methyl_extract")
        if isinstance(legacy, dict):
            out = deep_merge(out, dict(legacy))
        site_me = ac.get("methyl_extract") if isinstance(ac, dict) else None
        if isinstance(site_me, dict):
            out = deep_merge(out, dict(site_me))
        return out

    if isinstance(ac, dict) and action_key in ac and isinstance(ac[action_key], dict):
        return dict(ac[action_key])

    out = {}
    ref = site.get("reference_genome") or {}
    ann = site.get("annotation") or {}
    caches = site.get("caches") or {}
    pangenome = site.get("pangenome_genome") or {}

    linear_fasta = pangenome.get("linear_ref_fasta") or ref.get("fasta")

    if action_key == "alignment_qc" and linear_fasta:
        out["genome_fasta"] = linear_fasta
        out["reference_fasta"] = linear_fasta
    if action_key == "mapper":
        if ann.get("gtf"):
            out["gtf"] = ann["gtf"]
        mm_home = site.get("methyl_mapper_home") or caches.get("methyl_mapper")
        if mm_home:
            out["methyl_mapper_home"] = mm_home
    if action_key == "enricher":
        nr = caches.get("string_edges")
        if nr:
            out.setdefault("network_refinement", {})["cache_path"] = nr
    if action_key == "parabricks" and site.get("parabricks"):
        out.update(dict(site["parabricks"]))
    rna_ref = site.get("rna_reference") or {}
    if isinstance(rna_ref, dict) and rna_ref:
        if action_key in ("rna_align", "rna_qc"):
            for key in ("star_index_dir", "gtf", "reference_fasta", "kallisto_index", "transcriptome_fasta", "tx2gene"):
                if rna_ref.get(key):
                    out.setdefault(key, rna_ref[key])
        if action_key == "rna_align" and site.get("parabricks"):
            for key, val in dict(site["parabricks"]).items():
                out.setdefault(key, val)
    prot_ref = site.get("proteomics_reference") or {}
    if isinstance(prot_ref, dict) and prot_ref:
        if action_key in ("proteomics_quant", "proteomics_qc"):
            for key in ("protein_fasta", "spectral_library", "prosit_model", "casanovo_model"):
                if prot_ref.get(key):
                    out.setdefault(key, prot_ref[key])
    return out


def resolve_proteomics_reference(site: Mapping[str, Any] | None = None) -> Dict[str, str]:
    """Resolve proteomics reference assets from site manifest ``proteomics_reference``.

    Returns whatever keys the operator pinned (protein FASTA, spectral/DIA library,
    Prosit/Casanovo model weights). Callers validate the subset they need per tool.
    """
    data = dict(site or load_site_manifest())
    bundle = data.get("proteomics_reference") or {}
    if not isinstance(bundle, dict):
        return {}
    return {str(k): str(v) for k, v in bundle.items() if v not in (None, "")}


def resolve_rna_reference(site: Mapping[str, Any] | None = None) -> Dict[str, str]:
    """Resolve RNA-Seq reference assets from site manifest ``rna_reference``.

    Returns whatever keys the operator pinned. Callers validate the subset they
    need for the selected quantifier (STAR vs kallisto) so a site can ship only
    one path.
    """
    data = dict(site or load_site_manifest())
    bundle = data.get("rna_reference") or {}
    if not isinstance(bundle, dict):
        return {}
    return {str(k): str(v) for k, v in bundle.items() if v not in (None, "")}


def resolve_pangenome_genome(site: Mapping[str, Any] | None = None) -> Dict[str, str]:
    """
    Resolve pangenome graph bundle paths from site manifest ``pangenome_genome``.

    Required keys: gbz, dist, min, zipcodes, ref_paths, linear_ref_fasta.
    """
    data = dict(site or load_site_manifest())
    bundle = data.get("pangenome_genome") or {}
    if not isinstance(bundle, dict):
        bundle = {}
    required = ("gbz", "dist", "min", "zipcodes", "ref_paths", "linear_ref_fasta")
    resolved: Dict[str, str] = {}
    for key in required:
        raw = bundle.get(key)
        if raw in (None, ""):
            raise RuntimeError(
                f"site manifest pangenome_genome.{key} is required for pangenome alignment "
                f"(METHYL_SITE_CONFIG / reference_genome.fasta alone is insufficient)"
            )
        resolved[key] = str(raw)
    return resolved


_WGBS_INDEX_KEYS = ("gbz", "dist", "min", "zipcodes")


def resolve_methylgrapher_wgbs_genome(
    site: Mapping[str, Any] | None = None,
    *,
    resolved_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve methylGrapher C2T/G2A BS pangenome assets.

    Preference order:
    1. Explicit ``resolved_config`` (worker path — already baked)
    2. site ``actionConfig.methylgrapher_wgbs``
    3. site top-level ``pangenome_wgbs_genome``

    Never falls back to stock ``pangenome_genome`` (Giraffe) indexes.
    """
    data = dict(site or {})
    if not data and resolved_config is None:
        data = dict(load_site_manifest())

    cfg: Dict[str, Any] = {}
    if isinstance(resolved_config, Mapping) and resolved_config:
        cfg = dict(resolved_config)
    else:
        ac = data.get("actionConfig") or {}
        if isinstance(ac, Mapping) and isinstance(ac.get("methylgrapher_wgbs"), Mapping):
            cfg = dict(ac["methylgrapher_wgbs"])
        bundle = data.get("pangenome_wgbs_genome") or {}
        if isinstance(bundle, Mapping) and bundle:
            # Top-level site bundle fills missing keys only.
            for key, val in bundle.items():
                cfg.setdefault(key, val)

    def _index(side: str) -> Dict[str, str]:
        raw = cfg.get(side)
        if not isinstance(raw, Mapping):
            raw = {}
        out: Dict[str, str] = {}
        missing = []
        for key in _WGBS_INDEX_KEYS:
            # Allow flat keys: c2t_gbz / g2a_dist
            flat = cfg.get(f"{side}_{key}")
            val = raw.get(key) if raw.get(key) not in (None, "") else flat
            if val in (None, ""):
                missing.append(f"{side}.{key}")
            else:
                out[key] = str(val)
        if missing:
            raise RuntimeError(
                "methylGrapher WGBS pangenome assets missing: "
                + ", ".join(missing)
                + " (pin actionConfig.methylgrapher_wgbs or pangenome_wgbs_genome; "
                "do not fall back to stock pangenome_genome)"
            )
        return out

    required_scalars = ("ref_paths", "cpg_tsv", "linear_ref_fasta")
    resolved: Dict[str, Any] = {
        "c2t": _index("c2t"),
        "g2a": _index("g2a"),
    }
    missing_scalars = []
    for key in required_scalars:
        raw = cfg.get(key)
        if raw in (None, ""):
            missing_scalars.append(key)
        else:
            resolved[key] = str(raw)
    if missing_scalars:
        raise RuntimeError(
            "methylGrapher WGBS pangenome assets missing: "
            + ", ".join(missing_scalars)
            + " (pin actionConfig.methylgrapher_wgbs / pangenome_wgbs_genome)"
        )
    for optional in (
        "original_gbz",
        "node_replacement_json",
        "index_prefix",
        "image",
        "image_digest",
        "methylgrapher_version",
        "vg_version",
        "linear_cpg_tsv",
        "wl_gfa",
        "engine",
        "align_engine",
        "alignment_mode",
        "gpu_giraffe_fallback",
        "giraffe_device",
        "align_device",
        "mojo_segments_cache",
        "modular_cache_dir",
        "qc_bam_engine",
        "qc_bam_fallback",
        "conversion_rate_sidecar",
    ):
        if cfg.get(optional) not in (None, ""):
            resolved[optional] = str(cfg[optional])
    if "directional" in cfg and cfg["directional"] is not None:
        resolved["directional"] = bool(cfg["directional"])
    if cfg.get("threads") is not None:
        resolved["threads"] = int(cfg["threads"])
    if cfg.get("batch_size") is not None:
        resolved["batch_size"] = int(cfg["batch_size"])
    if "cg_only" in cfg and cfg["cg_only"] is not None:
        resolved["cg_only"] = bool(cfg["cg_only"])
    if "mojo_giraffe_ready" in cfg and cfg["mojo_giraffe_ready"] is not None:
        resolved["mojo_giraffe_ready"] = bool(cfg["mojo_giraffe_ready"])
    if "dual_graph_parallel" in cfg and cfg["dual_graph_parallel"] is not None:
        resolved["dual_graph_parallel"] = bool(cfg["dual_graph_parallel"])
    if "conversion_rate_enabled" in cfg and cfg["conversion_rate_enabled"] is not None:
        resolved["conversion_rate_enabled"] = bool(cfg["conversion_rate_enabled"])
    if isinstance(cfg.get("read_level"), Mapping):
        resolved["read_level"] = dict(cfg["read_level"])
    if isinstance(cfg.get("contexts"), list):
        resolved["contexts"] = [str(c) for c in cfg["contexts"]]
    return resolved


def resolve_action_config(
    action_key: str,
    *,
    site: Optional[Mapping[str, Any]] = None,
    profile_action_config: Optional[Mapping[str, Any]] = None,
    program_override: Optional[Mapping[str, Any]] = None,
    instance_override: Optional[Mapping[str, Any]] = None,
    regulatory: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge configuration layers for one action catalog section."""
    site_data = dict(site or {})
    profile_all = dict(profile_action_config or {})
    merged: Dict[str, Any] = {}
    merged = deep_merge(merged, site_slice_for_action(site_data, action_key))
    section = profile_all.get(action_key)
    if isinstance(section, dict):
        merged = deep_merge(merged, section)
    for overlay in (program_override, instance_override):
        if isinstance(overlay, dict):
            merged = deep_merge(merged, overlay)

    from .analyte_profiles import merge_step_config, normalize_primary_analyte, should_apply_analyte_profile

    reg = dict(regulatory or {})
    if should_apply_analyte_profile(reg):
        analyte = normalize_primary_analyte(reg.get("primary_analyte"))
        merged = merge_step_config(action_key, merged, analyte)
    return merged


def resolve_action_config_from_env(
    action_key: str,
    *,
    regulatory: Optional[Mapping[str, Any]] = None,
    program_override: Optional[Mapping[str, Any]] = None,
    profile_path: str | Path | None = None,
    site_path: str | Path | None = None,
) -> Dict[str, Any]:
    site = load_site_manifest(site_path)
    profile = load_profile_action_config(profile_path)
    return resolve_action_config(
        action_key,
        site=site,
        profile_action_config=profile,
        program_override=program_override,
        regulatory=regulatory,
    )


def resolve_from_task_input(
    action_key: str,
    input_json: Mapping[str, Any],
    *,
    regulatory: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve config for a worker task (materialized or built at claim time)."""
    if isinstance(input_json.get("resolvedConfig"), dict):
        base = dict(input_json["resolvedConfig"])
        override = input_json.get("stepOverride")
        if isinstance(override, dict):
            return deep_merge(base, override)
        return base

    ctx = input_json.get("actionConfig")
    site = input_json.get("siteConfig")
    if isinstance(ctx, dict) and action_key in ctx:
        profile_slice = {action_key: ctx[action_key]}
    elif isinstance(ctx, dict):
        profile_slice = ctx
    else:
        profile_slice = load_profile_action_config(
            input_json.get("pipelineProfile") or input_json.get("profilePath")
        )

    site_data = site if isinstance(site, dict) else load_site_manifest(
        input_json.get("siteConfigPath")
    )
    return resolve_action_config(
        action_key,
        site=site_data,
        profile_action_config=profile_slice if isinstance(profile_slice, dict) else {},
        program_override=input_json.get("stepOverride") if isinstance(input_json.get("stepOverride"), dict) else None,
        regulatory=regulatory,
    )


def _load_json_mapping(path: str | Path) -> Dict[str, Any]:
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"resolved config JSON must be an object: {path}")
    return dict(raw)


def load_resolved_config(
    action_key: str,
    *,
    resolved_config_path: str | Path | None = None,
    resolved_config: Optional[Mapping[str, Any]] = None,
    step_override: Optional[Mapping[str, Any]] = None,
    step_override_path: str | Path | None = None,
) -> Dict[str, Any]:
    """
    Load a baked actionConfig slice from a worker ``--resolved-config`` file or dict.

    ``step_override`` / ``step_override_path`` overlay wins over the baked slice.
    Does not read project.json, profiles, or METHYL_* environment variables.
    """
    del action_key  # slice is already action-specific when passed from the worker
    if resolved_config_path not in (None, ""):
        cfg = _load_json_mapping(resolved_config_path)
    elif isinstance(resolved_config, dict):
        cfg = dict(resolved_config)
    else:
        raise ValueError("load_resolved_config requires resolved_config_path or resolved_config")

    overlay = dict(step_override) if isinstance(step_override, dict) else None
    if overlay is None and step_override_path not in (None, ""):
        overlay = _load_json_mapping(step_override_path)
    if overlay:
        return deep_merge(cfg, overlay)
    return cfg


def resolve_for_project(
    action_key: str,
    project: Any,
    *,
    step_override: Optional[Mapping[str, Any]] = None,
    resolved_config: Optional[Mapping[str, Any]] = None,
    resolved_config_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    site_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Resolve action config for standalone CLIs and package resolvers."""
    if resolved_config_path not in (None, "") or isinstance(resolved_config, dict):
        return load_resolved_config(
            action_key,
            resolved_config_path=resolved_config_path,
            resolved_config=resolved_config,
            step_override=step_override,
        )
    reg = project.get_regulatory_config() if hasattr(project, "get_regulatory_config") else {}
    return resolve_action_config_from_env(
        action_key,
        regulatory=reg,
        program_override=dict(step_override) if isinstance(step_override, dict) else None,
        profile_path=profile_path,
        site_path=site_path,
    )
