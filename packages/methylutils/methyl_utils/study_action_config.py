"""SamplePrep guardrails: site full window + sparse overlays for other layers.

Operators edit the **effective** next-run document. Persist rules:

* **site** — full ``alignment_qc`` / ``extraction_qc`` slice (published WGBS window).
* **profile / procedure / study** — sparse diff against inherited layers
  (omit a key = inherit; JSON ``null`` = clear that inherited leaf).

Do **not** bind ``AlignmentQCConfig`` / ``ExtractionQCConfig`` as the editor
schema: those require workflow identity (``sample_paths``, ``output_dir``).

Committed JSON Schemas:

* ``schemas/config/sample_prep_guardrails.schema.json`` (site)
* ``schemas/config/study_action_config_overlay.schema.json`` (overlay; study GET alias)
* ``schemas/config/sample_prep_guardrails_overlay.schema.json`` (same overlay model)
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Literal, Mapping, Optional, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from .action_config_resolver import deep_merge

SCHEMA_ID = "study_action_config_overlay"
SCHEMA_FILENAME = "study_action_config_overlay.schema.json"
FULL_SCHEMA_ID = "sample_prep_guardrails"
FULL_SCHEMA_FILENAME = "sample_prep_guardrails.schema.json"
OVERLAY_SCHEMA_ID = "sample_prep_guardrails_overlay"
OVERLAY_SCHEMA_FILENAME = "sample_prep_guardrails_overlay.schema.json"
GUARDRAIL_ACTION_KEYS = ("alignment_qc", "extraction_qc")
GuardrailLayer = Literal["site", "profile", "procedure", "study"]

CORE_WINDOW_KEYS = (
    "min_pf_percent",
    "min_q30_percent",
    "min_mean_quality",
    "min_quality_post20",
    "max_at_dropout",
    "max_gc_dropout",
    "median_insert_min_bp",
    "median_insert_max_bp",
    "max_deamination_qscore",
    "min_oxog_qscore",
)
EXTRACTION_WINDOW_KEYS = (
    "min_cpg_weighted_mean_coverage",
    "max_chh_methylation_level",
    "max_chg_methylation_level",
    "min_autosomal_coverage_uniformity_ratio",
    "max_discard_fraction",
)
DEFAULT_EXPECTED_CHROMOSOMES: List[str] = [str(i) for i in range(1, 23)] + ["X", "Y"]

_INHERIT = (
    "Omit to inherit site/profile/procedure. JSON null clears the inherited leaf. "
    "Operator-set on the overlay Guardrails editor (profile, procedure, or study)."
)
_SITE = (
    "Published WGBS window. Operator-set on Platform → Site → Guardrails; "
    "this is the canonical deployment value, not a Python fallback."
)


class CycleScreeningOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(default=None, description=_INHERIT)
    read_length: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    r2_start_window_cycles: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    recovery_cycles: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    r2_quality_threshold: Optional[float] = Field(default=None, description=_INHERIT)
    max_trim_bases: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    multi_region_min_separate_dips: Optional[int] = Field(default=None, ge=2, description=_INHERIT)
    read_edge_window: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    broad_bad_cycle_count: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    localized_max_span: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    remediate_without_cycles: Optional[bool] = Field(default=None, description=_INHERIT)
    fallback_trim_front: Optional[int] = Field(default=None, ge=0, description=_INHERIT)
    fallback_trim_tail: Optional[int] = Field(default=None, ge=0, description=_INHERIT)


class CoreGuardrailsOverlay(BaseModel):
    """Sparse overlay of WGBS core thresholds. No published-window defaults."""

    model_config = ConfigDict(extra="forbid")

    min_pf_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0, description=_INHERIT)
    min_q30_percent: Optional[float] = Field(default=None, ge=0.0, le=100.0, description=_INHERIT)
    min_mean_quality: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    min_quality_post20: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    max_at_dropout: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    max_gc_dropout: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    median_insert_min_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    median_insert_max_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    max_deamination_qscore: Optional[int] = Field(default=None, ge=0, description=_INHERIT)
    min_oxog_qscore: Optional[int] = Field(default=None, ge=0, description=_INHERIT)


class OptionalGuardrailsOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplication_rate_max: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_INHERIT)
    min_pf_reads: Optional[int] = Field(default=None, ge=1, description=_INHERIT)


class AlignmentGuardrailsOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(default=None, description=_INHERIT)
    min_mapping_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_INHERIT)
    wgbs_min_mapped_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_INHERIT)
    max_secondary_supplementary_rate: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )
    min_gc_coverage_uniformity: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    flagstat_enabled: Optional[bool] = Field(default=None, description=_INHERIT)
    min_properly_paired_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_INHERIT)
    max_supplementary_rate_flagstat: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )


class BisulfiteConversionOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(default=None, description=_INHERIT)
    source: Optional[Literal["sidecar", "deamination_proxy", "auto"]] = Field(
        default=None, description=_INHERIT
    )
    sidecar_filename: Optional[str] = Field(default=None, description=_INHERIT)
    min_conversion_rate_pct: Optional[float] = Field(
        default=None, ge=0.0, le=100.0, description=_INHERIT
    )
    max_non_cpg_methylation_pct: Optional[float] = Field(
        default=None, ge=0.0, le=100.0, description=_INHERIT
    )
    max_deamination_qscore_proxy: Optional[int] = Field(default=None, ge=0, description=_INHERIT)


class FragmentomicsOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(default=None, description=_INHERIT)
    profile: Optional[Literal["off", "cfdna", "wgbs"]] = Field(default=None, description=_INHERIT)
    short_fragment_max_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    long_fragment_min_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    nucleosome_peak_bp_min: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    nucleosome_peak_bp_max: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    median_insert_min_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    median_insert_max_bp: Optional[int] = Field(default=None, ge=1, description=_INHERIT)
    max_short_fragment_fraction: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )


class AlignmentQcOverlay(BaseModel):
    """Operator-tunable ``alignment_qc`` slice. No sample paths or output dir."""

    model_config = ConfigDict(extra="forbid")

    auto_profile_from_analyte: Optional[bool] = Field(default=None, description=_INHERIT)
    fragmentomics: Optional[FragmentomicsOverlay] = Field(default=None, description=_INHERIT)
    bisulfite_conversion: Optional[BisulfiteConversionOverlay] = Field(
        default=None, description=_INHERIT
    )
    cycle_screening: Optional[CycleScreeningOverlay] = Field(default=None, description=_INHERIT)
    core_guardrails: Optional[CoreGuardrailsOverlay] = Field(default=None, description=_INHERIT)
    optional_guardrails: Optional[OptionalGuardrailsOverlay] = Field(
        default=None, description=_INHERIT
    )
    alignment_guardrails: Optional[AlignmentGuardrailsOverlay] = Field(
        default=None, description=_INHERIT
    )


class ExtractionQcGuardrailOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_cpg_weighted_mean_coverage: Optional[float] = Field(default=None, ge=0.0, description=_INHERIT)
    max_chh_methylation_level: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )
    max_chg_methylation_level: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )
    min_autosomal_coverage_uniformity_ratio: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description=_INHERIT
    )
    max_discard_fraction: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_INHERIT)


class ExtractionQcOverlay(BaseModel):
    """Operator-tunable ``extraction_qc`` slice. No sample paths."""

    model_config = ConfigDict(extra="forbid")

    guardrails: Optional[ExtractionQcGuardrailOverlay] = Field(default=None, description=_INHERIT)
    expected_chromosomes: Optional[List[str]] = Field(default=None, description=_INHERIT)


class StudyActionConfigOverlay(BaseModel):
    """JSON Schema bind target for overlay Guardrails editors (profile / procedure / study).

    The property grid binds this schema to the **effective** document (inherited
    values filled in). Persist only ``sparse_overlay_diff(inherited, edited)``.
    """

    model_config = ConfigDict(extra="forbid")

    alignment_qc: Optional[AlignmentQcOverlay] = Field(
        default=None,
        description="Alignment QC guardrails overlay. Omit the object to inherit the whole slice.",
    )
    extraction_qc: Optional[ExtractionQcOverlay] = Field(
        default=None,
        description="Extraction QC guardrails overlay. Omit the object to inherit the whole slice.",
    )


class CoreGuardrailsFull(BaseModel):
    """Published WGBS core thresholds. Site stores every key."""

    model_config = ConfigDict(extra="forbid")

    min_pf_percent: float = Field(default=90.0, ge=0.0, le=100.0, description=_SITE)
    min_q30_percent: float = Field(default=85.0, ge=0.0, le=100.0, description=_SITE)
    min_mean_quality: float = Field(default=35.0, ge=0.0, description=_SITE)
    min_quality_post20: float = Field(default=30.0, ge=0.0, description=_SITE)
    max_at_dropout: float = Field(default=3.0, ge=0.0, description=_SITE)
    max_gc_dropout: float = Field(default=5.0, ge=0.0, description=_SITE)
    median_insert_min_bp: int = Field(default=150, ge=1, description=_SITE)
    median_insert_max_bp: int = Field(default=300, ge=1, description=_SITE)
    max_deamination_qscore: int = Field(default=30, ge=0, description=_SITE)
    min_oxog_qscore: int = Field(default=20, ge=0, description=_SITE)


class OptionalGuardrailsFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplication_rate_max: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_SITE)
    min_pf_reads: Optional[int] = Field(default=None, ge=1, description=_SITE)


class AlignmentGuardrailsFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description=_SITE)
    min_mapping_rate: Optional[float] = Field(default=0.98, ge=0.0, le=1.0, description=_SITE)
    wgbs_min_mapped_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description=_SITE)
    max_secondary_supplementary_rate: Optional[float] = Field(
        default=0.05, ge=0.0, le=1.0, description=_SITE
    )
    min_gc_coverage_uniformity: Optional[float] = Field(default=None, ge=0.0, description=_SITE)
    flagstat_enabled: bool = Field(default=True, description=_SITE)
    min_properly_paired_rate: Optional[float] = Field(default=0.90, ge=0.0, le=1.0, description=_SITE)
    max_supplementary_rate_flagstat: Optional[float] = Field(
        default=0.02, ge=0.0, le=1.0, description=_SITE
    )


class BisulfiteConversionFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description=_SITE)
    source: Literal["sidecar", "deamination_proxy", "auto"] = Field(default="auto", description=_SITE)
    sidecar_filename: str = Field(default="bisulfite_conversion.json", description=_SITE)
    min_conversion_rate_pct: float = Field(default=99.0, ge=0.0, le=100.0, description=_SITE)
    max_non_cpg_methylation_pct: float = Field(default=2.0, ge=0.0, le=100.0, description=_SITE)
    max_deamination_qscore_proxy: int = Field(default=30, ge=0, description=_SITE)


class FragmentomicsFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description=_SITE)
    profile: Literal["off", "cfdna", "wgbs"] = Field(default="off", description=_SITE)
    short_fragment_max_bp: int = Field(default=150, ge=1, description=_SITE)
    long_fragment_min_bp: int = Field(default=300, ge=1, description=_SITE)
    nucleosome_peak_bp_min: int = Field(default=140, ge=1, description=_SITE)
    nucleosome_peak_bp_max: int = Field(default=200, ge=1, description=_SITE)
    median_insert_min_bp: int = Field(default=120, ge=1, description=_SITE)
    median_insert_max_bp: int = Field(default=220, ge=1, description=_SITE)
    max_short_fragment_fraction: float = Field(default=0.35, ge=0.0, le=1.0, description=_SITE)


class CycleScreeningFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description=_SITE)
    read_length: Optional[int] = Field(default=None, ge=1, description=_SITE)
    r2_start_window_cycles: int = Field(default=5, ge=1, description=_SITE)
    recovery_cycles: int = Field(default=10, ge=1, description=_SITE)
    r2_quality_threshold: float = Field(default=30.0, description=_SITE)
    max_trim_bases: int = Field(default=8, ge=1, description=_SITE)
    multi_region_min_separate_dips: int = Field(default=2, ge=2, description=_SITE)
    read_edge_window: int = Field(default=10, ge=1, description=_SITE)
    broad_bad_cycle_count: int = Field(default=8, ge=1, description=_SITE)
    localized_max_span: int = Field(default=6, ge=1, description=_SITE)
    remediate_without_cycles: Optional[bool] = Field(default=None, description=_SITE)
    fallback_trim_front: Optional[int] = Field(default=None, ge=0, description=_SITE)
    fallback_trim_tail: Optional[int] = Field(default=None, ge=0, description=_SITE)


class AlignmentQcFull(BaseModel):
    """Site ``alignment_qc`` slice. No sample paths or output dir."""

    model_config = ConfigDict(extra="forbid")

    auto_profile_from_analyte: Optional[bool] = Field(default=None, description=_SITE)
    fragmentomics: Optional[FragmentomicsFull] = Field(default=None, description=_SITE)
    bisulfite_conversion: Optional[BisulfiteConversionFull] = Field(default=None, description=_SITE)
    cycle_screening: Optional[CycleScreeningFull] = Field(default=None, description=_SITE)
    core_guardrails: CoreGuardrailsFull = Field(default_factory=CoreGuardrailsFull, description=_SITE)
    optional_guardrails: Optional[OptionalGuardrailsFull] = Field(default=None, description=_SITE)
    alignment_guardrails: Optional[AlignmentGuardrailsFull] = Field(default=None, description=_SITE)


class ExtractionQcGuardrailFull(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_cpg_weighted_mean_coverage: float = Field(default=10.0, ge=0.0, description=_SITE)
    max_chh_methylation_level: float = Field(default=0.02, ge=0.0, le=1.0, description=_SITE)
    max_chg_methylation_level: float = Field(default=0.02, ge=0.0, le=1.0, description=_SITE)
    min_autosomal_coverage_uniformity_ratio: float = Field(
        default=0.5, ge=0.0, le=1.0, description=_SITE
    )
    max_discard_fraction: float = Field(default=0.9, ge=0.0, le=1.0, description=_SITE)


class ExtractionQcFull(BaseModel):
    """Site ``extraction_qc`` slice. No sample paths."""

    model_config = ConfigDict(extra="forbid")

    guardrails: ExtractionQcGuardrailFull = Field(
        default_factory=ExtractionQcGuardrailFull, description=_SITE
    )
    expected_chromosomes: List[str] = Field(
        default_factory=lambda: list(DEFAULT_EXPECTED_CHROMOSOMES), description=_SITE
    )


class SamplePrepGuardrails(BaseModel):
    """JSON Schema bind target for Platform → Site → Guardrails.

    Persist the full working document (published window). Nested optional groups
    (fragmentomics, alignment_guardrails) may be omitted so analyte fill-missing
    still applies at instance bake.
    """

    model_config = ConfigDict(extra="forbid")

    alignment_qc: AlignmentQcFull = Field(default_factory=AlignmentQcFull, description=_SITE)
    extraction_qc: ExtractionQcFull = Field(default_factory=ExtractionQcFull, description=_SITE)


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin is Union:
        candidates = [a for a in get_args(annotation) if a is not type(None)]
        if len(candidates) == 1:
            annotation = candidates[0]
        else:
            return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def take_known_fields(model: type[BaseModel], data: Any) -> Any:
    """Keep only fields declared on ``model`` (recursive). Preserves JSON null."""
    if data is None or not isinstance(data, Mapping):
        return data
    out: Dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name not in data:
            continue
        value = data[name]
        nested = _nested_model(field.annotation)
        if nested is not None and isinstance(value, Mapping):
            out[name] = take_known_fields(nested, value)
        else:
            out[name] = copy.deepcopy(value)
    return out


def slice_guardrails(action_config: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Project an actionConfig map onto the Guardrails editor keys."""
    raw = dict(action_config or {})
    sliced = {key: raw[key] for key in GUARDRAIL_ACTION_KEYS if key in raw}
    known = take_known_fields(StudyActionConfigOverlay, sliced)
    return known if isinstance(known, dict) else {}


_MISSING = object()


def sparse_overlay_diff(
    baseline: Mapping[str, Any] | None,
    edited: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Return the overlay such that ``deep_merge(baseline, overlay)`` equals ``edited``.

    Equal values are omitted. A key present on ``baseline`` but missing from
    ``edited`` becomes JSON ``null`` (delete leaf). Nested objects recurse.
    Lists and scalars replace wholesale (same as ``deep_merge``).
    """
    base = dict(baseline or {})
    edit = dict(edited or {})
    out: Dict[str, Any] = {}
    for key, edited_val in edit.items():
        if edited_val is None:
            if key in base:
                out[key] = None
            continue
        base_val = base.get(key, _MISSING)
        if isinstance(edited_val, dict) and isinstance(base_val, dict):
            child = sparse_overlay_diff(base_val, edited_val)
            if child:
                out[key] = child
        elif base_val != edited_val:
            out[key] = copy.deepcopy(edited_val)
    for key in base:
        if key not in edit:
            out[key] = None
    return out


def published_sample_prep_guardrails() -> Dict[str, Any]:
    """Canonical site QC slice: core WGBS window + extraction caps (omit optional groups)."""
    return SamplePrepGuardrails().model_dump(exclude_none=True)


def missing_full_window_keys(doc: Mapping[str, Any] | None) -> List[str]:
    """Return dotted paths missing from a site full-guardrails document."""
    raw = dict(doc or {})
    missing: List[str] = []
    aq = raw.get("alignment_qc")
    if not isinstance(aq, dict):
        missing.append("alignment_qc.core_guardrails")
    else:
        core = aq.get("core_guardrails")
        if not isinstance(core, dict):
            missing.append("alignment_qc.core_guardrails")
        else:
            for key in CORE_WINDOW_KEYS:
                if key not in core or core[key] is None:
                    missing.append(f"alignment_qc.core_guardrails.{key}")
    eq = raw.get("extraction_qc")
    if not isinstance(eq, dict):
        missing.append("extraction_qc.guardrails")
    else:
        guards = eq.get("guardrails")
        if not isinstance(guards, dict):
            missing.append("extraction_qc.guardrails")
        else:
            for key in EXTRACTION_WINDOW_KEYS:
                if key not in guards or guards[key] is None:
                    missing.append(f"extraction_qc.guardrails.{key}")
    return missing


def assert_full_core_window(doc: Mapping[str, Any] | None) -> None:
    missing = missing_full_window_keys(doc)
    if missing:
        raise ValueError(
            "site guardrails require the full published window; missing: " + ", ".join(missing)
        )


def _schema_id_for_layer(layer: GuardrailLayer) -> str:
    if layer == "site":
        return FULL_SCHEMA_ID
    if layer == "study":
        return SCHEMA_ID
    return OVERLAY_SCHEMA_ID


def compose_guardrails(
    *,
    site_action_config: Mapping[str, Any] | None = None,
    profile_action_config: Mapping[str, Any] | None = None,
    procedure_action_config: Mapping[str, Any] | None = None,
    study_overlay: Mapping[str, Any] | None = None,
    through_layer: GuardrailLayer = "study",
) -> Dict[str, Any]:
    """Build a Guardrails editor payload up to ``through_layer`` (later layer wins).

    Merge order matches instance bake: site → profile → procedure → study overlay.
    """
    site = slice_guardrails(site_action_config)
    profile = slice_guardrails(profile_action_config)
    procedure = slice_guardrails(procedure_action_config)
    study = slice_guardrails(study_overlay)
    layers: Dict[GuardrailLayer, Dict[str, Any]] = {
        "site": site,
        "profile": profile,
        "procedure": procedure,
        "study": study,
    }
    order: List[GuardrailLayer] = ["site", "profile", "procedure", "study"]
    inherited: Dict[str, Any] = {}
    overlay: Dict[str, Any] = {}
    for layer in order:
        if layer == through_layer:
            overlay = layers[layer]
            break
        inherited = deep_merge(inherited, layers[layer])
    else:
        overlay = study
    effective = deep_merge(inherited, overlay) if through_layer != "site" else dict(site)
    if through_layer == "site":
        inherited = {}
        overlay = site
        effective = dict(site)
    return {
        "schema_id": _schema_id_for_layer(through_layer),
        "inherited": inherited,
        "overlay": overlay,
        "effective": effective,
        "through_layer": through_layer,
    }


def compose_study_guardrails(
    *,
    site_action_config: Mapping[str, Any] | None = None,
    profile_action_config: Mapping[str, Any] | None = None,
    procedure_action_config: Mapping[str, Any] | None = None,
    study_overlay: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Study Guardrails editor payload (schema_id stays ``study_action_config_overlay``)."""
    view = compose_guardrails(
        site_action_config=site_action_config,
        profile_action_config=profile_action_config,
        procedure_action_config=procedure_action_config,
        study_overlay=study_overlay,
        through_layer="study",
    )
    view.pop("through_layer", None)
    return view


def _merge_qc_slice_into_action_config(
    existing_action_config: Mapping[str, Any] | None,
    qc_slice: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    merged = dict(existing_action_config or {})
    for key in GUARDRAIL_ACTION_KEYS:
        merged.pop(key, None)
    for key, value in dict(qc_slice or {}).items():
        if value is None:
            continue
        merged[key] = copy.deepcopy(value)
    return merged


def overlay_from_edited_effective(
    *,
    inherited: Mapping[str, Any],
    edited_effective: Mapping[str, Any],
    existing_action_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Validate the edited working document and return the full layer actionConfig.

    Guardrail keys are replaced by the computed sparse diff. Every other
    ``actionConfig`` key (HPO ``validation``, mapper, …) is preserved.
    """
    edited = slice_guardrails(edited_effective)
    StudyActionConfigOverlay.model_validate(edited)
    guardrail_overlay = sparse_overlay_diff(inherited, edited)
    StudyActionConfigOverlay.model_validate(guardrail_overlay)
    return _merge_qc_slice_into_action_config(existing_action_config, guardrail_overlay)


def site_action_config_from_edited_full(
    *,
    edited_effective: Mapping[str, Any],
    existing_action_config: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Validate a full site working document and replace only the QC slice."""
    edited = slice_guardrails(edited_effective)
    assert_full_core_window(edited)
    SamplePrepGuardrails.model_validate(edited)
    return _merge_qc_slice_into_action_config(existing_action_config, edited)
