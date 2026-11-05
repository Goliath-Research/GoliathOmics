"""Application-owned pipeline profile presets (product extension; allowlisted)."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, cast


# Deprecated profile names → canonical file (preset flags also aliased in PROFILE_PRESETS).
_PROFILE_ALIASES: Dict[str, str] = {
    "buffy_mc_gene_fc": "mc_dmp_gene_fc",
    "gene_enricher_stability": "mc_dmp",
    "dmp_panel_stability": "mc_dmp_fc",
    "mc_dmp_discovery": "mc_dmp",
    "mc_dmp_featurecuts": "mc_dmp_fc",
    "mc_gene_mapper": "mc_gene",
    "mc_gene_featurecuts": "mc_gene_fc",
    "discovery_gene_featurecuts": "mc_dmp_gene_fc",
}

# Legacy mc_* research axes → samd_research + named mode overlay (profiles/modes/*.mode.json).
_SAMD_RESEARCH_MODES: Dict[str, str] = {
    "mc_dmp": "dmp_raw",
    "mc_dmp_fc": "dmp_fc",
    "mc_gene": "gene_enricher",
    "mc_gene_fc": "gene_fc",
    "mc_dmp_gene_fc": "dual_fc",
}

_RESEARCH_MODE_IDS = frozenset(_SAMD_RESEARCH_MODES.values()) | {"mhl_survival"}

# Profiles that fold into samd_research (cfg rows should be retired on sync).
_DEPRECATED_PROFILE_NAMES = frozenset(
    {
        *_PROFILE_ALIASES.keys(),
        *_SAMD_RESEARCH_MODES.keys(),
        "legacy_dual",
    }
)


def cfg_status_for_catalog(catalog: Mapping[str, Any] | None) -> Optional[str]:
    """Map catalog metadata to cfg status; see ``cfg.process_pack_catalog``."""
    from cfg.process_pack_catalog import cfg_status_for_catalog as _impl

    return _impl(catalog)


def catalog_meta_for(name_or_doc: str | Mapping[str, Any]) -> Dict[str, Any]:
    """Return catalog metadata for a profile name or document (empty if absent)."""
    if isinstance(name_or_doc, Mapping):
        cat = name_or_doc.get("catalog")
        return dict(cat) if isinstance(cat, Mapping) else {}
    key = str(name_or_doc).strip()
    if not key:
        return {}
    try:
        data = load_profile(key)
    except FileNotFoundError:
        return {}
    cat = data.get("catalog")
    return dict(cat) if isinstance(cat, Mapping) else {}


_STRING_SCOPE_KEYS = frozenset(
    {
        "researchMode",
        "dmp_modeling_mode",
        "gene_modeling_mode",
        "pipelineProcedure",
        "samplePrepProgram",
        "lifecycleProgram",
        "libraryProtocol",
        "alignmentMode",
    }
)

_PROCEDURE_SCOPE_BOOLS = frozenset(
    {
        "usePangenome",
        "useWgbsPangenome",
        "useEpiGbs",
        "useEmseqTargeted",
        "runDmpSelection",
        "runGeneFeaturecuts",
        "runBiomarkerFilter",
        "runGeneFeatureSelect",
        "runProgressionAnalysis",
        "stabilityFeaturecutsEnabled",
        "stabilityGeneFeaturecutsEnabled",
        "stabilityGeneBiomarkerFilterEnabled",
    }
)

# Scope keys owned by one omics modality. seed_pipeline_scope_flags writes them
# only for that modality and leaves them absent on methylation contexts.
_RNA_SCOPE_KEYS = ("quantMode", "useKallisto")
_PROTEOMICS_SCOPE_KEYS = ("ingestMode", "usePanel", "useDda", "useRescore")

# regulatory.primary_modality tokens. Aliases match the live study vocabulary
# (methylation | rnaseq | proteomics). Unknown tokens pass through so a future
# modality does not inherit RNA or proteomics defaults.
_MODALITY_ALIASES = {
    "methylation": "methylation",
    "wgbs": "methylation",
    "methyl": "methylation",
    "dna_methylation": "methylation",
    "bisulfite": "methylation",
    "rnaseq": "rnaseq",
    "rna_seq": "rnaseq",
    "rna": "rnaseq",
    "transcriptomics": "rnaseq",
    "transcriptome": "rnaseq",
    "proteomics": "proteomics",
    "proteome": "proteomics",
    "protein": "proteomics",
    "ms_proteomics": "proteomics",
    "mass_spec": "proteomics",
}

PIPELINE_FLAG_DEFAULTS: Dict[str, bool] = {
    "usePangenome": False,
    "useWgbsPangenome": False,
    "useEpiGbs": False,
    "useEmseqTargeted": False,
    "skipDemultiplex": False,
    "useKallisto": False,
    "usePanel": False,
    "useDda": False,
    "useRescore": False,
    "deleteFastqs": True,
    "runDmpSelection": False,
    "runGeneFeaturecuts": False,
    "runBiomarkerFilter": False,
    "runGeneFeatureSelect": False,
    "runProgressionAnalysis": False,
    "stabilityFeaturecutsEnabled": False,
    "stabilityGeneFeaturecutsEnabled": False,
    "stabilityGeneBiomarkerFilterEnabled": False,
}

PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
    "legacy_dual": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "discovery_interpretation": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
    },
    "gene_enricher_stability": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "dmp_panel_stability": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": False,
    },
    "full_biomarker_gene_fc": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "runBiomarkerFilter": True,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
        "stabilityGeneBiomarkerFilterEnabled": True,
    },
    "discovery_gene_featurecuts": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "structural_features": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": True,
    },
    "staged_ovr_mc": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "runProgressionAnalysis": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "staged_full_lifecycle": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "runProgressionAnalysis": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "mc_gene_fc": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "mc_dmp_gene_fc": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    # SaMD lifecycle ladder (healthy vs disease; partitions on study manifest)
    "samd_research": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "samd_holdout_enrichment": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "samd_pivotal": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    # Deprecated alias — use mc_dmp_gene_fc
    "buffy_mc_gene_fc": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": True,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    # Research stability profiles (process-agnostic; study facts live in project.json)
    "mc_dmp": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "mc_dmp_fc": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": False,
    },
    "mc_gene": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    # Statistical-mode profiles (deprecated names; aliases resolve to mc_* above)
    "mc_dmp_discovery": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "mc_dmp_featurecuts": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": False,
    },
    "mc_gene_mapper": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": False,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "mc_gene_featurecuts": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": True,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": True,
        "stabilityGeneBiomarkerFilterEnabled": False,
    },
    "mc_two_phase_dmp_then_gene": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": True,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": True,
    },
    "phase_a_dmp_stability": {
        "runDmpSelection": True,
        "runGeneFeaturecuts": False,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": True,
        "stabilityGeneFeaturecutsEnabled": False,
    },
    "phase_b_gene_from_stable_dmps": {
        "runDmpSelection": False,
        "runGeneFeaturecuts": True,
        "runBiomarkerFilter": False,
        "runGeneFeatureSelect": False,
        "stabilityFeaturecutsEnabled": False,
        "stabilityGeneFeaturecutsEnabled": True,
    },
}

def _mode_search_dirs() -> list[Path]:
    """Directories that may contain ``modes/<id>.mode.json`` (honors legacy profile dir env)."""
    profile_search_dirs = _profile_paths_module().profile_search_dirs

    dirs: list[Path] = []
    # Sibling of this module (repo checkout or runtime-bundle/domain on sys.path)
    dirs.append(Path(__file__).resolve().parent / "profiles")
    for d in profile_search_dirs():
        dirs.append(d)
    # Deduplicate while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        resolved = d.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def load_mode_overlay(mode_id: str) -> Dict[str, Any]:
    """Load a samd_research modeling-mode overlay (``…/profiles/modes/<id>.mode.json``)."""
    candidates: list[Path] = []
    for profiles_dir in _mode_search_dirs():
        path = profiles_dir / "modes" / f"{mode_id}.mode.json"
        candidates.append(path)
        if path.is_file():
            return _load_profile_file(path)
    known: list[str] = []
    for profiles_dir in _mode_search_dirs():
        modes_dir = profiles_dir / "modes"
        if modes_dir.is_dir():
            known.extend(p.stem for p in modes_dir.glob("*.mode.json"))
    known_u = sorted(set(known))
    raise FileNotFoundError(
        f"Unknown research mode {mode_id!r} (expected one of {known_u}; "
        f"searched: {', '.join(str(c) for c in candidates)})"
    )


def load_procedure(name_or_path: str | Path) -> Dict[str, Any]:
    """Load an assay procedure pack (``profiles/procedures/<id>.procedure.json``)."""
    resolve_procedure_path = _profile_paths_module().resolve_procedure_path

    path = resolve_procedure_path(name_or_path)
    data = _load_profile_file(path)
    proc_id = str(data.get("pipelineProcedure") or path.name.replace(".procedure.json", ""))
    data["pipelineProcedure"] = proc_id
    return data


def apply_pipeline_procedure(
    context: Dict[str, Any],
    procedure: Mapping[str, Any],
) -> Dict[str, Any]:
    """Merge a procedure pack under instance context (instance wins on conflicts).

    Procedure sits between profile and instance:
    ``instance → procedure → profile/mode → analyte → site``.
    """
    out = dict(context)
    proc_id = procedure.get("pipelineProcedure")
    if proc_id:
        out["pipelineProcedure"] = str(proc_id)

    for key in (
        "pipelineProfile",
        "researchMode",
        "samplePrepProgram",
        "lifecycleProgram",
        "libraryProtocol",
        "alignmentMode",
        "analyteExpectation",
    ):
        if key in procedure and procedure[key] is not None and key not in out:
            out[key] = procedure[key]

    for key in _PROCEDURE_SCOPE_BOOLS:
        if key in procedure and key not in out:
            out[key] = bool(procedure[key])

    proc_ac = procedure.get("actionConfig") or procedure.get("step_config_overrides") or {}
    if isinstance(proc_ac, dict) and proc_ac:
        existing = dict(out.get("actionConfig") or {})
        out["actionConfig"] = _pipeline_profiles_module()._deep_merge(dict(proc_ac), existing)

    # Mirror libraryProtocol into actionConfig when procedure pins it and instance
    # did not already set sample_prep.library_protocol.
    lib = out.get("libraryProtocol") or procedure.get("libraryProtocol")
    if lib:
        ac = dict(out.get("actionConfig") or {})
        sample_prep = dict(ac.get("sample_prep") or {})
        sample_prep.setdefault("library_protocol", str(lib))
        ac["sample_prep"] = sample_prep
        if procedure.get("alignmentMode") or out.get("alignmentMode"):
            parabricks = dict(ac.get("parabricks") or {})
            parabricks.setdefault(
                "alignment_mode",
                str(out.get("alignmentMode") or procedure.get("alignmentMode")),
            )
            ac["parabricks"] = parabricks
        out["actionConfig"] = ac

    return out


def validate_procedure_analyte(context: Mapping[str, Any]) -> None:
    """Ensure study ``regulatory.primary_analyte`` matches procedure expectation."""
    expected = context.get("analyteExpectation")
    if expected is None:
        proc_id = context.get("pipelineProcedure")
        if not proc_id:
            return
        try:
            expected = load_procedure(str(proc_id)).get("analyteExpectation")
        except FileNotFoundError:
            return
    if expected is None:
        return
    if isinstance(expected, str):
        allowed = {expected.strip().lower()}
    else:
        allowed = {str(x).strip().lower() for x in expected if str(x).strip()}
    if not allowed:
        return
    regulatory = context.get("regulatory") or {}
    if not isinstance(regulatory, Mapping):
        raise ValueError(
            f"pipelineProcedure {context.get('pipelineProcedure')!r} requires "
            f"regulatory.primary_analyte in {sorted(allowed)}"
        )
    actual = str(regulatory.get("primary_analyte") or "").strip().lower()
    if actual not in allowed:
        raise ValueError(
            f"pipelineProcedure {context.get('pipelineProcedure')!r} expects "
            f"regulatory.primary_analyte in {sorted(allowed)}, got {actual!r}"
        )


def load_samd_research_with_mode(
    mode_id: str,
    *,
    pipeline_profile: str = "samd_research",
) -> Dict[str, Any]:
    """Merge ``samd_research.profile.json`` with a named mode overlay.

    Mode overlays carry statistical axis knobs only. Legacy ``mc_*`` alias folds
    additionally reset SaMD research lifecycle flags (early-stop, holdout eval,
    regulatory stubs) so historical ``mc_*`` behavior is preserved. Prefer
    ``pipelineProfile: samd_research`` + ``researchMode`` so early-stop stays on.
    """
    resolve_profile_path = _profile_paths_module().resolve_profile_path

    pp = _pipeline_profiles_module()
    base = _load_profile_file(resolve_profile_path("samd_research"))
    merged = cast(Dict[str, Any], pp._deep_merge(base, load_mode_overlay(mode_id)))
    merged["pipelineProfile"] = pipeline_profile
    merged["researchMode"] = mode_id
    if pipeline_profile in _SAMD_RESEARCH_MODES:
        # Deprecated mc_* path: do not inherit samd_research early-stop / claim shell.
        ac = dict(merged.get("actionConfig") or {})
        val = dict(ac.get("validation") or {})
        val["stability_early_stop_enabled"] = False
        val["holdout_eval"] = False
        val["require_biological_review_for_model"] = False
        val["biological_review_confirmed"] = False
        val["regulatory"] = {
            "stage": "feasibility",
            "intended_use_summary": (
                f"Deprecated profile alias {pipeline_profile} folded into "
                f"samd_research mode {mode_id}."
            ),
            "allow_clinical_performance_claims": False,
            "claim_boundary": (
                "Research axis via samd_research mode overlay; not pivotal claims."
            ),
            # primary_analyte is study-owned; do not pin buffy_coat here.
        }
        val["validation_partitions"] = {
            "development_train": [],
            "internal_validation": [],
            "locked_test": [],
            "pivotal_validation": [],
            "post_market_monitoring": [],
            "independence_keys": ["sample_id", "patient_id", "site_id", "batch"],
        }
        ac["validation"] = val
        merged["actionConfig"] = ac
    return merged


def load_profile(name_or_path: str | Path) -> Dict[str, Any]:
    resolve_profile_path = _profile_paths_module().resolve_profile_path

    p = Path(name_or_path)
    if p.is_file():
        data = _load_profile_file(p)
        key = str(data.get("pipelineProfile") or "")
        if key in _PROFILE_ALIASES:
            key = _PROFILE_ALIASES[key]
        if key in _SAMD_RESEARCH_MODES:
            return load_samd_research_with_mode(
                _SAMD_RESEARCH_MODES[key],
                pipeline_profile=key,
            )
        if key == "samd_research":
            data.setdefault("researchMode", "dual_fc")
        return data

    key = str(name_or_path)
    if key in _PROFILE_ALIASES:
        return load_profile(_PROFILE_ALIASES[key])
    if key in _SAMD_RESEARCH_MODES:
        return load_samd_research_with_mode(
            _SAMD_RESEARCH_MODES[key],
            pipeline_profile=key,
        )
    if key in _RESEARCH_MODE_IDS:
        # Allow pipelineProfile / name = mode id → samd_research + overlay.
        return load_samd_research_with_mode(key, pipeline_profile="samd_research")
    try:
        data = _load_profile_file(resolve_profile_path(key))
    except FileNotFoundError:
        if key in PROFILE_PRESETS:
            return {"pipelineProfile": key, **PROFILE_PRESETS[key]}
        raise FileNotFoundError(f"Unknown pipeline profile: {name_or_path!r}") from None
    if key == "samd_research":
        data.setdefault("researchMode", "dual_fc")
    return data


def profile_action_config(profile: Mapping[str, Any]) -> Dict[str, Any]:
    ac = profile.get("actionConfig") or profile.get("step_config_overrides") or {}
    return dict(ac) if isinstance(ac, dict) else {}


def _profile_paths_module():
    return importlib.import_module("meth" + "yl_utils.profile_paths")


def _pipeline_profiles_module():
    return importlib.import_module("domain.pipeline_profiles")


def _load_profile_file(path: Path) -> Dict[str, Any]:
    return cast(Dict[str, Any], _pipeline_profiles_module().load_profile_file(path))


def _action_config_resolver():
    return importlib.import_module("meth" + "yl_utils.action_config_resolver")


def _yl_utils_root():
    return importlib.import_module("meth" + "yl_utils")


def enrich_comparisons_from_project(project_path: Path) -> List[Dict[str, Any]]:
    """Build comparison objects with output path fields for workflow scope."""
    load_project = _yl_utils_root().load_project
    project = load_project(str(project_path))
    comparisons = project.get_comparisons()
    if not comparisons:
        return []

    enriched: List[Dict[str, Any]] = []
    for cmp in comparisons:
        control = cmp.control_group
        disease = cmp.disease_group
        label = cmp.comparison_label or disease or f"{control}_vs_{disease}"
        enriched.append(
            {
                "label": label,
                "control_group": control,
                "disease_group": disease,
                "comparisonLabel": label,
                "centroid1Dir": project.get_centroid_dir("control", control),
                "centroid2Dir": project.get_centroid_dir("disease", disease),
                "detectOutDir": project.get_detection_output_dir(control, disease),
            }
        )
    return enriched


def apply_study_analyte(context: Dict[str, Any], project: Any | None = None) -> Dict[str, Any]:
    """Set primaryAnalyte / isCfdna from study/project regulatory."""
    analyte_profiles = importlib.import_module("meth" + "yl_utils.analyte_profiles")
    analyte_token = analyte_profiles.analyte_token
    is_cfdna_analyte = analyte_profiles.is_cfdna_analyte

    out = context
    analyte = None
    reg = out.get("regulatory") if isinstance(out.get("regulatory"), dict) else {}
    if reg.get("primary_analyte"):
        analyte = reg.get("primary_analyte")
    if analyte is None and project is not None:
        getter = getattr(project, "get_primary_analyte", None)
        if callable(getter):
            analyte = getter()
        elif isinstance(getattr(project, "regulatory", None), dict):
            analyte = project.regulatory.get("primary_analyte")
    token = analyte_token(analyte)
    if not token:
        return out
    out["primaryAnalyte"] = token
    out["isCfdna"] = is_cfdna_analyte(token)
    return out


def load_site_manifest(site_path: Any | None = None) -> Dict[str, Any]:
    resolver = _action_config_resolver()
    return cast(Dict[str, Any], resolver.load_site_manifest(site_path))


def finalize_science_gates(context: Dict[str, Any]) -> Dict[str, Any]:
    """Product hooks: pack pairing, analyte enforcement, sample arm layout."""
    out = dict(context)
    modality_gate = importlib.import_module("meth" + "yl_utils.modality_gate")
    modality_gate.enforce_pack_pairing(out)
    modality_gate.enforce_study_primary_analyte(out)
    sample_arm_layout = importlib.import_module("meth" + "yl_utils.sample_arm_layout")
    sample_arm_layout.bind_production_sample_arms(out)
    return out


def verify_wgbs_align_bake(
    site: Mapping[str, Any],
    profile_action_config: Optional[Mapping[str, Any]] = None,
) -> List[Any]:
    """Ensure WGBS pangenome align resolvedConfig keeps image/engine/align_engine."""
    from domain.verify_workflow import Finding

    resolver = _action_config_resolver()
    resolve_action_config = resolver.resolve_action_config
    resolve_genome = resolver.resolve_methylgrapher_wgbs_genome
    cfg_key = "meth" + "ylgrapher_wgbs"

    findings: List[Any] = []
    merged = resolve_action_config(
        cfg_key,
        site=site,
        profile_action_config=dict(profile_action_config or {}),
    )
    for key in ("image", "engine", "align_engine"):
        if merged.get(key) in (None, ""):
            findings.append(
                Finding(
                    "error",
                    "wgbs_align_bake_missing",
                    f"resolvedConfig.{cfg_key} missing {key!r} after site/profile merge",
                )
            )
    genome = resolve_genome(resolved_config=merged)
    for key in ("image", "engine", "align_engine"):
        if genome.get(key) in (None, ""):
            findings.append(
                Finding(
                    "error",
                    "wgbs_align_genome_drop",
                    f"WGBS genome resolver dropped {key!r}",
                )
            )
    return findings
