from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import json

import numpy as np

from methyl_predictor.project_resolver import resolve_predictor_config
from methyl_utils import load_project


@contextmanager
def _project_cwd(project_json: str | Path):
    pj = Path(project_json).resolve()
    prev = Path.cwd()
    try:
        os.chdir(pj.parent)
        yield
    finally:
        os.chdir(prev)


def resolve_eval_paths_and_labels(
    project_json: str | Path,
    class_names: List[str],
    predictor_cfg: Optional[Any] = None,
    project_loader: Optional[Any] = None,
    evaluation_partition: Optional[str] = None,
) -> Tuple[List[str], Optional[np.ndarray]]:
    """
    Resolve evaluation samples with unified precedence used by model backends.

    Precedence:
      1) test_group_paths
      2) holdout_group_paths
      3) train_group_paths
      4) binary holdout_control/disease (only for binary class_names)
      5) binary train_control/disease (only for binary class_names)
      6) binary test_control/disease (only for binary class_names)
      7) project.get_resolved_groups() fallback
    """
    partition = str(evaluation_partition or "").strip().lower()
    if partition == "test":
        # Prefer the logical project parent (production/) before following CAAS symlinks.
        logical = Path(project_json)
        resolved = logical.resolve()
        candidates = [
            logical.parent / "test_groups.json",
            logical.parent / "val_test_groups.json",
            resolved.parent / "test_groups.json",
            resolved.parent / "val_test_groups.json",
        ]
        manifest = next((p for p in candidates if p.is_file()), None)
        if manifest is None:
            raise FileNotFoundError(
                "Model-MC test evaluation requires test_groups.json under "
                f"{logical.parent} (or resolved {resolved.parent})"
            )
        project_path = resolved
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"Model-MC test manifest is empty or invalid: {manifest}")
        samples: List[str] = []
        y_true: List[int] = []
        for idx, entry in enumerate(payload):
            if not isinstance(entry, dict):
                raise ValueError(f"Invalid test group entry {idx} in {manifest}")
            cls_idx = int(entry.get("class_index", idx))
            for path in entry.get("paths") or []:
                samples.append(str(path))
                y_true.append(cls_idx)
        if not samples:
            raise ValueError(f"Model-MC test manifest contains no samples: {manifest}")
        loader = project_loader or load_project
        with _project_cwd(project_json):
            project = loader(project_json)
        train_paths = [
            str(path)
            for _label, paths in project.get_resolved_groups()
            for path in (paths or [])
        ]
        train_resolved = {str(Path(path).resolve()) for path in train_paths}
        test_resolved = {str(Path(path).resolve()) for path in samples}
        overlap = sorted(train_resolved & test_resolved)
        if overlap:
            raise ValueError(
                "Model-MC train/test partitions overlap; refusing to score test metrics. "
                f"First overlap(s): {overlap[:5]}"
            )
        return samples, np.asarray(y_true, dtype=np.int32)

    if partition == "train":
        loader = project_loader or load_project
        with _project_cwd(project_json):
            project = loader(project_json)
        samples = []
        y_true = []
        for cls_idx, (_label, paths) in enumerate(project.get_resolved_groups()):
            if cls_idx >= len(class_names):
                continue
            for path in paths:
                samples.append(str(path))
                y_true.append(cls_idx)
        if not samples:
            raise ValueError(f"Model-MC training partition contains no samples: {project_json}")
        return samples, np.asarray(y_true, dtype=np.int32)

    if predictor_cfg is None:
        predictor_cfg = resolve_predictor_config(project_json)
    samples: List[str] = []
    y_true: List[int] = []

    test_group_paths = getattr(predictor_cfg, "test_group_paths", None)
    holdout_group_paths = getattr(predictor_cfg, "holdout_group_paths", None)
    train_group_paths = getattr(predictor_cfg, "train_group_paths", None)
    test_control_paths = list(getattr(predictor_cfg, "test_control_paths", []) or [])
    test_disease_paths = list(getattr(predictor_cfg, "test_disease_paths", []) or [])
    holdout_control_paths = list(getattr(predictor_cfg, "holdout_control_paths", []) or [])
    holdout_disease_paths = list(getattr(predictor_cfg, "holdout_disease_paths", []) or [])
    train_control_paths = list(getattr(predictor_cfg, "train_control_paths", []) or [])
    train_disease_paths = list(getattr(predictor_cfg, "train_disease_paths", []) or [])

    if test_group_paths:
        for idx, entry in enumerate(test_group_paths):
            cls_idx = int(entry.get("class_index", idx))
            paths = [str(p) for p in (entry.get("paths") or [])]
            for p in paths:
                samples.append(p)
                y_true.append(cls_idx)
        return samples, np.asarray(y_true, dtype=np.int32)

    if holdout_group_paths:
        for idx, entry in enumerate(holdout_group_paths):
            cls_idx = int(entry.get("class_index", idx))
            paths = [str(p) for p in (entry.get("paths") or [])]
            for p in paths:
                samples.append(p)
                y_true.append(cls_idx)
        if samples:
            return samples, np.asarray(y_true, dtype=np.int32)

    if train_group_paths:
        for idx, entry in enumerate(train_group_paths):
            cls_idx = int(entry.get("class_index", idx))
            paths = [str(p) for p in (entry.get("paths") or [])]
            for p in paths:
                samples.append(p)
                y_true.append(cls_idx)
        if samples:
            return samples, np.asarray(y_true, dtype=np.int32)

    if (holdout_control_paths or holdout_disease_paths) and len(class_names) <= 2:
        samples = holdout_control_paths + holdout_disease_paths
        y_true = [0] * len(holdout_control_paths) + [1] * len(holdout_disease_paths)
        return samples, np.asarray(y_true, dtype=np.int32)

    if (train_control_paths or train_disease_paths) and len(class_names) <= 2:
        samples = train_control_paths + train_disease_paths
        y_true = [0] * len(train_control_paths) + [1] * len(train_disease_paths)
        return samples, np.asarray(y_true, dtype=np.int32)

    if (test_control_paths or test_disease_paths) and len(class_names) <= 2:
        samples = test_control_paths + test_disease_paths
        y_true = [0] * len(test_control_paths) + [1] * len(test_disease_paths)
        return samples, np.asarray(y_true, dtype=np.int32)

    loader = project_loader or load_project
    with _project_cwd(project_json):
        project = loader(project_json)
    for cls_idx, (_label, paths) in enumerate(project.get_resolved_groups()):
        if cls_idx >= len(class_names):
            continue
        for p in paths:
            samples.append(str(p))
            y_true.append(cls_idx)
    if samples:
        return samples, np.asarray(y_true, dtype=np.int32)
    return [], None


def _resolve_paths(paths: Sequence[str]) -> set[str]:
    return {str(Path(p).resolve()) for p in paths if str(p).strip()}


def _load_test_manifest_paths(project_json: str | Path) -> Optional[List[str]]:
    logical = Path(project_json)
    resolved = logical.resolve()
    candidates = [
        logical.parent / "test_groups.json",
        logical.parent / "val_test_groups.json",
        resolved.parent / "test_groups.json",
        resolved.parent / "val_test_groups.json",
    ]
    manifest = next((p for p in candidates if p.is_file()), None)
    if manifest is None:
        return None
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Model-MC test manifest is invalid: {manifest}")
    samples: List[str] = []
    for idx, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid test group entry {idx} in {manifest}")
        for path in entry.get("paths") or []:
            samples.append(str(path))
    return samples


def _load_train_sidecar_paths(project_json: str | Path) -> Optional[List[str]]:
    """Load train_control.csv + train_disease.csv when both exist beside the project."""
    from .split import load_and_resolve_sample_paths

    logical = Path(project_json)
    run_dir = logical.resolve().parent
    control_csv = run_dir / "train_control.csv"
    disease_csv = run_dir / "train_disease.csv"
    if not control_csv.is_file() or not disease_csv.is_file():
        # Prefer logical parent when project.json is a symlink into shared/.
        control_csv = logical.parent / "train_control.csv"
        disease_csv = logical.parent / "train_disease.csv"
    if not control_csv.is_file() or not disease_csv.is_file():
        return None
    samples_base = ""
    try:
        payload = json.loads(logical.read_text(encoding="utf-8"))
        samples_base = str(payload.get("samples_base_path") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        samples_base = ""
    return list(load_and_resolve_sample_paths(control_csv, samples_base)) + list(
        load_and_resolve_sample_paths(disease_csv, samples_base)
    )


def assert_model_mc_train_partition(
    project_json: str | Path,
    train_sample_paths: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """
    Fail-closed training membership for model-MC partitioned runs.

    When ``test_groups.json`` is present beside the project, training samples must be
    disjoint from the holdout and must match ``train_*.csv`` sidecars when those exist
    (otherwise they must match ``project.get_resolved_groups()``).
    """
    test_paths = _load_test_manifest_paths(project_json)
    if test_paths is None:
        return {"checked": 0, "n_train": 0, "n_test": 0}

    loader = load_project
    with _project_cwd(project_json):
        project = loader(project_json)
        project_train = [
            str(path)
            for _label, paths in project.get_resolved_groups()
            for path in (paths or [])
        ]
    if train_sample_paths is None:
        train_sample_paths = project_train
    train_resolved = _resolve_paths(train_sample_paths)
    project_train_resolved = _resolve_paths(project_train)
    if train_resolved != project_train_resolved:
        raise ValueError(
            "Model-MC training sample set does not match project train groups "
            f"(train={len(train_resolved)}, project={len(project_train_resolved)})."
        )

    sidecar_train = _load_train_sidecar_paths(project_json)
    if sidecar_train is not None:
        sidecar_resolved = _resolve_paths(sidecar_train)
        if train_resolved != sidecar_resolved:
            raise ValueError(
                "Model-MC training sample set does not match train_*.csv sidecars "
                f"(train={len(train_resolved)}, sidecars={len(sidecar_resolved)})."
            )

    test_resolved = _resolve_paths(test_paths)
    overlap = sorted(train_resolved & test_resolved)
    if overlap:
        raise ValueError(
            "Model-MC training samples overlap holdout test_groups.json; "
            f"refusing to fit. First overlap(s): {overlap[:5]}"
        )
    if not train_resolved:
        raise ValueError(f"Model-MC training partition is empty: {project_json}")
    if not test_resolved:
        raise ValueError(f"Model-MC test_groups.json contains no samples: {project_json}")
    return {
        "checked": 1,
        "n_train": int(len(train_resolved)),
        "n_test": int(len(test_resolved)),
    }
