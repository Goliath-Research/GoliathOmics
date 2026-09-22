"""
Resolve MethylClassifier config from a pipeline project config (Pydantic only).
Supports binary (centroid1/centroid2) and N-group multiclass (centroid_dirs + multiclass_class_names).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from methyl_utils import ProjectConfig, load_project
from methyl_utils.action_config_resolver import resolve_for_project

try:
    from methyl_utils.dmp_export_paths import first_classifier_dmps_csv
except ImportError:
    try:
        from methyl_detector.utils.dmp_export_paths import first_classifier_dmps_csv
    except ImportError:

        def first_classifier_dmps_csv(detection_dir: Path) -> Optional[Path]:
            if not detection_dir.exists():
                return None
            classifier = sorted(detection_dir.glob("dmps-*-classifier.csv"))
            if classifier:
                return classifier[0]
            for p in sorted(detection_dir.glob("dmps-*.csv")):
                if p.stem.endswith("-discovery"):
                    continue
                return p
            return None

from .models.config_schema import ClassificationConfig

CLASSIFIER_OUTPUT_FILENAME = "classification_results.csv"


def _resolved_training_cohort_paths_by_side(
    project: ProjectConfig,
) -> Optional[Tuple[List[str], List[str]]]:
    """Training cohort (control paths, disease paths) from project train sample lists."""
    if not project.uses_control_disease():
        return None
    control_paths: List[str] = []
    disease_paths: List[str] = []
    with_side = project._get_resolved_groups_with_side(expand_subclusters=False)
    for _label, paths, side in with_side:
        if side == "control":
            control_paths.extend(paths)
        else:
            disease_paths.extend(paths)
    if not control_paths or not disease_paths:
        return None
    return control_paths, disease_paths


def _apply_training_cohort_to_classifier_base(
    project: ProjectConfig,
    base: Dict[str, Any],
) -> None:
    """
    Use the project's training cohort for centroid validation when explicit sample lists
    are not already set (Monte Carlo train CSVs, production cohorts).
    """
    if base.get("centroid1_sample_paths") or base.get("centroid2_sample_paths"):
        return
    if base.get("samples"):
        return
    cohort = _resolved_training_cohort_paths_by_side(project)
    if cohort is None:
        return
    control_paths, disease_paths = cohort
    base["centroid1_sample_paths"] = list(control_paths)
    base["centroid2_sample_paths"] = list(disease_paths)


def default_ovr_unified_classifier_basename(project: ProjectConfig) -> str:
    """
    Basename MethylDetector writes under each comparison output dir:
    ``classifier-{chromosome}-{comma_sorted_contexts}.pkl`` (see methyldetector ``_save_unified_model``).
    OvR expansion probes the first project chromosome only to verify each detection dir exists.
    """
    chroms = project.chromosomes or ["1"]
    ctxs = project.contexts or ["CG"]
    ch = str(chroms[0])
    ctx_str = ",".join(sorted(str(c) for c in ctxs))
    return f"classifier-{ch}-{ctx_str}.pkl"


def _default_centroid_sample_root(project: ProjectConfig) -> Optional[str]:
    """
    Default root used to expand basename-only centroid ``samples_used`` entries.

    Centroid metadata in this workspace commonly stores sample IDs/basenames instead of
    absolute paths. In that case classifier centroid-validation must anchor them to the
    project sample root to build valid sample directories.
    """
    root = getattr(project, "samples_base_path", None)
    if root is None:
        return None
    s = str(root).strip()
    return s or None


# Merged into effective classifier step when predicting default OvR bundle save path.
_CLASSIFIER_STEP_KEYS_FOR_BUNDLE_PATH = (
    "ovr_binary_pickles_from_comparisons",
    "ovr_bundle_filename",
    "ovr_binary_model_paths",
    "ovr_detection_dirs",
    "ovr_pairwise_aggregate_control",
    "ovr_bipartite_aggregate",
    "ovr_n_control_classes",
)


def predicted_multiclass_ovr_bundle_path(
    project: ProjectConfig,
    classifier_step: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """
    Default ``save_classifier_path`` location for control/disease multiclass OvR:
    ``<project_root>/classifiers/<control_group>/<bundle>.pkl``.

    Applies when the project has ≥3 resolved groups and the classifier step requests
    OvR (``ovr_binary_pickles_from_comparisons`` or explicit ``ovr_binary_model_paths`` /
    ``ovr_detection_dirs`` with length ≥2). Does not check that the file exists.
    """
    step = dict(
        classifier_step
        if classifier_step is not None
        else (resolve_for_project("classifier", project))
    )
    if not project.uses_control_disease():
        return None
    resolved = project.get_resolved_groups()
    if len(resolved) < 3:
        return None
    ovr_dirs = step.get("ovr_detection_dirs") or []
    has_ovr = bool(step.get("ovr_binary_pickles_from_comparisons")) or len(
        step.get("ovr_binary_model_paths") or []
    ) >= 2 or len(ovr_dirs) >= 2 or (
        step.get("ovr_pairwise_aggregate_control")
        and len(ovr_dirs) >= 1
    ) or (bool(step.get("ovr_bipartite_aggregate")) and len(ovr_dirs) >= 2)
    if not has_ovr:
        return None
    ctrl_label = resolved[0][0]
    bundle_fn = step.get("ovr_bundle_filename")
    if bundle_fn:
        fn = str(bundle_fn)
        if not fn.endswith(".pkl"):
            fn = f"{fn}.pkl"
    else:
        fn = f"classifier_{ctrl_label}_{project.project_name}.pkl"
    return Path(project.get_project_root()) / "classifiers" / ctrl_label / fn


def merge_project_classifier_step(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Merged classifier config from profile actionConfig, including optional JSON overrides."""
    project = load_project(project_path)
    step_cfg = dict(resolve_for_project("classifier", project))
    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path) as f:
                step_cfg.update(json.load(f))
    return step_cfg


def classifier_step_dict_has_ovr_sources(step_cfg: Dict[str, Any]) -> bool:
    """True if merged classifier step defines K>=2 OvR detector sources."""
    if step_cfg.get("ovr_binary_pickles_from_comparisons"):
        return True
    p = step_cfg.get("ovr_binary_model_paths") or []
    d = step_cfg.get("ovr_detection_dirs") or []
    M = int(step_cfg.get("ovr_n_control_classes") or 0)
    bip = bool(step_cfg.get("ovr_bipartite_aggregate"))
    if bip and M >= 1 and len(d) >= M:
        return True
    return (
        len(p) >= 2
        or len(d) >= 2
        or (bool(step_cfg.get("ovr_pairwise_aggregate_control")) and len(d) >= 1)
    )


def expand_ovr_paths_from_comparisons(
    project: ProjectConfig,
    *,
    unified_basename: str,
    control_vs_rest_pkl: Optional[str] = None,
) -> Tuple[List[str], List[str], bool, bool]:
    """
    Build OvR **detection directory** paths from ``comparisons`` (and optional control override).

    Returns ``(dirs, class_names, pairwise_aggregate_control, ovr_bipartite_aggregate)``.

    **Single control (``pairwise_aggregate_control=True``):** one directory per **disease** —
    ``detections/<control>/<disease>/`` (**K-1** dirs for **K** names). Control head aggregates
    P(control) across those pairwises.

    **Multiple controls:** **bipartite** row-major order: for each control label, for each disease
    leaf, one directory (**M×N** dirs for **M+N** class names). Set ``ovr_bipartite_aggregate`` and
    ``ovr_n_control_classes=M`` in merged classifier config.

    **Explicit control directory** (``pairwise_aggregate_control=False``, single control only):
    ``ovr_control_vs_rest_pkl`` or ``detections/one_vs_rest/...`` adds a dedicated control dir
    (**K** dirs for **K** names).
    """
    if not project.uses_control_disease():
        raise ValueError(
            "ovr_binary_pickles_from_comparisons requires a project with controls, diseases, and comparisons"
        )
    if project.control is None:
        raise ValueError("project.control is required")
    resolved = project.get_resolved_groups()
    if len(resolved) < 2:
        raise ValueError("Project must have at least one control and one disease group")
    comparisons = project.get_comparisons()
    comp_by_pair = {(spec.control_group, spec.disease_group): spec for spec in comparisons}

    with_side = project._get_resolved_groups_with_side(expand_subclusters=False)
    control_labels = [lbl for lbl, _, side in with_side if side == "control"]
    disease_labels = [lbl for lbl, _, side in with_side if side == "disease"]
    names = control_labels + disease_labels
    M, N = len(control_labels), len(disease_labels)
    if M < 1 or N < 1:
        raise ValueError("OvR expansion requires at least one control and one disease group")

    def _verify_det_dir(ctrl: str, dis: str) -> str:
        det_dir = Path(project.get_detection_output_dir(ctrl, dis))
        candidate = det_dir / unified_basename
        if not candidate.is_file():
            raise FileNotFoundError(
                f"OvR: detector artifact not found at {candidate} "
                f"(comparison {ctrl!r} vs {dis!r}). "
                "Run MethylDetector or adjust ovr_unified_classifier_basename."
            )
        return str(det_dir)

    if M > 1:
        out_dirs: List[str] = []
        for c in control_labels:
            for d in disease_labels:
                if (c, d) not in comp_by_pair:
                    raise ValueError(
                        f"No comparison for control_group={c!r}, disease_group={d!r}; "
                        f"need full bipartite (have {len(comp_by_pair)} specs)"
                    )
                out_dirs.append(_verify_det_dir(c, d))
        return out_dirs, names, False, True

    # M == 1: legacy single-control path
    control_label = control_labels[0]
    comp_by_disease = {spec.disease_group: spec for spec in comparisons}
    disease_dirs: List[str] = []
    for label in disease_labels:
        if label not in comp_by_disease:
            raise ValueError(
                f"No comparison with disease_group={label!r} for OvR path; "
                f"check comparisons vs disease groups (have: {sorted(comp_by_disease.keys())})"
            )
        spec = comp_by_disease[label]
        disease_dirs.append(_verify_det_dir(spec.control_group, spec.disease_group))

    if control_vs_rest_pkl:
        p = Path(control_vs_rest_pkl).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"ovr_control_vs_rest_pkl not found: {p}")
        ctrl_dir = str(p.parent if p.is_file() else p)
        out_dirs = [ctrl_dir] + disease_dirs
        return out_dirs, names, False, False

    root = Path(project.get_project_root())
    dedicated = root / "detections" / "one_vs_rest" / control_label / unified_basename
    if dedicated.is_file():
        ctrl_dir = str(dedicated.parent)
        out_dirs = [ctrl_dir] + disease_dirs
        return out_dirs, names, False, False

    return disease_dirs, names, True, False


def _consume_ovr_comparison_options_and_maybe_expand(
    project: ProjectConfig, base: Dict[str, Any]
) -> None:
    """
    Pop resolver-only classifier keys and, if requested, set ovr_detection_dirs (+ names)
    from comparisons. Safe to call when base was built from resolve_classifier_config merge.
    """
    flag = bool(base.pop("ovr_binary_pickles_from_comparisons", False))
    basename = base.pop("ovr_unified_classifier_basename", None)
    if not basename:
        basename = default_ovr_unified_classifier_basename(project)
    else:
        basename = str(basename).strip() or default_ovr_unified_classifier_basename(project)
    ctrl_pkl = base.pop("ovr_control_vs_rest_pkl", None)
    if not flag:
        return
    dirs, default_names, agg_ctrl, bip = expand_ovr_paths_from_comparisons(
        project,
        unified_basename=str(basename),
        control_vs_rest_pkl=ctrl_pkl,
    )
    base["ovr_detection_dirs"] = dirs
    base["ovr_pairwise_aggregate_control"] = agg_ctrl
    base["ovr_bipartite_aggregate"] = bip
    if bip:
        ws = project._get_resolved_groups_with_side(expand_subclusters=False)
        base["ovr_n_control_classes"] = sum(1 for _, _, s in ws if s == "control")
    if not base.get("ovr_class_names"):
        base["ovr_class_names"] = default_names


def _disease_subdir(project: ProjectConfig) -> str:
    """Middle path segment for step dirs: <step>/<disease_label>/<disease_group>. Uses project.disease.label when set."""
    if project.disease is not None:
        return project.disease.label
    return "cancer"


def resolve_classifier_config_per_cancer_group(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    control_index: int = 0,
    disease_subdir: Optional[str] = None,
) -> List[Tuple[ClassificationConfig, str]]:
    """
    Build one ClassificationConfig per comparison.
    When project uses control/disease + comparisons: one entry per get_comparisons().
    Otherwise: one per non-control group (flat groups).

    Returns:
        List of (config, comparison_label) for each comparison.
    """
    project = load_project(project_path)
    step_cfg = dict(resolve_for_project("classifier", project))
    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path) as f:
                overrides = json.load(f)
            step_cfg = {**step_cfg, **overrides}

    val_cfg_mc = resolve_for_project("validation", project)
    if step_cfg.get("calibration_train_fraction") is None and val_cfg_mc.get("train_fraction") is not None:
        step_cfg["calibration_train_fraction"] = float(val_cfg_mc["train_fraction"])
    if step_cfg.get("calibration_seed") is None and val_cfg_mc.get("seed") is not None:
        step_cfg["calibration_seed"] = int(val_cfg_mc["seed"])

    disease_label = disease_subdir if disease_subdir is not None else _disease_subdir(project)

    if project.uses_control_disease():
        comparisons = project.get_comparisons()
        paths = project.get_derived_paths()
        out: List[Tuple[ClassificationConfig, str]] = []
        for spec in comparisons:
            comp_label = spec.comparison_label or spec.disease_group
            ctrl_label = spec.control_group
            dis_label = spec.disease_group
            model_dir = project.get_detection_output_dir(ctrl_label, dis_label)
            output_path = str(Path(project.get_classifier_output_dir(ctrl_label, dis_label)) / CLASSIFIER_OUTPUT_FILENAME)
            base: Dict[str, Any] = {
                "model_dir": model_dir,
                "model_path": None,
                "centroid1_dir": project.get_centroid_dir("control", ctrl_label),
                "centroid2_dir": project.get_centroid_dir("disease", dis_label),
                "output_path": output_path,
            }
            if project.path_remap:
                base["centroid_path_remap"] = project.path_remap
            for k, v in step_cfg.items():
                base[k] = v
            if not base.get("centroid_sample_root"):
                default_root = _default_centroid_sample_root(project)
                if default_root is not None:
                    base["centroid_sample_root"] = default_root
            if not base.get("save_classifier_path"):
                classifier_out_dir = Path(project.get_classifier_output_dir(ctrl_label, dis_label))
                base["save_classifier_path"] = str(
                    classifier_out_dir / f"{project.project_name}-classifier.pkl"
                )
            resolved_map = {lbl: paths for lbl, paths in project.get_resolved_groups()}
            if not base.get("centroid1_sample_paths"):
                ctrl_paths = list(resolved_map.get(ctrl_label, []))
                dis_paths = list(resolved_map.get(dis_label, []))
                if ctrl_paths and dis_paths:
                    base["centroid1_sample_paths"] = ctrl_paths
                    base["centroid2_sample_paths"] = dis_paths
            out.append((ClassificationConfig(**base), comp_label))
        return out

    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()
    if len(resolved) < 2:
        return []
    centroid_dirs = paths.centroid_dirs or [paths.centroid1_dir, paths.centroid2_dir]
    if len(centroid_dirs) != len(resolved):
        centroid_dirs = []
        for i, (label, _) in enumerate(resolved):
            side = "control" if i == control_index else "disease"
            centroid_dirs.append(project.get_centroid_dir(side, label))
    c1_dir = centroid_dirs[control_index]
    out = []
    for i in range(len(resolved)):
        if i == control_index:
            continue
        label = resolved[i][0]
        control_label = resolved[control_index][0]
        model_dir = project.get_detection_output_dir(control_label, label)
        output_path = str(Path(project.get_classifier_output_dir(control_label, label)) / CLASSIFIER_OUTPUT_FILENAME)
        base = {
            "model_dir": model_dir,
            "model_path": None,
            "centroid1_dir": c1_dir,
            "centroid2_dir": centroid_dirs[i],
            "output_path": output_path,
        }
        if project.path_remap:
            base["centroid_path_remap"] = project.path_remap
        for k, v in step_cfg.items():
            base[k] = v
        if not base.get("centroid_sample_root"):
            default_root = _default_centroid_sample_root(project)
            if default_root is not None:
                base["centroid_sample_root"] = default_root
        if not base.get("save_classifier_path"):
            classifier_out_dir = Path(project.get_classifier_output_dir(control_label, label))
            base["save_classifier_path"] = str(
                classifier_out_dir / f"{project.project_name}-classifier.pkl"
            )
        out.append((ClassificationConfig(**base), label))
    return out


def build_multiclass_config_from_project(
    project_path: Union[str, Path],
    dmps_csv: Optional[Union[str, Path]] = None,
    output_model: Optional[Union[str, Path]] = None,
    weights_column: Optional[str] = "weight",
    detection_dmps_glob: str = "dmps-*-classifier.csv",
    output_base_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a multiclass model config dict from a project (for use with build_multiclass_model).

    Uses the project's resolved groups (centroid_dirs + labels). If dmps_csv is not given,
    looks under the project's detection_dir for a DMP CSV (first file matching detection_dmps_glob).
    For N-class detection you typically run MethylDetector one-vs-rest or merge pairwise DMPs
    and place the merged CSV in detection_dir, or pass dmps_csv explicitly.
    When ``output_base_override`` is set, it is passed to ``load_project`` (same as MethylDetector
    ``--output-base``) so centroid and classifier paths resolve consistently.

    Returns:
        Config dict with keys: dmps_csv, output_model, weights_column, classes (list of {name, centroid_dir}).
    """
    project = load_project(
        project_path,
        output_base_override=output_base_override,
    )
    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()
    if len(resolved) < 2:
        raise ValueError("Project must have at least 2 groups for multiclass")
    centroid_dirs = paths.centroid_dirs or [paths.centroid1_dir, paths.centroid2_dir]
    if len(centroid_dirs) != len(resolved):
        centroid_dirs = []
        for i, (label, _) in enumerate(resolved):
            side = "control" if i == 0 else "disease"
            centroid_dirs.append(project.get_centroid_dir(side, label))

    if dmps_csv is None:
        det_dir = Path(paths.detection_dir)
        if det_dir.exists():
            picked = first_classifier_dmps_csv(det_dir)
            if picked is not None:
                dmps_csv = str(picked)
            else:
                candidates = sorted(det_dir.glob(detection_dmps_glob))
                if candidates:
                    dmps_csv = str(candidates[0])
                else:
                    dmps_csv = str(det_dir / "dmps-all-biological.csv")
        else:
            dmps_csv = str(Path(paths.detection_dir) / "dmps-all-biological.csv")
    else:
        dmps_csv = str(Path(dmps_csv).resolve())

    if output_model is None:
        output_model = str(Path(paths.classifier_dir) / "multiclass-classifier.pkl")
    else:
        output_model = str(Path(output_model).resolve())

    classes: List[Dict[str, str]] = []
    for (label, _), cdir in zip(resolved, centroid_dirs):
        classes.append({"name": label, "centroid_dir": str(Path(cdir).resolve())})

    control_label: Optional[str] = None
    comparison_labels: List[str] = []
    if getattr(project, "uses_control_disease", lambda: False)():
        with_side = project._get_resolved_groups_with_side(expand_subclusters=False)
        controls = [str(lbl) for lbl, _, side in with_side if side == "control"]
        diseases = [str(lbl) for lbl, _, side in with_side if side == "disease"]
        control_label = controls[0] if controls else None
        comparison_labels = diseases

    return {
        "dmps_csv": dmps_csv,
        "output_model": output_model,
        "weights_column": weights_column,
        "temperature": 1.0,
        "histogram_smoothing": 0.5,
        "weight_power": 1.0,
        "control_label": control_label,
        "comparison_labels": comparison_labels,
        "contexts": list(project.contexts or []),
        "chromosomes": [str(c) for c in (project.chromosomes or [])],
        "classes": classes,
        "project_path": str(Path(project_path).resolve()),
        "train_learned_multiclass": False,
    }


def resolve_classifier_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    output_filename: str = "classification_results.csv",
    resolved_config_path: Optional[Union[str, Path]] = None,
) -> ClassificationConfig:
    """
    Build ClassificationConfig from a project config and optional step overrides.

    Model directory is set to the project's detection_dir (where MethylDetector
    writes trained classifiers). Centroid dirs and path_remap come from the project;
    output goes to classifier_dir.
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()
    centroid_dirs = paths.centroid_dirs

    base = {
        "model_dir": paths.detection_dir,
        "centroid1_dir": paths.centroid1_dir,
        "centroid2_dir": paths.centroid2_dir,
        "output_path": str(Path(paths.classifier_dir) / output_filename),
    }
    if len(centroid_dirs) > 2:
        base["centroid_dirs"] = list(centroid_dirs)
        base["multiclass_class_names"] = [resolved[i][0] for i in range(len(resolved))]
    if project.path_remap:
        base["centroid_path_remap"] = project.path_remap

    # Apply project-level step config (classifier) if present
    from methyl_utils.cli_resolved_config import resolve_cli_step_config

    step_cfg = resolve_cli_step_config(
        "classifier",
        project,
        resolved_config_path=resolved_config_path,
        step_override_path=step_override_path,
    )
    if step_cfg:
        for k, v in step_cfg.items():
            base[k] = v

    # Default hierarchical panel from comparisons when step omits panel (dedupe vs predictor JSON)
    if not (isinstance(base.get("panel"), dict) and base.get("panel")):
        derive_fn = getattr(project, "derive_panel_spec_from_comparisons", None)
        derived_panel = derive_fn() if callable(derive_fn) else None
        if derived_panel is not None:
            base["panel"] = derived_panel

    # Default calibration holdout to MC validation train_fraction/seed when classifier omits them
    if resolved_config_path in (None, ""):
        val_cfg = resolve_for_project("validation", project)
        if base.get("calibration_train_fraction") is None and val_cfg.get("train_fraction") is not None:
            base["calibration_train_fraction"] = float(val_cfg["train_fraction"])
        if base.get("calibration_seed") is None and val_cfg.get("seed") is not None:
            base["calibration_seed"] = int(val_cfg["seed"])

    if not base.get("centroid_sample_root"):
        default_root = _default_centroid_sample_root(project)
        if default_root is not None:
            base["centroid_sample_root"] = default_root

    _consume_ovr_comparison_options_and_maybe_expand(project, base)

    # OvR assembly from detector outputs replaces a single shared detection_dir model.
    if base.get("ovr_binary_model_paths") or base.get("ovr_detection_dirs"):
        base["model_dir"] = None
        base["model_path"] = None

    # Default save path: multiclass OvR → classifiers/<control>/<bundle>.pkl; else classifiers root.
    if not base.get("save_classifier_path"):
        merged_cls = dict(step_cfg or {})
        for k in _CLASSIFIER_STEP_KEYS_FOR_BUNDLE_PATH:
            if k in base:
                merged_cls[k] = base[k]
        p_bundle = predicted_multiclass_ovr_bundle_path(project, merged_cls)
        ovr_paths = base.get("ovr_binary_model_paths") or []
        ovr_dirs = base.get("ovr_detection_dirs") or []
        agg_ovr = bool(base.get("ovr_pairwise_aggregate_control"))
        bip_ovr = bool(base.get("ovr_bipartite_aggregate"))
        if p_bundle is not None and (
            len(ovr_paths) >= 2
            or len(ovr_dirs) >= 2
            or (agg_ovr and len(ovr_dirs) >= 1)
            or (bip_ovr and len(ovr_dirs) >= 2)
        ):
            base["save_classifier_path"] = str(p_bundle)
        else:
            base["save_classifier_path"] = str(
                Path(paths.classifier_dir) / f"{project.project_name}-classifier.pkl"
            )

    base.pop("ovr_bundle_filename", None)

    _apply_training_cohort_to_classifier_base(project, base)

    return ClassificationConfig(**base)
