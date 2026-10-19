"""
WorkflowContext contract: how instance parameters become per-action input_json.

Parameter derivation lives in planners + SQL engine (templates, scope, bindings).
Workers receive fully resolved payloads from the DB read-path when instances are
configured with ``finalize_instance_context`` (``resolvedConfig__*`` scope vars).
This module supports planners, admin CLI, tests, and ``LocalWorkflowEngine``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

_PLACEHOLDER_RE = re.compile(r"^\$\{var\.([^}]+)\}$")


class InstanceContext(BaseModel):
    """Minimal or enriched payload for workflow_instance.context_json."""

    model_config = ConfigDict(extra="allow")

    projectPath: str
    project: Optional[Dict[str, Any]] = None
    iterations: Optional[List[Dict[str, Any]]] = None
    samples: Optional[List[Dict[str, Any]]] = None
    comparisons: Optional[List[Dict[str, Any]]] = None
    chromosomes: Optional[List[str]] = None
    contexts: Optional[List[str]] = None
    centroid1Dir: Optional[str] = None


class ResolvedScope(BaseModel):
    """Read-only scope snapshot for tests and planners."""

    vars: Dict[str, Any] = Field(default_factory=dict)


class ActionInputSpec(BaseModel):
    """Expected resolved keys for one ACTION (from catalog + task schema)."""

    action_name: str
    required_keys: List[str]
    optional_keys: List[str] = Field(default_factory=list)


def _resolve_project_path(context: Mapping[str, Any]) -> Path:
    raw = context.get("projectPath") or context.get("project_path")
    if not raw:
        raise ValueError("context_json missing projectPath")
    path = Path(str(raw)).expanduser().resolve()
    if path.is_dir():
        candidate = path / "project.json"
        if candidate.is_file():
            return candidate
    if path.is_file():
        return path
    raise FileNotFoundError(f"Could not resolve project.json from projectPath={raw!r}")


def enrich_comparisons_from_project(project_path: Path) -> List[Dict[str, Any]]:
    """Build comparison objects with output path fields for workflow scope."""
    from methyl_utils import load_project

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
    """Set ``primaryAnalyte`` / ``isCfdna`` from study/project regulatory.

    Study ``regulatory.primary_analyte`` wins over DomainProgram fixture seeds
    and stale portal ``cfdna`` defaults. Token shape matches SQL create-instance
    overlays: lowercase, hyphen → underscore (aliases are not collapsed).
    """
    from methyl_utils.analyte_profiles import analyte_token, is_cfdna_analyte

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


def enrich_instance_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expand minimal ``{ projectPath }`` into engine-ready context_json.

    Adds comparisons (with path fields), chromosomes, contexts, and shared
    ``centroid1Dir`` when absent. Safe to call on already-enriched payloads.
    """
    out = dict(context)
    project_path = _resolve_project_path(out)

    from methyl_utils import load_project
    from methyl_utils.action_config_resolver import load_site_manifest
    from pipeline_profiles import (
        PIPELINE_FLAG_DEFAULTS,
        apply_pipeline_procedure,
        apply_pipeline_profile,
        load_procedure,
        load_profile,
        profile_action_config,
        seed_pipeline_scope_flags,
        validate_procedure_analyte,
    )

    project = load_project(str(project_path))

    if not out.get("comparisons"):
        out["comparisons"] = enrich_comparisons_from_project(project_path)

    if not out.get("chromosomes"):
        out["chromosomes"] = list(project.chromosomes or [])

    if not out.get("contexts"):
        out["contexts"] = list(getattr(project, "contexts", None) or ["CG"])

    if not out.get("centroid1Dir") and out.get("comparisons"):
        first = out["comparisons"][0]
        control = first.get("control_group") or first.get("group1Label")
        if control:
            out["centroid1Dir"] = project.get_centroid_dir("control", str(control))

    if not out.get("groups"):
        resolved = project.get_resolved_groups()
        out["groups"] = [{"label": label} for label, _ in resolved]

    if not out.get("siteConfig"):
        site_path = out.get("siteConfigPath")
        out["siteConfig"] = load_site_manifest(site_path) if site_path else load_site_manifest()

    if not out.get("regulatory"):
        out["regulatory"] = project.get_regulatory_config()

    # Study/project analyte is source of truth. DomainProgram fixture defaults
    # (historically primaryAnalyte=cfdna) and portal leftovers must not win.
    apply_study_analyte(out, project)

    # Assay procedure pack (optional). Precedence (highest wins first):
    # instance → procedure → profile/mode → analyte → site.
    # Rebuild actionConfig in that order so procedure JSON nulls can clear
    # profile keys (e.g. cell_deconvolution on cfDNA plasma).
    from pipeline_profiles import _deep_merge

    instance_action_config = dict(out.get("actionConfig") or {})
    out["actionConfig"] = {}

    procedure_name = out.get("pipelineProcedure")
    procedure_file = out.get("procedurePath")
    if procedure_file or procedure_name:
        procedure = load_procedure(procedure_file or procedure_name)
        out = apply_pipeline_procedure(out, procedure)

    profile_name = out.get("pipelineProfile")
    profile_file = out.get("profilePath")
    if profile_file or profile_name:
        # Prefer procedure/instance researchMode when loading samd_research modes.
        profile = load_profile(profile_file or profile_name)
        out = apply_pipeline_profile(out, profile)
    elif out.get("actionConfig"):
        out = seed_pipeline_scope_flags(out, action_config=out.get("actionConfig"))
    elif any(k in out for k in PIPELINE_FLAG_DEFAULTS):
        out = seed_pipeline_scope_flags(out, action_config=profile_action_config(out))

    if instance_action_config:
        out["actionConfig"] = _deep_merge(
            dict(out.get("actionConfig") or {}),
            instance_action_config,
        )
        out = seed_pipeline_scope_flags(out, action_config=out.get("actionConfig"))

    validate_procedure_analyte(out)
    return out


def _lookup_scope_path(scope: Mapping[str, Any], path: str) -> Any:
    """Resolve dotted scope paths such as ``iteration.runDir``."""
    cur: Any = scope
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def _resolve_template_value(value: Any, scope: Mapping[str, Any], *, missing: str = "error") -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {"ref"} and isinstance(value.get("ref"), str):
            try:
                return _lookup_scope_path(scope, str(value["ref"]))
            except KeyError:
                if missing == "none":
                    return None
                raise
        return {k: _resolve_template_value(v, scope, missing=missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_template_value(v, scope, missing=missing) for v in value]
    return resolve_placeholder(value, scope, missing=missing)


def resolve_placeholder(
    value: Any, scope: Mapping[str, Any], *, missing: str = "error"
) -> Any:
    """Resolve ``${var.name}`` placeholders (Python mirror of SQL read-path)."""
    if isinstance(value, dict):
        return {k: resolve_placeholder(v, scope, missing=missing) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholder(v, scope, missing=missing) for v in value]
    if not isinstance(value, str):
        return value
    m = _PLACEHOLDER_RE.match(value.strip())
    if not m:
        return value
    key = m.group(1)
    if key in scope:
        return scope[key]
    try:
        return _lookup_scope_path(scope, key)
    except KeyError:
        if missing == "none":
            return None
        raise KeyError(f"unresolved scope variable: {key}") from None


def resolve_input_json_from_template(
    template: Mapping[str, Any], scope: Mapping[str, Any]
) -> Dict[str, Any]:
    """Resolve an action input_template against a flat scope dict (for tests)."""
    resolved = _resolve_template_value(dict(template), scope, missing="none")
    if not isinstance(resolved, dict):
        return {}
    return {k: v for k, v in resolved.items() if v is not None}


def list_unresolved_placeholders(value: Any) -> List[str]:
    """Return placeholder var names still present in a nested structure."""
    found: Set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            m = _PLACEHOLDER_RE.match(obj.strip())
            if m:
                found.add(m.group(1))

    walk(value)
    return sorted(found)


def action_input_spec_for(action_name: str) -> Optional[ActionInputSpec]:
    """Load required keys from the action catalog entry."""
    import sys
    from pathlib import Path as _Path

    workers = _Path(__file__).resolve().parents[2] / "workers"
    if str(workers) not in sys.path:
        sys.path.insert(0, str(workers))

    from methyl_worker.action_catalog import find_catalog_entry

    entry = find_catalog_entry(action_name)
    if entry is None:
        return None

    optional = {
        "outputDir",
        "centroid1Dir",
        "centroid2Dir",
        "centroidSeedDir",
        "residualizeCoefDir",
        "taskConfig",
        "phase",
        "runId",
        "addSamples",
        "removeSamples",
        "stepOverride",
        "comparison",
        "fixedDmpPanel",
        # Bound when present; methyl_qc fails closed at runtime if artifacts are ambiguous.
        "alignmentMode",
        "primaryAnalyte",
        # Full-archive SamplePrep has no reject reason; QC-fail nodes set it in `with`.
        "rejectReason",
        "sampleRoot",
    }
    required = ["tool"]
    if entry.action_config_key:
        required.extend(["project", "projectPath"])
    required.extend(c for c in entry.context_vars if c not in optional)
    optional_keys = list(optional)
    return ActionInputSpec(
        action_name=action_name,
        required_keys=sorted(set(required)),
        optional_keys=optional,
    )


def resolved_config_scope_var_name(action_config_key: str) -> str:
    """Scope variable name for a pre-resolved actionConfig slice at instance start."""
    return f"resolvedConfig__{action_config_key}"


def build_resolved_config_scope_vars(context: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Resolve each actionConfig key once at instance configuration time.

    Flattened scope vars (``resolvedConfig__<key>``) are stored in context_json so
    the SQL engine can bind ``${var.resolvedConfig__*}`` in action input templates
    without gateway-side materialization at task claim.
    """
    import sys
    from pathlib import Path as _Path

    workers = _Path(__file__).resolve().parents[2] / "workers"
    if str(workers) not in sys.path:
        sys.path.insert(0, str(workers))

    from methyl_worker.action_catalog import ACTION_CATALOG
    from methyl_utils.action_config_resolver import resolve_action_config

    ac = context.get("actionConfig")
    profile_ac = dict(ac) if isinstance(ac, dict) else {}
    site = context.get("siteConfig") if isinstance(context.get("siteConfig"), dict) else {}
    reg = context.get("regulatory") if isinstance(context.get("regulatory"), dict) else {}

    keys: Set[str] = set(profile_ac.keys())
    for entry in ACTION_CATALOG:
        if entry.action_config_key:
            keys.add(entry.action_config_key)

    out: Dict[str, Any] = {}
    for key in sorted(keys):
        resolved = resolve_action_config(
            key,
            site=site,
            profile_action_config=profile_ac,
            program_override=None,
            regulatory=reg,
        )
        # Study regulatory owns analyte / claim shell in the validation slice so
        # profile nests cannot disagree with context.regulatory in the UI bake.
        if key == "validation" and reg:
            nested = dict(resolved.get("regulatory") or {})
            for rk, rv in reg.items():
                if rv is None:
                    nested.pop(rk, None)
                else:
                    nested[rk] = rv
            resolved["regulatory"] = nested
        out[resolved_config_scope_var_name(key)] = resolved
    return out


def compute_execution_scope_id(context: Mapping[str, Any]) -> str:
    """
    Stable identifier for the merged execution scope baked at instance start.

    Hashes all ``resolvedConfig__*`` scope slices plus an optional operator label
    (``executionScopeName`` / ``executionScopeLabel``; the legacy
    ``hyperparamSetName`` / ``hyperparamSetLabel`` are accepted as aliases).
    """
    slices: Dict[str, Any] = {}
    for key, value in sorted(context.items()):
        if key.startswith("resolvedConfig__"):
            slices[key] = value
    payload: Dict[str, Any] = {"slices": slices}
    label = (
        context.get("executionScopeName")
        or context.get("executionScopeLabel")
        or context.get("hyperparamSetName")
        or context.get("hyperparamSetLabel")
    )
    if label:
        payload["name"] = label
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def finalize_instance_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich instance context and bake resolvedConfig scope vars for DB-backed runs.

    Call before ``create_workflow_instance`` when the workflow engine will resolve
    action input templates from scope (distributed path).

    Always attempts cfg → /work study sync first so membership CSVs and project
    JSON match the published registry before the instance is created.
    """
    import sys
    from pathlib import Path as _Path

    _we = _Path(__file__).resolve().parents[1]
    if str(_we) not in sys.path:
        sys.path.insert(0, str(_we))
    from cfg.sync_on_start import ensure_study_work_synced

    out = ensure_study_work_synced(context)
    out = enrich_instance_context(out)
    from methyl_utils.modality_gate import enforce_pack_pairing, enforce_study_primary_analyte

    enforce_pack_pairing(out)
    enforce_study_primary_analyte(out)
    from methyl_utils.sample_arm_layout import bind_production_sample_arms

    # After enrich so site/profile engine knobs are visible. RNA/proteomics skip.
    bind_production_sample_arms(out)
    out.update(build_resolved_config_scope_vars(out))
    scope_id = compute_execution_scope_id(out)
    out["executionScopeId"] = scope_id
    # One-release alias so pre-rename compiled graphs (which bind
    # ${var.hyperparamSetId}) still resolve against this scope.
    out["hyperparamSetId"] = scope_id
    return out


def _match_resolved_comparison(
    resolved_project: Mapping[str, Any],
    *,
    comparison: Optional[str] = None,
    group: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    comparisons = resolved_project.get("comparisons")
    if not isinstance(comparisons, list) or not comparisons:
        return None
    token = comparison or group
    if token in (None, ""):
        first = comparisons[0]
        return dict(first) if isinstance(first, dict) else None
    token_str = str(token)
    for item in comparisons:
        if not isinstance(item, dict):
            continue
        if token_str in {
            str(item.get("label") or ""),
            str(item.get("comparisonLabel") or ""),
            str(item.get("diseaseGroup") or ""),
        }:
            return dict(item)
    return None


def bind_resolved_project_paths(
    input_json: Dict[str, Any],
    scope: Mapping[str, Any],
) -> Dict[str, Any]:
    """Inject concrete artifact dirs from scope ``resolvedProject`` when templates omit them."""
    rp = scope.get("resolvedProject")
    if not isinstance(rp, dict):
        return input_json
    cmp = _match_resolved_comparison(
        rp,
        comparison=input_json.get("comparison") if isinstance(input_json.get("comparison"), str) else None,
        group=input_json.get("group") if isinstance(input_json.get("group"), str) else None,
    )
    if cmp is None:
        return input_json
    out = dict(input_json)
    action_name = str(out.get("tool") or "")
    if not out.get("centroid1Dir") and cmp.get("centroid1Dir"):
        out["centroid1Dir"] = cmp["centroid1Dir"]
    if not out.get("centroid2Dir") and cmp.get("centroid2Dir"):
        out["centroid2Dir"] = cmp["centroid2Dir"]
    if not out.get("outputDir"):
        if "MethylDetector" in action_name or out.get("discoveryCsv"):
            out["outputDir"] = cmp.get("detectOutDir")
        elif "MethylMapper" in action_name:
            out["outputDir"] = cmp.get("mapperOutDir")
        elif "MethylEnricher" in action_name:
            out["outputDir"] = cmp.get("enricherOutDir")
        elif "MethylClassifier" in action_name:
            out["outputDir"] = cmp.get("classifierOutDir") or cmp.get("detectOutDir")
    return out


def materialize_action_input(
    input_json: Dict[str, Any],
    action_name: str,
    scope: Mapping[str, Any],
) -> Dict[str, Any]:
    """Inject resolvedConfig for workflow ACTION nodes when catalog defines action_config_key."""
    import sys
    from pathlib import Path as _Path

    workers = _Path(__file__).resolve().parents[2] / "workers"
    if str(workers) not in sys.path:
        sys.path.insert(0, str(workers))

    from methyl_worker.action_catalog import find_catalog_entry
    from methyl_utils.action_config_resolver import resolve_action_config

    entry = find_catalog_entry(action_name)
    if entry is None or not entry.action_config_key:
        return bind_resolved_project_paths(input_json, scope)

    out = bind_resolved_project_paths(dict(input_json), scope)
    if isinstance(out.get("resolvedConfig"), dict):
        return out

    ac = scope.get("actionConfig")
    profile_ac = dict(ac) if isinstance(ac, dict) else {}
    site = scope.get("siteConfig") if isinstance(scope.get("siteConfig"), dict) else {}
    override = out.get("stepOverride") if isinstance(out.get("stepOverride"), dict) else None
    reg = scope.get("regulatory") if isinstance(scope.get("regulatory"), dict) else {}

    out["resolvedConfig"] = resolve_action_config(
        entry.action_config_key,
        site=site,
        profile_action_config=profile_ac,
        program_override=override,
        regulatory=reg,
    )
    if entry.action_config_key == "predictor":
        classifier_cfg = resolve_action_config(
            "classifier",
            site=site,
            profile_action_config=profile_ac,
            program_override=None,
            regulatory=reg,
        )
        if isinstance(classifier_cfg, dict) and classifier_cfg:
            baked = dict(out["resolvedConfig"])
            baked["classifier"] = classifier_cfg
            if baked.get("model_path") in (None, "") and classifier_cfg.get("save_classifier_path"):
                baked["model_path"] = classifier_cfg["save_classifier_path"]
            out["resolvedConfig"] = baked
    return out


def validate_resolved_input_json(input_json: Mapping[str, Any], action_name: str) -> List[str]:
    """Return validation errors for a resolved worker payload."""
    errors: List[str] = []
    unresolved = list_unresolved_placeholders(dict(input_json))
    if unresolved:
        errors.append(f"unresolved placeholders: {', '.join(unresolved)}")

    spec = action_input_spec_for(action_name)
    if spec is None:
        return errors

    for key in spec.required_keys:
        if key == "projectPath":
            if not any(input_json.get(k) for k in ("project", "projectPath", "project_path")):
                errors.append("missing project or projectPath")
        elif input_json.get(key) in (None, ""):
            errors.append(f"missing required key: {key}")

    return errors


def instance_context_to_json(context: InstanceContext | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(context, InstanceContext):
        return context.model_dump(exclude_none=True)
    return dict(context)


def write_instance_context(path: Path, context: InstanceContext | Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = instance_context_to_json(context)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
