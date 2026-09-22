"""
True held-out batch evaluation (Workflow 3).

Score a *frozen* production model on a designated hold-out partition (sample
batches that were never used for training or model selection) and report the
bootstrap distribution of QC metrics (balanced accuracy, sensitivity,
specificity, F1, ROC-AUC).

Unlike ``--predictor-only`` (Workflow 2), which draws random holdouts from the
development pool that overlap the training centroid, this workflow evaluates a
fixed, disjoint batch. Disjointness is enforced two ways:

1. ``validation_partitions`` already validates non-overlap of the hold-out role
   with ``development_train`` at config-parse time.
2. :func:`apply_holdout_exclusion_to_project_dict` filters the hold-out samples
   out of the production cohort list files at ``--freeze`` time, and
   :func:`run_holdout_evaluation` preflights that a ``holdout_manifest.json``
   recorded that exclusion before trusting the held-out claim.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .cohort_inference import infer_monte_carlo_cohorts_from_project
from .split import load_and_resolve_sample_paths

HOLDOUT_MANIFEST_NAME = "holdout_manifest.json"


def stratified_holdout_by_fraction(
    cohort_paths_by_label: Sequence[Tuple[str, Sequence[str]]],
    fraction: float,
    *,
    seed: Optional[int] = None,
    min_holdout_per_class: int = 1,
) -> Dict[str, Dict[str, List[str]]]:
    """
    Draw a per-class stratified hold-out: each class contributes ``round(fraction * n_class)``
    samples to the hold-out (at least ``min_holdout_per_class`` when the class is non-empty and
    ``fraction > 0``), leaving the remainder as the *active* pool for stability/freeze/model.

    Holding out 10% therefore removes 10% of each class independently, preserving class balance in
    both the hold-out and the active set (which stability further partitions, e.g. 80/20).

    Returns ``{label: {"holdout": [paths], "active": [paths]}}``.
    """
    if not (0.0 <= float(fraction) <= 1.0):
        raise ValueError(f"fraction must be in [0, 1]; got {fraction}")
    rng = random.Random(seed)
    out: Dict[str, Dict[str, List[str]]] = {}
    for label, paths in cohort_paths_by_label:
        items = [str(p) for p in paths]
        n = len(items)
        if n == 0:
            out[str(label)] = {"holdout": [], "active": []}
            continue
        shuffled = list(items)
        rng.shuffle(shuffled)
        n_hold = int(round(float(fraction) * n))
        if fraction > 0.0 and n_hold < min_holdout_per_class:
            n_hold = min(min_holdout_per_class, n)
        # Never leave the active pool empty when there is more than one sample.
        if n_hold >= n and n > 1:
            n_hold = n - 1
        holdout = sorted(shuffled[:n_hold])
        active = sorted(shuffled[n_hold:])
        out[str(label)] = {"holdout": holdout, "active": active}
    return out


def filter_cohort_paths_excluding(
    cohort_paths_list: Sequence[Tuple[str, Sequence[str]]],
    holdout_basenames: set[str],
) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """
    Remove hold-out samples (matched by basename) from each cohort's path list.

    Returns ``(filtered_cohort_paths_list, removed_basenames)``. Used so that ``--stability`` (and
    other training-time cohort assembly) operate on the *active* pool only, keeping the hold-out
    batch disjoint from feature selection as well as from the final freeze.
    """
    if not holdout_basenames:
        return [(str(lbl), [str(p) for p in paths]) for lbl, paths in cohort_paths_list], []
    filtered: List[Tuple[str, List[str]]] = []
    removed: set[str] = set()
    for label, paths in cohort_paths_list:
        kept: List[str] = []
        for p in paths:
            bn = _basename(p)
            if bn in holdout_basenames:
                removed.add(bn)
            else:
                kept.append(str(p))
        filtered.append((str(label), kept))
    return filtered, sorted(removed)


def holdout_basenames_from_config(config: Any) -> set[str]:
    """Basenames of the configured hold-out partition, or empty set when exclusion is off/unset."""
    if not bool(getattr(config, "holdout_exclude_from_training", True)):
        return set()
    partition = str(getattr(config, "holdout_partition", "locked_test"))
    partitions = getattr(config, "validation_partitions", None)
    paths = list(getattr(partitions, partition, []) or []) if partitions is not None else []
    return {_basename(p) for p in paths}


def _basename(p: str) -> str:
    return Path(str(p)).name


def _samples_base_path(project_dict: Dict[str, Any]) -> str:
    return str(project_dict.get("samples_base_path") or ".")


def _is_control_label(label: str) -> bool:
    """
    Heuristic control-side classification for the flat ``groups`` layout.

    Mirrors ``_heuristic_control_class_index`` in ``classification_metrics.py``:
    ``all`` matches only by exact equality (never as a substring), while
    ``healthy``/``control``/``normal`` match as substrings. This avoids the
    false positive where ``"all" in "small_cell"`` would misclassify a disease
    group (e.g. small-cell carcinoma) as control.
    """
    norm = str(label).strip().lower()
    if norm == "all":
        return True
    return any(tok in norm for tok in ("healthy", "control", "normal"))


def _control_disease_group_csvs(project_dict: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Return (control_groups, disease_groups) as [{label, csv}] using cohort inference sides."""
    controls = project_dict.get("controls") or project_dict.get("control") or {}
    diseases = project_dict.get("diseases") or project_dict.get("disease") or {}
    ctrl_dict = {"controls": controls} if controls else {}
    dis_dict = {"diseases": diseases} if diseases else {}
    control_groups = infer_monte_carlo_cohorts_from_project(ctrl_dict, Path(".")) if controls else []
    disease_groups = infer_monte_carlo_cohorts_from_project(dis_dict, Path(".")) if diseases else []
    # Flat "groups" layout: treat control-side by heuristic label match.
    if not control_groups and not disease_groups:
        flat = infer_monte_carlo_cohorts_from_project(project_dict, Path("."))
        for g in flat:
            if _is_control_label(g["label"]):
                control_groups.append(g)
            else:
                disease_groups.append(g)
    return control_groups, disease_groups


def _group_label_to_basenames(
    groups: Sequence[Dict[str, str]],
    base_path: str,
) -> Dict[str, set[str]]:
    out: Dict[str, set[str]] = {}
    for g in groups:
        csv_path = g.get("csv")
        if not csv_path:
            continue
        try:
            paths = load_and_resolve_sample_paths(csv_path, base_path)
        except FileNotFoundError:
            continue
        out[g["label"]] = {_basename(p) for p in paths}
    return out


def resolve_holdout_groups(
    project_dict: Dict[str, Any],
    holdout_paths: Sequence[str],
) -> Dict[str, Any]:
    """
    Classify hold-out sample paths into project class groups by basename membership.

    Returns a dict with:
      - ``binary``: bool (True when exactly one control side and one disease side apply),
      - ``control_paths`` / ``disease_paths``: absolute paths (binary layout),
      - ``groups_by_label``: {label: [abs_path]} (multiclass layout),
      - ``unresolved``: hold-out basenames not found in any project cohort.
    """
    base_path = _samples_base_path(project_dict)
    control_groups, disease_groups = _control_disease_group_csvs(project_dict)
    ctrl_members = _group_label_to_basenames(control_groups, base_path)
    dis_members = _group_label_to_basenames(disease_groups, base_path)

    control_basenames: set[str] = set().union(*ctrl_members.values()) if ctrl_members else set()
    label_by_basename: Dict[str, str] = {}
    for label, names in {**ctrl_members, **dis_members}.items():
        for nm in names:
            label_by_basename.setdefault(nm, label)

    control_paths: List[str] = []
    disease_paths: List[str] = []
    groups_by_label: Dict[str, List[str]] = {}
    unresolved: List[str] = []
    for raw in holdout_paths:
        abs_path = str(Path(raw)) if Path(raw).is_absolute() else str(Path(base_path).resolve() / raw)
        bn = _basename(raw)
        label = label_by_basename.get(bn)
        if label is None:
            unresolved.append(bn)
            continue
        groups_by_label.setdefault(label, []).append(abs_path)
        if bn in control_basenames:
            control_paths.append(abs_path)
        else:
            disease_paths.append(abs_path)

    n_control_groups = len({lbl for lbl in groups_by_label if lbl in ctrl_members})
    n_disease_groups = len({lbl for lbl in groups_by_label if lbl in dis_members})
    binary = (n_control_groups <= 1) and (n_disease_groups <= 1) and (len(groups_by_label) <= 2)

    return {
        "binary": binary,
        "control_paths": control_paths,
        "disease_paths": disease_paths,
        "groups_by_label": groups_by_label,
        "unresolved": unresolved,
    }


def apply_holdout_exclusion_to_project_dict(
    project_dict: Dict[str, Any],
    holdout_basenames: set[str],
    out_dir: Path,
) -> Tuple[List[str], Dict[str, Dict[str, str]]]:
    """
    Rewrite each cohort list file referenced by ``project_dict`` to drop hold-out
    samples, writing filtered CSVs under ``out_dir`` and repointing the project.

    Returns ``(removed_basenames, class_map)`` where ``class_map`` maps each removed
    basename to ``{"side": "control"|"disease", "label": <group label>}``. The class
    map is what lets :func:`run_holdout_evaluation` assign labels to hold-out samples
    later — the filtered cohort CSVs no longer contain them, so the labels must be
    captured here (Bug 2 fix).
    """
    if not holdout_basenames:
        return [], {}
    filtered_dir = Path(out_dir) / "holdout_filtered_cohorts"
    filtered_dir.mkdir(parents=True, exist_ok=True)
    removed: set[str] = set()
    class_map: Dict[str, Dict[str, str]] = {}

    def _filter_group(group: Dict[str, Any], scope: str, gid: str, *, side: str, label: str) -> None:
        paths = group.get("sample_paths")
        if not isinstance(paths, list) or not paths:
            return
        src_csv = Path(str(paths[0]))
        if not src_csv.is_file():
            return
        kept_rows: List[List[str]] = []
        header: Optional[List[str]] = None

        def _record(name: str) -> None:
            bn = _basename(name)
            removed.add(bn)
            class_map[bn] = {"side": side, "label": str(label)}

        with open(src_csv, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            first = next(reader, None)
            has_header = bool(
                first and first[0].strip().lower() in ("sample", "path", "sample_path", "name", "id")
            )
            if has_header:
                header = first
            elif first and first[0].strip():
                if _basename(first[0]) in holdout_basenames:
                    _record(first[0])
                else:
                    kept_rows.append(first)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                if _basename(row[0]) in holdout_basenames:
                    _record(row[0])
                else:
                    kept_rows.append(row)
        dst_csv = filtered_dir / f"{scope}_{gid}_{src_csv.name}"
        with open(dst_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if header is not None:
                writer.writerow(header)
            writer.writerows(kept_rows)
        group["sample_paths"] = [str(dst_csv)]

    def _walk_side(side_key: str, side: str) -> None:
        side_obj = project_dict.get(side_key)
        if not isinstance(side_obj, dict):
            return
        groups = side_obj.get("groups")
        if not isinstance(groups, list):
            return
        for gi, g in enumerate(groups):
            if not isinstance(g, dict):
                continue
            parent = str(g.get("label") or f"{side}_{gi}")
            stages = g.get("stages")
            if isinstance(stages, list) and stages:
                for si, st in enumerate(stages):
                    if isinstance(st, dict):
                        stage_label = str(st.get("label") or si)
                        _filter_group(st, side_key, f"{gi}_{si}", side=side, label=f"{parent}_{stage_label}")
            else:
                _filter_group(g, side_key, str(gi), side=side, label=parent)

    for side_key, side in (("controls", "control"), ("control", "control"), ("diseases", "disease"), ("disease", "disease")):
        _walk_side(side_key, side)
    flat_groups = project_dict.get("groups")
    if isinstance(flat_groups, list):
        for gi, g in enumerate(flat_groups):
            if isinstance(g, dict):
                label = str(g.get("label") or f"group_{gi}")
                side = "control" if _is_control_label(label) else "disease"
                _filter_group(g, "groups", str(gi), side=side, label=label)

    return sorted(removed), class_map


def write_holdout_manifest(
    out_dir: Path,
    *,
    partition: str,
    holdout_paths: Sequence[str],
    excluded: Sequence[str],
    class_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Record which samples were held out and excluded from production training."""
    manifest = {
        "partition": str(partition),
        "holdout_samples": sorted({_basename(p) for p in holdout_paths}),
        "excluded_from_training": sorted(set(excluded)),
        "n_holdout": len({_basename(p) for p in holdout_paths}),
        "n_excluded_from_training": len(set(excluded)),
        "holdout_class_map": dict(class_map or {}),
    }
    path = Path(out_dir) / HOLDOUT_MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path


def _write_paths_csv(path: Path, paths: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path"])
        for p in paths:
            w.writerow([str(Path(p).resolve() if Path(p).exists() else Path(p))])


def write_holdout_eval_artifacts(
    *,
    production_dir: Path,
    holdout_paths: Sequence[str],
    class_map: Dict[str, Dict[str, str]],
    samples_base_path: str = "",
    project_json: Optional[Path] = None,
    monte_carlo_runs_root: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Materialize locked-test eval sidecars for production model / post_model_validation.

    Writes ``test_groups.json``, ``test_control.csv``, ``test_disease.csv``, and
    ``val_*`` copies into:

    - ``production_dir``
    - the resolved parent of ``project_json`` when it differs (CAAS product dir)
    - ``monte_carlo_runs_root`` (post_model_validation binary cohort lookup)
    """
    prod_dir = Path(production_dir)
    prod_dir.mkdir(parents=True, exist_ok=True)
    base = str(samples_base_path or "")
    resolved = resolve_holdout_from_class_map(dict(class_map or {}), holdout_paths, base)
    if resolved.get("unresolved"):
        raise RuntimeError(
            "Cannot write holdout eval artifacts; unresolved holdout samples: "
            + ", ".join(resolved["unresolved"][:10])
        )

    groups_by_label: Dict[str, List[str]] = dict(resolved.get("groups_by_label") or {})
    control_paths = list(resolved.get("control_paths") or [])
    disease_paths = list(resolved.get("disease_paths") or [])

    # Prefer stable binary label order: control then disease.
    order: List[str] = []
    for paths, fallback_side in ((control_paths, "control"), (disease_paths, "disease")):
        if not paths:
            continue
        label = None
        for p in paths:
            info = (class_map or {}).get(_basename(p)) or {}
            label = str(info.get("label") or "")
            if label:
                break
        if not label:
            label = "all" if fallback_side == "control" else "disease"
        if label not in order:
            order.append(label)
    for lab in groups_by_label:
        if lab not in order:
            order.append(lab)

    test_groups = [
        {
            "label": lab,
            "paths": [str(Path(p).resolve() if Path(p).exists() else Path(p)) for p in groups_by_label.get(lab, [])],
        }
        for lab in order
        if groups_by_label.get(lab)
    ]

    dest_dirs: List[Path] = [prod_dir]
    if project_json is not None:
        pj = Path(project_json)
        logical_parent = pj.parent
        if logical_parent not in dest_dirs:
            dest_dirs.append(logical_parent)
        try:
            resolved_parent = pj.resolve().parent
        except OSError:
            resolved_parent = logical_parent
        if resolved_parent not in dest_dirs:
            dest_dirs.append(resolved_parent)
    if monte_carlo_runs_root is not None:
        mc_root = Path(monte_carlo_runs_root)
        if mc_root not in dest_dirs:
            dest_dirs.append(mc_root)

    written: Dict[str, Path] = {}
    for dest in dest_dirs:
        dest.mkdir(parents=True, exist_ok=True)
        tg = dest / "test_groups.json"
        tg.write_text(json.dumps(test_groups, indent=2) + "\n", encoding="utf-8")
        ctrl = dest / "test_control.csv"
        dis = dest / "test_disease.csv"
        _write_paths_csv(ctrl, control_paths)
        _write_paths_csv(dis, disease_paths)
        written[str(dest)] = tg
    return written


def apply_config_holdout_to_project(
    project_dict: Dict[str, Any],
    config: Any,
    prod_dir: Path,
    *,
    monte_carlo_runs_root: Optional[Path] = None,
    project_json: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Exclude ``holdout_partition`` samples from production cohorts and emit eval sidecars.

    Returns holdout metadata when exclusion ran; ``None`` when holdout is disabled/empty.
    """
    if config is None:
        return None
    if not bool(getattr(config, "holdout_exclude_from_training", True)):
        return None
    holdout_partition = str(getattr(config, "holdout_partition", "locked_test") or "locked_test")
    partitions = getattr(config, "validation_partitions", None)
    holdout_paths = (
        list(getattr(partitions, holdout_partition, []) or []) if partitions is not None else []
    )
    if not holdout_paths:
        return None

    holdout_basenames = {_basename(p) for p in holdout_paths}
    excluded, holdout_class_map = apply_holdout_exclusion_to_project_dict(
        project_dict, holdout_basenames, prod_dir
    )
    write_holdout_manifest(
        prod_dir,
        partition=holdout_partition,
        holdout_paths=holdout_paths,
        excluded=excluded,
        class_map=holdout_class_map,
    )
    samples_base = str(project_dict.get("samples_base_path") or getattr(config, "samples_base_path", "") or "")
    write_holdout_eval_artifacts(
        production_dir=prod_dir,
        holdout_paths=holdout_paths,
        class_map=holdout_class_map,
        samples_base_path=samples_base,
        project_json=project_json or (prod_dir / "project.json"),
        monte_carlo_runs_root=monte_carlo_runs_root,
    )
    return {
        "partition": holdout_partition,
        "holdout_paths": list(holdout_paths),
        "excluded": list(excluded),
        "class_map": dict(holdout_class_map),
    }


def resolve_holdout_from_class_map(
    class_map: Dict[str, Dict[str, str]],
    holdout_paths: Sequence[str],
    base_path: str,
) -> Dict[str, Any]:
    """
    Build the same structure as :func:`resolve_holdout_groups`, but from the freeze-time
    class map instead of the (now-filtered) cohort CSVs.

    This is the Bug 2 fix: after ``--freeze`` excludes hold-out samples from the cohort
    list files, their class labels can no longer be recovered from those files, so they
    are read from ``holdout_class_map`` recorded in the manifest.
    """
    control_paths: List[str] = []
    disease_paths: List[str] = []
    groups_by_label: Dict[str, List[str]] = {}
    unresolved: List[str] = []
    control_labels: set[str] = set()
    disease_labels: set[str] = set()
    for raw in holdout_paths:
        abs_path = str(Path(raw)) if Path(raw).is_absolute() else str(Path(base_path).resolve() / raw)
        bn = _basename(raw)
        info = class_map.get(bn)
        if not info:
            unresolved.append(bn)
            continue
        side = str(info.get("side") or "").strip().lower()
        label = str(info.get("label") or (side or bn))
        groups_by_label.setdefault(label, []).append(abs_path)
        if side == "control":
            control_paths.append(abs_path)
            control_labels.add(label)
        else:
            disease_paths.append(abs_path)
            disease_labels.add(label)
    binary = (len(control_labels) <= 1) and (len(disease_labels) <= 1) and (len(groups_by_label) <= 2)
    return {
        "binary": binary,
        "control_paths": control_paths,
        "disease_paths": disease_paths,
        "groups_by_label": groups_by_label,
        "unresolved": unresolved,
    }


def read_predictions_for_bootstrap(predictions_csv: Path) -> Dict[str, Any]:
    """
    Read a MethylPredictor ``predictions.csv`` into arrays for the bootstrap.

    Requires ``expected_class`` (true labels) and ``prediction`` columns; probability
    columns ``prob_class{i}`` are used for AUC when present.
    """
    import pandas as pd

    df = pd.read_csv(predictions_csv)
    if "expected_class" not in df.columns or "prediction" not in df.columns:
        raise ValueError(
            f"{predictions_csv} must contain 'expected_class' and 'prediction' columns for labeled hold-out evaluation."
        )
    y_true = df["expected_class"].to_numpy(dtype=int)
    y_pred = df["prediction"].to_numpy(dtype=int)
    prob_cols = [c for c in df.columns if c.startswith("prob_class")]

    def _col_index(c: str) -> int:
        try:
            return int(c.replace("prob_class", ""))
        except ValueError:
            return 1_000_000

    prob_cols = sorted(prob_cols, key=_col_index)
    y_proba = df[prob_cols].to_numpy(dtype=float) if prob_cols else None
    return {"y_true": y_true, "y_pred": y_pred, "y_proba": y_proba, "n_rows": int(len(df))}


def load_class_roles_from_metrics(metrics_json: Path) -> Dict[str, Any]:
    """Read control/disease class indices + class names from validation_metrics.json when available."""
    try:
        with open(metrics_json, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    roles = data.get("class_roles") or {}
    return {
        "control_index": int(roles.get("control_class_index", 0)) if roles else 0,
        "disease_indices": [int(i) for i in roles.get("disease_class_indices", [])] if roles else [],
        "n_classes": int(data.get("n_classes", 0)) or None,
        "class_names": data.get("class_names"),
    }


def _preflight_exclusion(
    manifest_path: Path,
    holdout_basenames: set[str],
) -> Tuple[bool, str]:
    """Verify the frozen model recorded exclusion of all current hold-out samples."""
    if not manifest_path.is_file():
        return False, (
            f"No {HOLDOUT_MANIFEST_NAME} next to the frozen project ({manifest_path}). "
            "Re-run '--freeze' after populating validation_partitions so hold-out samples are "
            "excluded from production training, or set holdout_exclude_from_training=false to "
            "evaluate without the disjointness guarantee."
        )
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Could not read {manifest_path}: {exc}"
    excluded = {str(x) for x in manifest.get("excluded_from_training", [])}
    missing = sorted(holdout_basenames - excluded)
    if missing:
        preview = missing[:8]
        more = f" (+{len(missing) - len(preview)} more)" if len(missing) > len(preview) else ""
        return False, (
            "Hold-out samples were NOT excluded from the frozen model's training set: "
            f"{preview}{more}. Re-run '--freeze' with these samples in the "
            "validation_partitions hold-out role."
        )
    return True, "ok"


def run_holdout_evaluation(
    config: Any,
    frozen_project_path: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """
    End-to-end held-out batch evaluation against a frozen model.

    Steps: resolve hold-out partition by class -> (preflight disjointness) -> run
    methyl-predictor once on the frozen model -> bootstrap QC-metric distributions
    -> write artifacts under ``output_dir``.
    """
    from .holdout_bootstrap import bootstrap_holdout_metrics
    from .pipeline_runner import run_predictor, run_predictor_multiclass
    from .project_gen import write_val_csv
    from .validator_metrics import write_metrics_distribution_plotly

    frozen_project_path = Path(frozen_project_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(frozen_project_path, encoding="utf-8") as f:
        project_dict = json.load(f)

    partition = str(getattr(config, "holdout_partition", "locked_test"))
    partitions = getattr(config, "validation_partitions", None)
    holdout_paths: List[str] = list(getattr(partitions, partition, []) or []) if partitions is not None else []
    if not holdout_paths:
        raise ValueError(
            f"No hold-out samples in validation_partitions.{partition}. Populate that partition "
            "in the profile/project actionConfig.validation before running --holdout-eval."
        )
    holdout_basenames = {_basename(p) for p in holdout_paths}

    if bool(getattr(config, "holdout_exclude_from_training", True)):
        manifest_path = frozen_project_path.parent / HOLDOUT_MANIFEST_NAME
        ok, msg = _preflight_exclusion(manifest_path, holdout_basenames)
        if not ok:
            raise ValueError(msg)
        # Bug 2 fix: the frozen project's cohort CSVs had the hold-out samples removed,
        # so their class labels are recovered from the manifest class map, not the CSVs.
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        class_map = manifest.get("holdout_class_map") or {}
        if not class_map:
            raise ValueError(
                f"{manifest_path} has no holdout_class_map (older freeze). Re-run '--freeze' to "
                "regenerate the manifest so hold-out class labels can be resolved."
            )
        resolved = resolve_holdout_from_class_map(
            class_map, holdout_paths, _samples_base_path(project_dict)
        )
    else:
        # Exclusion disabled: cohorts still contain the hold-out samples, so labels can
        # be resolved directly from the (unfiltered) project cohort list files.
        resolved = resolve_holdout_groups(project_dict, holdout_paths)

    if resolved["unresolved"]:
        preview = resolved["unresolved"][:8]
        raise ValueError(
            "Hold-out samples could not be assigned a class label: "
            f"{preview}. Ensure they were in a labeled control/disease cohort when '--freeze' ran."
        )

    pred_out = output_dir / "predictor"
    pred_out.mkdir(parents=True, exist_ok=True)

    if resolved["binary"]:
        ctrl_csv = output_dir / "holdout_control.csv"
        dis_csv = output_dir / "holdout_disease.csv"
        write_val_csv(ctrl_csv, resolved["control_paths"])
        write_val_csv(dis_csv, resolved["disease_paths"])
        rc, out, err = run_predictor(frozen_project_path, ctrl_csv, dis_csv, pred_out)
    else:
        groups = [{"label": lbl, "paths": paths} for lbl, paths in resolved["groups_by_label"].items()]
        test_groups_json = output_dir / "holdout_test_groups.json"
        with open(test_groups_json, "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2)
        rc, out, err = run_predictor_multiclass(frozen_project_path, test_groups_json, pred_out)

    if rc != 0:
        (output_dir / "predictor_stderr.log").write_text(err or "", encoding="utf-8")
        raise RuntimeError(f"methyl-predictor failed on hold-out batch (exit {rc}). See {output_dir/'predictor_stderr.log'}")

    predictions_csv = pred_out / "predictions.csv"
    if not predictions_csv.is_file():
        raise RuntimeError(f"Predictor did not produce {predictions_csv}")
    arrays = read_predictions_for_bootstrap(predictions_csv)
    roles = load_class_roles_from_metrics(pred_out / "validation_metrics.json")

    n_classes = roles.get("n_classes")
    control_index = int(roles.get("control_index", 0))
    disease_indices = roles.get("disease_indices") or None

    result = bootstrap_holdout_metrics(
        arrays["y_true"],
        arrays["y_pred"],
        arrays["y_proba"],
        n_classes=n_classes,
        control_index=control_index,
        disease_indices=disease_indices,
        n_bootstrap=int(getattr(config, "holdout_n_bootstrap", 1000)),
        ci=float(getattr(config, "holdout_ci", 0.95)),
        seed=getattr(config, "holdout_seed", None),
        stratified=bool(getattr(config, "holdout_stratified", True)),
    )
    result["partition"] = partition
    result["frozen_project"] = str(frozen_project_path)
    result["predictions_csv"] = str(predictions_csv)
    result["class_names"] = roles.get("class_names")

    bootstrap_json = output_dir / "holdout_metrics_bootstrap.json"
    serializable = {k: v for k, v in result.items() if k != "samples"}
    with open(bootstrap_json, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)

    import pandas as pd

    dist_df = pd.DataFrame(result["samples"])
    dist_csv = output_dir / "holdout_metrics_distribution.csv"
    dist_df.to_csv(dist_csv, index=False)
    try:
        write_metrics_distribution_plotly(dist_df, output_dir / "holdout_metrics_distribution.html")
    except Exception:
        pass

    result["outputs"] = {
        "bootstrap_json": str(bootstrap_json),
        "distribution_csv": str(dist_csv),
        "predictor_dir": str(pred_out),
    }
    return result
