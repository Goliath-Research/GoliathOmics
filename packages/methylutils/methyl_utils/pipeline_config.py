"""
Shared project configuration for MethylPipeline workflows.

This module is the code-level contract for project JSONs consumed by the main
pipeline steps. The canonical project shape is control/disease/comparisons,
with optional backward compatibility for group1/group2 or flat groups.

Derived paths follow a single project root:

    {output_base}/{project_name}/
        centroids/
        detections/
        mapper/
        enricher/
        classifiers/
        predictors/
        alignment_qc/
        clustering/

Comparison-driven steps use explicit comparison folders such as
`detections/<control_group>/<disease_group>` and
`classifiers/<control_group>/<disease_group>` so downstream tools can resolve
the same artifact layout consistently.
"""

import json
import logging
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

logger = logging.getLogger(__name__)


class ControlDiseaseSide(BaseModel):
    """One side (control or disease) with a display label and one or more sub-groups."""

    label: str = Field(..., description="Display name for this side (e.g. caucasians, prostate cancer)")
    groups: List["GroupConfig"] = Field(
        default_factory=list,
        description="Sub-groups, each with label and sample_paths (and optional level_labels_path, samples_base_path)",
    )


class ComparisonSpec(BaseModel):
    """One comparison: a pair of control group vs disease group to run through detection, mapper, enricher, classifier."""

    control_group: str = Field(..., description="Label of the control sub-group (must exist in control.groups)")
    disease_group: str = Field(..., description="Label of the disease sub-group (must exist in disease.groups)")
    comparison_label: Optional[str] = Field(
        default=None,
        description="Output folder name for this comparison (default: disease_group)",
    )


class SubclusterRequest(BaseModel):
    """
    Request to run MethylCluster on this group's samples before building centroids.
    When enabled and persist_centroids is True, the group is expanded into one sub-group per
    discovered cluster (e.g. healthy_c0, healthy_c1); centroids are built per cluster via MethylCentroid.
    When persist_centroids is False, MethylCluster runs and writes assignments only; the group
    remains a single centroid (all samples) as usual.
    """

    enabled: bool = Field(default=True, description="Enable sub-clustering for this group")
    method: Literal["centroid", "hdbscan", "hierarchical"] = Field(
        default="centroid",
        description="MethylCluster clustering method (centroid recommended for methylation data)",
    )
    metric: str = Field(
        default="jensen_shannon",
        description="Distance metric for clustering (e.g. jensen_shannon, hellinger)",
    )
    min_cluster_size: Optional[int] = Field(
        default=None,
        ge=2,
        description="Minimum samples per cluster (HDBSCAN); defaults to MethylCluster default if unset",
    )
    force_k: Optional[int] = Field(
        default=None,
        ge=2,
        description="Force K clusters (centroid/hierarchical); bypasses automatic K selection",
    )
    output_subdir: Optional[str] = Field(
        default=None,
        description="Subdir under project clustering output (default: side/group_label, e.g. control/healthy)",
    )
    persist_centroids: bool = Field(
        default=True,
        description="If True, run MethylCentroid per cluster and expand group to healthy_c0, healthy_c1, ...",
    )


class GroupConfig(BaseModel):
    """
    One cohort or one level within a cohort.
    If level_labels_path is set, this group is expanded into one logical group per level
    (sample_paths are split by the level column); centroid dirs are created per level.
    When subcluster is set and persist_centroids is True, MethylCluster runs first and the group
    is expanded into one sub-group per cluster (e.g. healthy_c0, healthy_c1); no single centroid
    for the parent label is built. When samples_base_path is set (project or group), sample_paths
    may be paths to CSV/text files containing only sample folder names (one per line or one column);
    each name is resolved to samples_base_path / name.
    """

    label: str = Field(..., description="Group label; used for centroid subdir name (e.g. healthy, pcancer)")
    sample_paths: List[str] = Field(
        default_factory=list,
        description="List of sample directory paths, or paths to files containing one path per line, "
        "JSON array, or (when samples_base_path is set) one sample name per line / single column CSV.",
    )
    level_labels_path: Optional[str] = Field(
        default=None,
        description="Optional path to CSV with columns mapping sample_path or sample name to level (path, level). "
        "When set, this group is expanded into one centroid dir per level; dir name is {label}_{level}.",
    )
    samples_base_path: Optional[str] = Field(
        default=None,
        description="Base directory to resolve sample names from file (overrides project-level samples_base_path). "
        "When set, entries in sample_paths files are treated as folder names and resolved to this path.",
    )
    subcluster: Optional[SubclusterRequest] = Field(
        default=None,
        description="When set, run MethylCluster on this group's samples; if persist_centroids, expand to one centroid per cluster.",
    )
    stages: Optional[List["GroupConfig"]] = Field(
        default=None,
        description="Optional child strata under this disease family (JSON key is 'stages' but children "
        "may be stage, molecular subtype, or any mutually exclusive bins—not only TNM stage). "
        "When set, parent must not use sample_paths; each child has its own label and sample_paths. "
        "Resolved centroid labels are {parent.label}_{child.label}. See methylutils/docs/COHORT_TREE.md.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Human-readable meaning of this cohort or stage (e.g. clinical stage); do not put file paths here.",
    )
    order_index: Optional[int] = Field(
        default=None,
        description="Optional numeric ordering hint for progression synthesis when JSON order is insufficient.",
    )

    @field_validator("description", mode="before")
    @classmethod
    def description_strip_empty(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @field_validator("label")
    @classmethod
    def label_non_empty(cls, v: str) -> str:
        if not (v and v.strip()):
            raise ValueError("label must be non-empty")
        return v.strip()

    @model_validator(mode="after")
    def stages_mutually_exclusive_with_parent_samples(self) -> "GroupConfig":
        if self.stages:
            if len(self.sample_paths) > 0:
                raise ValueError(
                    f"group {self.label!r}: use either sample_paths or stages, not both"
                )
            if len(self.stages) == 0:
                raise ValueError(f"group {self.label!r}: stages must be a non-empty list when set")
            for s in self.stages:
                if not s.sample_paths:
                    raise ValueError(
                        f"group {self.label!r}: stage {s.label!r} must define sample_paths"
                    )
                if s.stages is not None:
                    raise ValueError(
                        f"group {self.label!r}: nested stages under stage {s.label!r} are not supported in v1"
                    )
        return self


# Resolve forward references for nested GroupConfig.stages
GroupConfig.model_rebuild()
ControlDiseaseSide.model_rebuild()


class DerivedPaths(BaseModel):
    """
    Derived paths from a ProjectConfig.

    Project root is `{output_base}/{project_name}`. Step-level base directories
    live directly under that root. Comparison-specific outputs are resolved via
    helper methods such as `get_detection_output_dir()` rather than being stored
    here as individual fields.

    `centroid_dirs` contains one centroid directory per resolved group in
    control-then-disease order. `centroid1_dir` and `centroid2_dir` are the
    first two entries for backward compatibility with two-group callers.
    """

    output_base: str = Field(..., description="Project root directory (output_base/project_name)")
    centroid1_dir: str = Field(..., description="Centroid directory for group1 (or first group)")
    centroid2_dir: str = Field(..., description="Centroid directory for group2 (or second group)")
    centroid_dirs: List[str] = Field(
        default_factory=list,
        description="List of centroid directories, one per group (when N groups); same as [centroid1_dir, centroid2_dir] when N=2.",
    )
    detection_dir: str = Field(..., description="MethylDetector output directory (detections/)")
    mapper_dir: str = Field(..., description="MethylMapper output directory")
    enricher_dir: str = Field(..., description="MethylEnricher output directory")
    classifier_dir: str = Field(..., description="MethylClassifier output directory (classifiers/)")
    validator_dir: str = Field(..., description="MethylValidator/MethylPredictor output directory (predictors/)")
    alignment_qc_dir: str = Field(..., description="MethylAlignmentQC output directory (one JSON per sample)")

    @property
    def mapper_combined_csv(self) -> str:
        """Typical MethylMapper combined gene CSV path."""
        return str(Path(self.mapper_dir) / "all-gene_name-combined.csv")


class ProjectConfig(BaseModel):
    """
    Single source of truth for a MethylPipeline project.

    Preferred schema:
    - `controls` / `diseases` (or singular `control` / `disease`)
    - explicit `comparisons`
    - shared `chromosomes`, `contexts`, `path_remap`
    - tool parameters live in pipeline profiles (`actionConfig`) and site manifest

    Backward compatibility is retained for `group1`/`group2`, flat `groups`,
    and a few historically nested keys that are promoted to the top level at
    load time. ``step_config`` is not accepted (use profiles + site manifest).
    """

    project_name: str = Field(
        ...,
        description="Project identifier for metadata and naming (e.g. PCa_vs_Healthy)",
    )
    output_base: str = Field(
        ...,
        description="Global output folder; each project uses a subfolder {output_base}/{project_name} for all step outputs",
    )
    group1: Optional[GroupConfig] = Field(
        default=None,
        description="First cohort (e.g. control/healthy). Required when groups is not set.",
    )
    group2: Optional[GroupConfig] = Field(
        default=None,
        description="Second cohort (e.g. disease/cancer). Required when groups is not set.",
    )
    groups: Optional[List[GroupConfig]] = Field(
        default=None,
        description="Optional N groups (overrides group1/group2 when set). Each group can have level_labels_path for stratification.",
    )
    control: Optional[ControlDiseaseSide] = Field(
        default=None,
        description="When set with disease: control side (label + groups). Centroids go to centroids/controls/{group.label}.",
    )
    disease: Optional[ControlDiseaseSide] = Field(
        default=None,
        description="When set with control: disease side (label + groups). Centroids go to centroids/diseases/{disease_subdir}/{group.label}.",
    )
    comparisons: Optional[Union[List[ComparisonSpec], str]] = Field(
        default=None,
        description="When control+disease: list of {control_group, disease_group, comparison_label?} or shorthand: "
        '"control_vs_each_disease" (first control vs each disease), "all_pairs" (every control group × every disease leaf).',
    )
    cohort_hierarchy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional reporting tree: e.g. disease_families[{label, stage_labels[]}], control_strata[]. "
        "Filled automatically when using disease groups with stages if omitted.",
    )
    disease_name: Optional[str] = Field(
        default=None,
        description="Human-readable disease name for metadata (e.g. 'Prostate Cancer'). Use 'disease' when it's a string and 'diseases' for structure.",
    )
    laboratory: Optional[str] = Field(
        default=None,
        description="Laboratory name for metadata.",
    )
    batch: Optional[str] = Field(
        default=None,
        description="Batch identifier for metadata.",
    )
    samples_base_path: Optional[str] = Field(
        default=None,
        description="Base directory to resolve sample names. When set, group sample_paths that point to files "
        "can list only sample folder names (one per line or single-column CSV); each is resolved to this path.",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Shared chromosome list (e.g. ['1','2',...,'X','Y'])",
    )
    contexts: Optional[List[Literal["CG", "CHG", "CHH"]]] = Field(
        default=None,
        description="Shared contexts (e.g. ['CG'])",
    )
    path_remap: Optional[Dict[str, str]] = Field(
        default=None,
        description="Prefix replacement when sample paths moved (e.g. NAS); longest match applied",
    )
    regulatory: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Regulatory / analyte metadata (primary_analyte, stage, intended_use_summary, …).",
    )
    validation_partitions: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Cohort partition CSV paths for validation governance (development_train, locked_test, …).",
    )
    survival_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional CSV sidecar for time-to-event modeling: sample_id, time, event, "
            "and optional psa/alp/ldh/predicted_ctdna_fraction. Operator-supplied; "
            "no in-process ctdna.org call."
        ),
    )
    progression_order: Optional[Literal["from_stages", "from_comparisons", "explicit"]] = Field(
        default=None,
        description="How to order disease stages for progression synthesis. Default: from_stages when nested stages exist.",
    )
    progression_labels: Optional[List[str]] = Field(
        default=None,
        description="Explicit comparison tokens when progression_order is 'explicit'.",
    )
    _sample_qc_cache: Dict[str, List[Tuple[str, List[str], Literal["control", "disease"]]]] = PrivateAttr(
        default_factory=dict
    )

    @field_validator("output_base")
    @classmethod
    def output_base_stripped(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    @field_validator("contexts", mode="before")
    @classmethod
    def normalize_contexts(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            tokens = [value]
        elif isinstance(value, (list, tuple, set)):
            tokens = list(value)
        else:
            return value
        normalized: List[str] = []
        allowed = {"CG", "CHG", "CHH"}
        for token in tokens:
            text = str(token).strip().upper()
            if not text:
                continue
            if text not in allowed:
                raise ValueError(f"contexts must contain only {sorted(allowed)}")
            normalized.append(text)
        return normalized or None

    @model_validator(mode="before")
    @classmethod
    def reject_step_config(cls, data: Any) -> Any:
        if isinstance(data, dict) and "step_config" in data:
            raise ValueError(
                "step_config was removed from study manifests. "
                "Use pipeline profile actionConfig + site manifest. "
                "Run: python scripts/migrate_project_config.py <legacy.json>"
            )
        return data

    @model_validator(mode="before")
    @classmethod
    def promote_legacy_top_level_keys(cls, data: Any) -> Any:
        """Promote nested validation.regulatory and validation_partitions from legacy shapes."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("regulatory") is None:
            val = data.get("validation")
            if isinstance(val, dict) and isinstance(val.get("regulatory"), dict):
                data["regulatory"] = val["regulatory"]
        if data.get("validation_partitions") is None:
            val = data.get("validation")
            if isinstance(val, dict) and isinstance(val.get("validation_partitions"), dict):
                data["validation_partitions"] = val["validation_partitions"]
        if data.get("progression_order") is None and data.get("disease") is not None:
            data.setdefault("progression_order", "from_stages")
        return data

    @model_validator(mode="before")
    @classmethod
    def normalize_control_disease_keys(cls, data: Any) -> Any:
        """Normalize accepted project-schema variants.

        Supported compatibility shims:
        - `controls` / `diseases` -> `control` / `disease`
        - string `disease` + structured `diseases` -> `disease_name` + `disease`
        - nested project-level keys under `control` or `disease` are promoted to
          the top level when missing there
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "controls" in data and "control" not in data:
            data["control"] = data.pop("controls")
        # disease: string = metadata; diseases: object = structure
        if "disease" in data and isinstance(data["disease"], str) and "diseases" in data:
            data["disease_name"] = data.pop("disease")
            data["disease"] = data.pop("diseases")
        elif "diseases" in data and "disease" not in data:
            data["disease"] = data.pop("diseases")
        if (
            data.get("control") is not None
            and data.get("disease") is not None
            and data.get("comparisons") is None
        ):
            ctrl = data["control"]
            nctrl = 0
            if isinstance(ctrl, dict):
                gr = ctrl.get("groups")
                if isinstance(gr, list):
                    nctrl = len(gr)
            # Multiple control strata → full bipartite vs every disease leaf; single control keeps legacy default.
            data["comparisons"] = "all_pairs" if nctrl > 1 else "control_vs_each_disease"
        # Promote project-level keys from control/disease side to top level when missing at root
        for side_key in ("control", "disease"):
            side = data.get(side_key)
            if not isinstance(side, dict):
                continue
            for key in ("comparisons", "chromosomes", "contexts", "path_remap", "regulatory", "validation_partitions"):
                if key in side and (key not in data or data.get(key) is None):
                    data[key] = side[key]
        return data

    @model_validator(mode="after")
    def require_group_def(self):
        """Require either (group1+group2), or groups, or (control+disease+comparisons)."""
        has_flat = self.groups is not None or (self.group1 is not None and self.group2 is not None)
        has_control_disease = self.control is not None and self.disease is not None
        if has_control_disease:
            if not self.control.groups:
                raise ValueError("control.groups must be non-empty when control is set")
            if not self.disease.groups:
                raise ValueError("disease.groups must be non-empty when disease is set")
            if self.comparisons is None:
                raise ValueError("comparisons is required when control and disease are set")
        if not has_flat and not has_control_disease:
            raise ValueError("Set either group1+group2, or groups, or control+disease+comparisons")
        if has_flat and has_control_disease:
            raise ValueError("Do not set both flat groups (or group1/group2) and control/disease")
        return self

    @model_validator(mode="after")
    def autofill_cohort_hierarchy(self) -> "ProjectConfig":
        """When diseases use nested stages and cohort_hierarchy is unset, derive metadata for reporting."""
        if self.cohort_hierarchy is not None:
            return self
        if self.control is None or self.disease is None:
            return self
        families: List[Dict[str, Any]] = []
        for g in self.disease.groups:
            if g.stages:
                families.append(
                    {
                        "label": g.label,
                        "stage_labels": [s.label for s in g.stages],
                        "leaves": [f"{g.label}_{s.label}" for s in g.stages],
                    }
                )
        if not families:
            return self
        ctr = [g.label for g in self.control.groups]
        return self.model_copy(
            update={
                "cohort_hierarchy": {
                    "version": 1,
                    "control_strata": ctr,
                    "disease_families": families,
                }
            }
        )

    def _base_for(self, g: Any) -> Optional[str]:
        return getattr(g, "samples_base_path", None) or getattr(self, "samples_base_path", None)

    def _chrom_context_pairs(self) -> List[Tuple[str, str]]:
        chroms_raw: List[str]
        if isinstance(self.chromosomes, list) and self.chromosomes:
            chroms_raw = [str(c) for c in self.chromosomes]
        elif isinstance(self.chromosomes, str) and self.chromosomes.strip():
            chroms_raw = [str(self.chromosomes).strip()]
        else:
            chroms_raw = []
        ctx_raw: List[str]
        if isinstance(self.contexts, list) and self.contexts:
            ctx_raw = [str(c) for c in self.contexts]
        elif isinstance(self.contexts, str) and self.contexts.strip():
            ctx_raw = [str(self.contexts).strip()]
        else:
            ctx_raw = ["CG"]
        if not chroms_raw:
            return []
        return [(chrom, ctx) for chrom in chroms_raw for ctx in ctx_raw]

    @staticmethod
    def _looks_like_path_entry(entry: str) -> bool:
        e = str(entry or "").strip()
        return bool(e) and ("/" in e or "\\" in e or e.endswith(".h5"))

    def _sample_h5_qc_record(self, sample_path: str) -> Dict[str, Any]:
        text = str(sample_path or "").strip()
        p = Path(text).expanduser()
        sample_id = p.name if p.name else text
        if not self._looks_like_path_entry(text):
            return {
                "sample_path": text,
                "sample_id": sample_id,
                "status": "unchecked_non_path",
                "reason": "",
                "expected_h5": 0,
                "found_h5": 0,
                "first_expected_missing_path": "",
                "eligible": True,
            }
        expected = 0
        found = 0
        first_missing: Optional[str] = None
        if p.suffix.lower() == ".h5" or p.is_file():
            expected = 1
            found = 1 if p.is_file() else 0
            if found == 0:
                first_missing = str(p)
        else:
            pairs = self._chrom_context_pairs()
            if pairs:
                expected = len(pairs)
                for chrom, ctx in pairs:
                    candidate = p / f"{chrom}-{ctx}.h5"
                    if candidate.is_file():
                        found += 1
                    elif first_missing is None:
                        first_missing = str(candidate)
            else:
                return {
                    "sample_path": text,
                    "sample_id": sample_id,
                    "status": "unchecked_missing_chromosome_scope",
                    "reason": "",
                    "expected_h5": 0,
                    "found_h5": 0,
                    "first_expected_missing_path": "",
                    "eligible": True,
                }
        eligible = found > 0
        return {
            "sample_path": text,
            "sample_id": sample_id,
            "status": "eligible" if eligible else "ineligible",
            "reason": "" if eligible else "excluded_no_h5",
            "expected_h5": int(expected),
            "found_h5": int(found),
            "first_expected_missing_path": str(first_missing or ""),
            "eligible": bool(eligible),
        }

    def _write_sample_qc_artifacts(
        self,
        records: List[Dict[str, Any]],
        eligible_sample_paths: List[str],
        ineligible_sample_paths: List[str],
    ) -> None:
        import csv

        qc_dir = Path(self.get_project_root()) / "sample_qc"
        qc_dir.mkdir(parents=True, exist_ok=True)
        csv_path = qc_dir / "sample_qc_report.csv"
        fields = [
            "sample_id",
            "sample_path",
            "status",
            "reason",
            "expected_h5",
            "found_h5",
            "first_expected_missing_path",
        ]
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for rec in records:
                writer.writerow({k: rec.get(k, "") for k in fields})

        with open(qc_dir / "eligible_samples.txt", "w", encoding="utf-8") as f:
            for p in sorted(set(eligible_sample_paths)):
                f.write(f"{p}\n")
        with open(qc_dir / "ineligible_samples.txt", "w", encoding="utf-8") as f:
            for p in sorted(set(ineligible_sample_paths)):
                f.write(f"{p}\n")
        for artifact in (
            csv_path,
            qc_dir / "eligible_samples.txt",
            qc_dir / "ineligible_samples.txt",
        ):
            try:
                artifact.chmod(0o666)
            except OSError:
                logger.debug("Could not share-mode %s", artifact, exc_info=True)

    def _apply_sample_qc_with_side(
        self,
        groups_with_side: List[Tuple[str, List[str], Literal["control", "disease"]]],
        *,
        cache_key: str,
    ) -> List[Tuple[str, List[str], Literal["control", "disease"]]]:
        cached = self._sample_qc_cache.get(cache_key)
        if cached is not None:
            return [(lbl, list(paths), side) for lbl, paths, side in cached]

        qc_map: Dict[str, Dict[str, Any]] = {}
        ordered_records: List[Dict[str, Any]] = []
        filtered: List[Tuple[str, List[str], Literal["control", "disease"]]] = []
        eligible_paths: List[str] = []
        ineligible_paths: List[str] = []
        removed_by_group: List[Tuple[str, str, List[str]]] = []

        for label, paths, side in groups_with_side:
            keep: List[str] = []
            removed_ids: List[str] = []
            for p in paths:
                if p not in qc_map:
                    rec = self._sample_h5_qc_record(p)
                    qc_map[p] = rec
                    ordered_records.append(rec)
                rec = qc_map[p]
                if bool(rec.get("eligible", False)):
                    keep.append(p)
                    eligible_paths.append(p)
                else:
                    ineligible_paths.append(p)
                    removed_ids.append(str(rec.get("sample_id") or Path(str(p)).name))
            if removed_ids:
                removed_by_group.append((label, side, removed_ids))
            filtered.append((label, keep, side))

        for label, side, removed_ids in removed_by_group:
            logger.warning(
                "Sample QC: removed %s sample(s) from %s group %r due to missing H5 evidence: %s",
                len(removed_ids),
                side,
                label,
                ", ".join(sorted(set(removed_ids))),
            )

        removed_total = len(set(ineligible_paths))
        if removed_total:
            logger.warning(
                "Sample QC summary: eligible=%s ineligible=%s (reason=excluded_no_h5).",
                len(set(eligible_paths)),
                removed_total,
            )
        else:
            logger.info(
                "Sample QC summary: eligible=%s ineligible=0 (all samples have required H5 evidence).",
                len(set(eligible_paths)),
            )
        self._write_sample_qc_artifacts(ordered_records, eligible_paths, ineligible_paths)

        emptied = [f"{side}:{label}" for label, paths, side in filtered if len(paths) == 0]
        if emptied:
            raise ValueError(
                "Sample QC removed all samples from cohort(s): "
                + ", ".join(emptied)
                + ". Check sample names/paths and required H5 files."
            )

        self._sample_qc_cache[cache_key] = [(lbl, list(paths), side) for lbl, paths, side in filtered]
        return [(lbl, list(paths), side) for lbl, paths, side in filtered]

    def _expand_side_groups(
        self, side_groups: List[GroupConfig], base_for_fn: Any, resolve_paths: bool = True
    ) -> List[Tuple[str, List[str]]]:
        """Expand a list of GroupConfig (with optional level_labels_path) to (label, paths) list.
        When resolve_paths=False, returns [(g.label, []) for each g] (no file I/O; for label validation).
        """
        if not resolve_paths:
            out: List[Tuple[str, List[str]]] = []
            for g in side_groups:
                if g.stages:
                    for st in g.stages:
                        out.append((f"{g.label}_{st.label}", []))
                else:
                    out.append((g.label, []))
            return out
        out = []
        for g in side_groups:
            if g.stages:
                for st in g.stages:
                    paths = _resolve_sample_paths(st.sample_paths, base_path=base_for_fn(st))
                    if st.level_labels_path:
                        path_to_level = _load_level_labels(st.level_labels_path)
                        by_level: Dict[str, List[str]] = {}
                        for p in paths:
                            level = (
                                path_to_level.get(p)
                                or path_to_level.get(str(Path(p).resolve()))
                                or path_to_level.get(Path(p).name)
                            )
                            if level is None:
                                level = "default"
                            by_level.setdefault(level, []).append(p)
                        for level, level_paths in sorted(by_level.items()):
                            out.append((f"{g.label}_{st.label}_{level}", level_paths))
                    else:
                        out.append((f"{g.label}_{st.label}", paths))
                continue
            paths = _resolve_sample_paths(g.sample_paths, base_path=base_for_fn(g))
            if g.level_labels_path:
                path_to_level = _load_level_labels(g.level_labels_path)
                by_level: Dict[str, List[str]] = {}
                for p in paths:
                    level = (
                        path_to_level.get(p)
                        or path_to_level.get(str(Path(p).resolve()))
                        or path_to_level.get(Path(p).name)
                    )
                    if level is None:
                        level = "default"
                    by_level.setdefault(level, []).append(p)
                for level, level_paths in sorted(by_level.items()):
                    out.append((f"{g.label}_{level}", level_paths))
            else:
                out.append((g.label, paths))
        return out

    def _expand_one_with_manifest(
        self,
        label: str,
        paths: List[str],
        side: Literal["control", "disease"],
    ) -> List[Tuple[str, List[str], Literal["control", "disease"]]]:
        """
        If this group has subcluster+persist_centroids and a clustering manifest exists,
        return [(derived_label, derived_paths, side), ...]; otherwise return [(label, paths, side)].
        """
        subcluster_groups = {(s, l) for s, l, g in self.get_groups_with_subcluster() if g.subcluster.persist_centroids}
        if (side, label) not in subcluster_groups:
            return [(label, paths, side)]
        manifest_path = Path(self.get_clustering_output_dir(side, label)) / "manifest.json"
        if not manifest_path.exists():
            return [(label, paths, side)]
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            return [(label, paths, side)]
        derived = manifest.get("derived_labels", [])
        groups_map = manifest.get("groups", {})
        if not derived or not groups_map:
            return [(label, paths, side)]
        return [(dl, groups_map.get(dl, []), side) for dl in derived]

    def _get_resolved_groups_with_side(
        self,
        expand_subclusters: bool = False,
    ) -> List[Tuple[str, List[str], Literal["control", "disease"]]]:
        """
        Return list of (label, sample_paths, side) for each centroid group.
        When control/disease: control groups first (side=control), then disease (side=disease).
        When flat groups: first group = control, rest = disease.
        If expand_subclusters is True and a clustering manifest exists for a group with
        subcluster+persist_centroids, that group is expanded into (derived_label, paths, side) per cluster.
        """
        base_for = self._base_for
        if self.control is not None and self.disease is not None:
            out: List[Tuple[str, List[str], Literal["control", "disease"]]] = []
            for label, paths in self._expand_side_groups(self.control.groups, base_for):
                if expand_subclusters:
                    out.extend(self._expand_one_with_manifest(label, paths, "control"))
                else:
                    out.append((label, paths, "control"))
            for label, paths in self._expand_side_groups(self.disease.groups, base_for):
                if expand_subclusters:
                    out.extend(self._expand_one_with_manifest(label, paths, "disease"))
                else:
                    out.append((label, paths, "disease"))
            return self._apply_sample_qc_with_side(
                out, cache_key=f"with_side:control_disease:{bool(expand_subclusters)}"
            )
        # Flat groups or group1/group2: no subcluster expansion
        if self.groups:
            resolved = self._expand_side_groups(self.groups, base_for)
        else:
            if self.group1 is None or self.group2 is None:
                raise ValueError("group1 and group2 are required when groups is not set")
            resolved = [
                (self.group1.label, _resolve_sample_paths(self.group1.sample_paths, base_path=base_for(self.group1))),
                (self.group2.label, _resolve_sample_paths(self.group2.sample_paths, base_path=base_for(self.group2))),
            ]
        with_side = [
            (label, paths, "control" if i == 0 else "disease")
            for i, (label, paths) in enumerate(resolved)
        ]
        return self._apply_sample_qc_with_side(with_side, cache_key="with_side:flat")

    def _get_resolved_groups(self, expand_subclusters: bool = False) -> List[Tuple[str, List[str]]]:
        """
        Return list of (label, sample_paths) for each centroid group.
        When control/disease: control groups first, then disease. When groups set, expands by level_labels_path.
        If expand_subclusters is True, groups with subcluster+persist_centroids are expanded from clustering manifests.
        """
        with_side = self._get_resolved_groups_with_side(expand_subclusters=expand_subclusters)
        return [(label, list(paths)) for label, paths, _ in with_side]

    # Legacy fallback for flat two-group configs that do not have a disease-side label.
    CENTROID_DISEASE_SUBDIR: ClassVar[str] = "cancer"
    CENTROID_CONTROL_SUBDIR: ClassVar[str] = "controls"
    CENTROID_DISEASE_FOLDER: ClassVar[str] = "diseases"

    def _get_disease_subdir(self) -> str:
        """Middle path segment for disease step dirs: <step>/<disease_label>/<disease_group>. Uses disease.label from config when control/disease."""
        return self.disease.label if self.disease is not None else self.CENTROID_DISEASE_SUBDIR

    def get_centroid_dir(self, side: Literal["control", "disease"], group_label: str) -> str:
        """Return centroid output dir for one resolved group."""
        global_base = self.output_base.rstrip("/")
        project_root = f"{global_base}/{self.project_name}"
        disease_sub = self._get_disease_subdir()
        if self.control is not None and self.disease is not None:
            if side == "control":
                return f"{project_root}/centroids/{self.CENTROID_CONTROL_SUBDIR}/{self.control.label}/{group_label}"
            return f"{project_root}/centroids/{self.CENTROID_DISEASE_FOLDER}/{disease_sub}/{group_label}"
        # Flat: control = centroids/controls/{label}, disease = centroids/diseases/{disease_sub}/{label}
        if side == "control":
            return f"{project_root}/centroids/{self.CENTROID_CONTROL_SUBDIR}/{group_label}"
        return f"{project_root}/centroids/{self.CENTROID_DISEASE_FOLDER}/{disease_sub}/{group_label}"

    def get_clustering_output_dir(self, side: Literal["control", "disease"], group_label: str) -> str:
        """Return MethylCluster output dir for one resolved group."""
        disease_sub = self._get_disease_subdir()
        if self.control is not None and self.disease is not None and side == "control":
            return f"{self.get_project_root()}/clustering/{self.CENTROID_CONTROL_SUBDIR}/{self.control.label}/{group_label}"
        if side == "control":
            return f"{self.get_project_root()}/clustering/{self.CENTROID_CONTROL_SUBDIR}/{group_label}"
        return f"{self.get_project_root()}/clustering/{self.CENTROID_DISEASE_FOLDER}/{disease_sub}/{group_label}"

    def get_groups_with_subcluster(self) -> List[Tuple[Literal["control", "disease"], str, "GroupConfig"]]:
        """Return (side, label, group_config) for each group that has subcluster enabled. Only for control/disease projects."""
        if self.control is None or self.disease is None:
            return []
        out: List[Tuple[Literal["control", "disease"], str, GroupConfig]] = []
        for g in self.control.groups:
            if g.subcluster and g.subcluster.enabled:
                out.append(("control", g.label, g))
        for g in self.disease.groups:
            if g.stages:
                for st in g.stages:
                    if st.subcluster and st.subcluster.enabled:
                        out.append(("disease", f"{g.label}_{st.label}", st))
            elif g.subcluster and g.subcluster.enabled:
                out.append(("disease", g.label, g))
        return out

    def cohort_tree_dict(self) -> Dict[str, Any]:
        """
        Cohort tree / hierarchy metadata for reporting and tooling (v1).

        Returns ``cohort_hierarchy`` when set or autofill from nested disease ``stages``;
        otherwise a minimal summary with ``resolved_leaves`` for control/disease projects.
        """
        if self.cohort_hierarchy is not None:
            return dict(self.cohort_hierarchy)
        if self.control is not None and self.disease is not None:
            return {
                "version": 1,
                "control_strata": [g.label for g in self.control.groups],
                "disease_parent_labels": [g.label for g in self.disease.groups],
                "resolved_leaves": [x[0] for x in self.get_resolved_groups()],
            }
        return {}

    def get_comparisons(self, expand_subclusters: bool = False) -> List[ComparisonSpec]:
        """
        Return list of ComparisonSpec. When control/disease: resolve shorthand or validate explicit list.
        When flat groups: not supported (caller should use get_resolved_groups and treat first as control).
        If expand_subclusters is True, control/disease labels include derived labels from clustering manifests.
        """
        if self.control is None or self.disease is None:
            return []
        if expand_subclusters:
            with_side = self._get_resolved_groups_with_side(expand_subclusters=True)
            control_labels_resolved = {label for label, _, s in with_side if s == "control"}
            disease_labels_resolved = {label for label, _, s in with_side if s == "disease"}
            control_resolved = [(l, []) for l, _, s in with_side if s == "control"]
            disease_resolved = [(l, []) for l, _, s in with_side if s == "disease"]
        else:
            control_resolved = self._expand_side_groups(self.control.groups, self._base_for, resolve_paths=False)
            disease_resolved = self._expand_side_groups(self.disease.groups, self._base_for, resolve_paths=False)
            control_labels_resolved = {label for label, _ in control_resolved}
            disease_labels_resolved = {label for label, _ in disease_resolved}

        raw = self.comparisons
        if isinstance(raw, str):
            if raw == "control_vs_each_disease":
                first_control = control_resolved[0][0] if control_resolved else ""
                return [
                    ComparisonSpec(control_group=first_control, disease_group=d)
                    for d, _ in disease_resolved
                ]
            if raw == "all_pairs":
                return [
                    ComparisonSpec(control_group=c, disease_group=d)
                    for c, _ in control_resolved
                    for d, _ in disease_resolved
                ]
            raise ValueError(f"comparisons shorthand must be 'control_vs_each_disease' or 'all_pairs', got: {raw!r}")
        out: List[ComparisonSpec] = []
        for spec in raw:
            if isinstance(spec, dict):
                spec = ComparisonSpec.model_validate(spec)
            if spec.control_group not in control_labels_resolved:
                raise ValueError(
                    f"comparisons: control_group {spec.control_group!r} not in control.groups (resolved: {sorted(control_labels_resolved)})"
                )
            if spec.disease_group not in disease_labels_resolved:
                raise ValueError(
                    f"comparisons: disease_group {spec.disease_group!r} not in disease.groups (resolved: {sorted(disease_labels_resolved)})"
                )
            out.append(ComparisonSpec(
                control_group=spec.control_group,
                disease_group=spec.disease_group,
                comparison_label=spec.comparison_label if spec.comparison_label is not None else spec.disease_group,
            ))
        return out

    def disease_parent_for_resolved_leaf(self, disease_leaf: str) -> str:
        """
        Map a resolved disease centroid label (e.g. ``pca_pca1``) to its disease parent group label (``pca``).

        Used to build hierarchical panel ``families`` keyed by parent without duplicating cohort JSON.
        """
        if self.disease is None:
            return disease_leaf
        for g in self.disease.groups:
            if g.stages:
                for st in g.stages:
                    if f"{g.label}_{st.label}" == disease_leaf:
                        return g.label
            else:
                if g.label == disease_leaf:
                    return g.label
        return disease_leaf

    def get_ordered_comparison_labels(self, expand_subclusters: bool = False) -> List[str]:
        """
        Ordered comparison tokens for progression / reporting.

        Uses ``progression_order`` when set; otherwise ``from_stages`` when nested
        stages exist, else comparison list order.
        """
        comparisons = self.get_comparisons(expand_subclusters=expand_subclusters)
        if not comparisons:
            return []
        by_dg = {c.disease_group: (c.comparison_label or c.disease_group) for c in comparisons}

        mode = self.progression_order
        if mode is None:
            mode = "from_stages" if self._stage_ordered_disease_leaves() else "from_comparisons"

        if mode == "explicit" and self.progression_labels:
            return [str(x) for x in self.progression_labels]

        if mode == "from_stages":
            ordered_leaves = self._stage_ordered_disease_leaves()
            if ordered_leaves:
                return [by_dg[leaf] for leaf in ordered_leaves if leaf in by_dg]

        return [(s.comparison_label or s.disease_group) for s in comparisons]

    def _stage_ordered_disease_leaves(self) -> List[str]:
        """Resolved disease leaf labels in stage order (order_index, then JSON order)."""
        if self.disease is None:
            return []
        leaves: List[tuple[int, int, str]] = []
        seq = 0
        for g in self.disease.groups:
            if g.stages:
                for st in g.stages:
                    idx = st.order_index if st.order_index is not None else seq
                    leaves.append((idx, seq, f"{g.label}_{st.label}"))
                    seq += 1
            else:
                idx = g.order_index if g.order_index is not None else seq
                leaves.append((idx, seq, g.label))
                seq += 1
        leaves.sort(key=lambda t: (t[0], t[1]))
        return [label for _, _, label in leaves]

    def _description_for_disease_group(self, disease_group: str) -> Optional[str]:
        """Return optional ``description`` from the disease ``GroupConfig`` matching this resolved leaf label."""
        if self.disease is None:
            return None
        dg = disease_group
        for g in self.disease.groups:
            if g.stages:
                for st in g.stages:
                    leaf = f"{g.label}_{st.label}"
                    if dg == leaf or dg.startswith(f"{leaf}_"):
                        d = st.description
                        if isinstance(d, str) and d.strip():
                            return d.strip()
                        return None
            else:
                if g.label == dg:
                    d = g.description
                    if isinstance(d, str) and d.strip():
                        return d.strip()
                    return None
        return None

    def get_ordered_stage_narratives(self, ordered_tokens: Sequence[str]) -> List[Dict[str, Any]]:
        """
        For each progression comparison token (in order), return comparison label, filesystem disease_group,
        and optional human ``description`` from project JSON. Omits sample_paths (path-free for LLM context).
        """
        comparisons = self.get_comparisons()
        if not comparisons:
            return []
        by_disease_group = {c.disease_group: c for c in comparisons}
        by_label = {(c.comparison_label or c.disease_group): c for c in comparisons}
        out: List[Dict[str, Any]] = []
        for token in ordered_tokens:
            spec = by_disease_group.get(token) or by_label.get(token)
            if spec is None:
                out.append({"comparison_label": token, "disease_group": None, "description": None})
                continue
            dg = spec.disease_group
            desc = self._description_for_disease_group(dg)
            out.append({"comparison_label": token, "disease_group": dg, "description": desc})
        return out

    def derive_panel_spec_from_comparisons(
        self,
        expand_subclusters: bool = False,
        indeterminate_delta: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Build a hierarchical ``panel`` dict (``primary_family``, ``families``, ``indeterminate_delta``)
        from :meth:`get_comparisons`, grouping disease leaves under each disease parent.

        Returns ``None`` when not control/disease, when there are no comparisons, or when fewer than
        two distinct disease leaves appear (binary / single-disease layouts do not need a panel).
        """
        if not self.uses_control_disease():
            return None
        specs = self.get_comparisons(expand_subclusters=expand_subclusters)
        if not specs:
            return None
        disease_leaves_ordered: List[str] = []
        seen: set[str] = set()
        for s in specs:
            if s.disease_group not in seen:
                seen.add(s.disease_group)
                disease_leaves_ordered.append(s.disease_group)
        if len(disease_leaves_ordered) < 2:
            return None

        families: "OrderedDict[str, List[str]]" = OrderedDict()
        for s in specs:
            dg = s.disease_group
            parent = self.disease_parent_for_resolved_leaf(dg)
            if parent not in families:
                families[parent] = []
            if dg not in families[parent]:
                families[parent].append(dg)

        first_parent = self.disease_parent_for_resolved_leaf(specs[0].disease_group)
        if first_parent not in families:
            first_parent = next(iter(families.keys()))

        delta = 0.25 if indeterminate_delta is None else float(indeterminate_delta)
        return {
            "primary_family": first_parent,
            "families": {k: list(v) for k, v in families.items()},
            "indeterminate_delta": delta,
        }

    def get_derived_paths(self, expand_subclusters: bool = False) -> DerivedPaths:
        """Compute derived paths from this project config. Project root is {output_base}/{project_name}. When expand_subclusters is True, centroid_dirs include derived groups from clustering manifests."""
        global_base = self.output_base.rstrip("/")
        project_root = f"{global_base}/{self.project_name}"
        with_side = self._get_resolved_groups_with_side(expand_subclusters=expand_subclusters)
        centroid_dirs_list = [self.get_centroid_dir(side, label) for label, _, side in with_side]
        c1 = centroid_dirs_list[0] if len(centroid_dirs_list) >= 1 else ""
        c2 = centroid_dirs_list[1] if len(centroid_dirs_list) >= 2 else c1
        return DerivedPaths(
            output_base=project_root,
            centroid1_dir=c1,
            centroid2_dir=c2,
            centroid_dirs=centroid_dirs_list,
            detection_dir=f"{project_root}/detections",
            mapper_dir=f"{project_root}/mapper",
            enricher_dir=f"{project_root}/enricher",
            classifier_dir=f"{project_root}/classifiers",
            validator_dir=f"{project_root}/predictors",
            alignment_qc_dir=f"{project_root}/alignment_qc",
        )

    def get_group1_sample_paths(self) -> List[str]:
        """Return resolved sample paths for group1 (or first group when using N groups)."""
        resolved = self._get_resolved_groups()
        return list(resolved[0][1]) if resolved else []

    def get_group2_sample_paths(self) -> List[str]:
        """Return resolved sample paths for group2 (or second group when using N groups)."""
        resolved = self._get_resolved_groups()
        return list(resolved[1][1]) if len(resolved) > 1 else []

    def get_group_sample_paths(self, index: int) -> List[str]:
        """Return resolved sample paths for the group at the given index (0-based)."""
        resolved = self._get_resolved_groups()
        if index < 0 or index >= len(resolved):
            raise IndexError(f"group index {index} out of range (have {len(resolved)} groups)")
        return list(resolved[index][1])

    def get_group_label(self, index: int) -> str:
        """Return the label for the group at the given index (0-based)."""
        resolved = self._get_resolved_groups()
        if index < 0 or index >= len(resolved):
            raise IndexError(f"group index {index} out of range (have {len(resolved)} groups)")
        return resolved[index][0]

    def get_group_sample_paths_by_label(self, label: str, expand_subclusters: bool = False) -> List[str]:
        """Return sample paths for the group with the given label (from control or disease). When expand_subclusters is True, derived labels (e.g. healthy_c0) are resolved from manifests."""
        resolved = self._get_resolved_groups(expand_subclusters=expand_subclusters)
        for l, paths in resolved:
            if l == label:
                return list(paths)
        raise ValueError(f"group label {label!r} not found in resolved groups")

    def get_resolved_groups(self, expand_subclusters: bool = False) -> List[tuple]:
        """Return list of (label, sample_paths). When expand_subclusters is True, groups with subcluster+persist_centroids are expanded from clustering manifests."""
        return self._get_resolved_groups(expand_subclusters=expand_subclusters)

    def get_regulatory_config(self) -> Dict[str, Any]:
        """Return top-level regulatory metadata (no analyte profile merge)."""
        return dict(self.regulatory) if isinstance(self.regulatory, dict) else {}

    def get_primary_analyte(self) -> Optional[str]:
        """Canonical primary analyte from regulatory config, if set."""
        from .analyte_profiles import normalize_primary_analyte

        return normalize_primary_analyte(self.get_regulatory_config().get("primary_analyte"))

    def get_primary_modality(self) -> str:
        """Canonical omics modality (``methylation`` default, or ``rnaseq``).

        Modality is the assay axis and selects the process pack (methylation vs
        RNA-Seq). It is independent of ``primary_analyte`` (the methylation sample
        matrix). Defaults to ``methylation`` for backward compatibility when no
        ``regulatory.primary_modality`` is set.
        """
        from .analyte_profiles import normalize_primary_modality

        modality = normalize_primary_modality(
            self.get_regulatory_config().get("primary_modality")
        )
        return modality or "methylation"

    def get_project_root(self) -> str:
        """Project root directory: {output_base}/{project_name}."""
        return f"{self.output_base.rstrip('/')}/{self.project_name}"

    def get_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        """Output dir for detection for one comparison: detections/<control_group>/<disease_group>."""
        return f"{self.get_project_root()}/detections/{control_group}/{disease_group}"

    def resolve_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        """
        Resolve an existing detection output directory for one comparison.

        Prefers the canonical ``get_detection_output_dir`` path, but reuses an
        on-disk sibling directory whose name matches case-insensitively. Legacy
        freeze runs often wrote lower-cased comparison folders (e.g. ``pca_pca1``)
        while newer project labels may be mixed-case (``PCa_PCa1``).
        """
        canonical = Path(self.get_detection_output_dir(control_group, disease_group))
        if canonical.exists():
            return str(canonical)
        parent = canonical.parent
        token = canonical.name.casefold()
        if parent.exists():
            for child in parent.iterdir():
                if child.is_dir() and child.name.casefold() == token:
                    return str(child)
        lower = canonical.with_name(canonical.name.lower())
        if lower.exists():
            return str(lower)
        return str(canonical)

    def get_mapper_output_dir(self, control_group: str, disease_group: str) -> str:
        """Output dir for mapper for one comparison: mapper/<control_group>/<disease_group>."""
        return f"{self.get_project_root()}/mapper/{control_group}/{disease_group}"

    def get_enricher_output_dir(self, control_group: str, disease_group: str) -> str:
        """Output dir for enricher for one comparison: enricher/<control_group>/<disease_group>."""
        return f"{self.get_project_root()}/enricher/{control_group}/{disease_group}"

    def get_classifier_output_dir(self, control_group: str, disease_group: str) -> str:
        """Output dir for classifier for one comparison: classifiers/<control_group>/<disease_group>."""
        return f"{self.get_project_root()}/classifiers/{control_group}/{disease_group}"

    def get_validator_output_dir(self, control_group: str, disease_group: str) -> str:
        """Output dir for validator/predictor for one comparison: predictors/<control_group>/<disease_group>."""
        return f"{self.get_project_root()}/predictors/{control_group}/{disease_group}"

    def uses_control_disease(self) -> bool:
        """True if this project uses control/disease + comparisons (not flat groups)."""
        return self.control is not None and self.disease is not None


def _load_level_labels(csv_path: str) -> Dict[str, str]:
    """
    Load path -> level mapping from a CSV file.
    Expects a header with a path column (path, sample_path, or first column) and a level column.
    """
    import csv
    path_col = level_col = None
    out: Dict[str, str] = {}
    p = Path(csv_path)
    if not p.is_file():
        raise FileNotFoundError(f"level_labels_path not found: {csv_path}")
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return out
        header_lower = [h.strip().lower() for h in header]
        for name in ("path", "sample_path", "sample"):
            if name in header_lower:
                path_col = header_lower.index(name)
                break
        if path_col is None:
            path_col = 0
        for name in ("level", "levels", "label", "labels", "stage", "grade"):
            if name in header_lower:
                level_col = header_lower.index(name)
                break
        if level_col is None:
            level_col = 1 if len(header) > 1 else 0
        for row in reader:
            if len(row) > max(path_col, level_col):
                path_val = row[path_col].strip()
                level_val = row[level_col].strip()
                if path_val and level_val:
                    out[path_val] = level_val
    return out


def _resolve_sample_paths(
    sample_paths: List[str],
    base_path: Optional[str] = None,
) -> List[str]:
    """
    Expand any path that points to a file (one path per line, JSON array, or single-column CSV of names)
    into a list of full paths. When base_path is set and the file contains sample names (no slashes),
    each name is resolved to base_path / name.
    """
    import csv
    import json
    out: List[str] = []
    base = Path(base_path).resolve() if base_path else None

    # Relative list files (e.g. "configs/healthy.csv") are commonly placed under
    # a project root while sample folders live under a sibling "data" directory.
    # Search a few deterministic roots before failing hard.
    search_roots: List[Path] = [Path.cwd()]
    if base is not None:
        search_roots.append(base)
        search_roots.append(base.parent)
    seen_roots: set[str] = set()
    deduped_roots: List[Path] = []
    for root in search_roots:
        key = str(root)
        if key not in seen_roots:
            seen_roots.add(key)
            deduped_roots.append(root)

    def find_list_file(entry: str) -> Optional[Path]:
        p_entry = Path(entry)
        if p_entry.is_absolute():
            return p_entry if p_entry.is_file() else None
        for root in deduped_roots:
            cand = root / entry
            if cand.is_file():
                return cand
        return None

    def resolve_entry(entry: str) -> str:
        entry = entry.strip()
        if not entry:
            return ""
        if base is not None and not _looks_like_absolute_path(entry):
            return str(base / entry)
        return entry

    for p in sample_paths:
        p = p.strip()
        if not p:
            continue
        path = find_list_file(p) or Path(p)
        if path.is_file():
            content = path.read_text().strip()
            if content.startswith("["):
                data = json.loads(content)
                for x in data:
                    r = resolve_entry(str(x))
                    if r:
                        out.append(r)
            elif path.suffix.lower() == ".csv":
                with open(path, newline="", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    first_row = next(reader, None)
                    if first_row is None:
                        pass
                    elif first_row and first_row[0].strip().lower() in ("sample", "path", "sample_path", "name", "id"):
                        for row in reader:
                            if row and row[0].strip():
                                r = resolve_entry(row[0])
                                if r:
                                    out.append(r)
                    else:
                        if first_row and first_row[0].strip():
                            r = resolve_entry(first_row[0])
                            if r:
                                out.append(r)
                        for row in reader:
                            if row and row[0].strip():
                                r = resolve_entry(row[0])
                                if r:
                                    out.append(r)
            else:
                for line in content.splitlines():
                    if line.strip().startswith("#"):
                        continue
                    r = resolve_entry(line)
                    if r:
                        out.append(r)
        else:
            if base is not None and (_looks_like_file_path(p) or "/" in p or "\\" in p):
                raise FileNotFoundError(
                    f"Sample list file not found: {path}. "
                    "Create the file (e.g. CSV with a 'sample' column of folder names) or run from the directory where it exists."
                )
            out.append(resolve_entry(p) if base else p)
    return out


def _looks_like_file_path(entry: str) -> bool:
    """True if entry looks like a path to a list file (.csv, .txt, etc.)."""
    e = entry.strip().lower()
    return e.endswith(".csv") or e.endswith(".txt") or e.endswith(".json")


def _looks_like_absolute_path(entry: str) -> bool:
    if not entry:
        return False
    if entry.startswith("/"):
        return True
    if len(entry) >= 2 and entry[1] == ":":
        return True
    if "/" in entry or "\\" in entry:
        return True
    return False


def load_project(
    path: Union[str, Path],
    output_base_override: Optional[str] = None,
) -> ProjectConfig:
    """
    Load and validate a project config from a JSON file.
    This is the single place pipeline steps use to resolve input/output paths
    (via the returned project's get_derived_paths(), get_centroid_dir(), etc.).

    If output_base_override is set, the project's output_base is replaced before
    any path resolution, so all derived paths (centroids, detections, classifiers, etc.)
    are computed under that base. Use when running on a different machine or directory
    than the one in the project JSON.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Project config not found: {path}")
    import json
    with open(path) as f:
        data = json.load(f)
    project = ProjectConfig.model_validate(data)
    if output_base_override is not None:
        project = project.model_copy(update={"output_base": output_base_override.rstrip("/")})
    return project
