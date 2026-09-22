"""
Load train/validation partitions from existing Monte Carlo ``run_XXXX`` directories.

Used to reuse the same stratified splits as a prior stability / default MC run when evaluating
models or post-model metrics, avoiding redundant split RNG and ensuring aligned partitions.

CAAS bridge (universal idempotency Phase 3): split CSV content fingerprints are part of
``validation.plan_iterations`` / ``validation.model_mc`` input signatures (see
``methyl_worker.action_skip``). When those actions hit CAAS, the older ``[split-reuse]`` /
``Kept existing`` paths are redundant — the worker relinks products instead of re-planning.
``requireArtifactReuse`` remains a strict gate inside model-MC execution when set.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .project_gen import _safe_cohort_filename_label
from .split import load_and_resolve_sample_paths, stratified_split, stratified_split_multiclass


def _resolved_set(paths: List[str]) -> set[str]:
    return {str(Path(p).resolve()) for p in paths}


def _partition_matches_pool(train: List[str], val: List[str], pool: List[str]) -> bool:
    """Train and val must be non-empty, disjoint, and exactly partition ``pool``."""
    rt = _resolved_set(train)
    rv = _resolved_set(val)
    rp = _resolved_set(pool)
    if not rt or not rv:
        return False
    if rt & rv:
        return False
    return rt | rv == rp


def resolve_run_metadata_dir(run_dir: Path) -> Path:
    """Resolve split metadata from the run or its recorded planner CAAS provenance."""
    if (run_dir / "project.json").is_file():
        return run_dir

    run_id = run_dir.name
    study_root = run_dir.parent.parent
    action_logs = [
        run_dir / "action_run_log.jsonl",
        run_dir.parent / "action_run_log.jsonl",
    ]
    for action_log in action_logs:
        if not action_log.is_file():
            continue
        try:
            rows = action_log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(rows):
            try:
                output = json.loads(line).get("outputs") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            values = [output.get("manifest_path")]
            values.extend(
                artifact.get("path")
                for artifact in output.get("artifacts") or []
                if isinstance(artifact, dict)
            )
            for value in values:
                if not isinstance(value, str) or "/validation_plan_iterations/" not in value:
                    continue
                parts = Path(value).parts
                try:
                    marker = parts.index("validation_plan_iterations")
                    cache_key = parts[marker + 1]
                except (ValueError, IndexError):
                    continue
                candidate = (
                    study_root
                    / ".caas"
                    / "validation_plan_iterations"
                    / cache_key
                    / run_id
                )
                if (candidate / "project.json").is_file():
                    return candidate
    return run_dir


def try_load_binary_split_from_run_dir(
    run_dir: Path,
    control_paths: List[str],
    disease_paths: List[str],
    samples_base_path: str,
) -> Optional[Tuple[List[str], List[str], List[str], List[str]]]:
    """
    Load binary train/val lists from ``train_control.csv`` / ``train_disease.csv`` /
    ``val_control.csv`` / ``val_disease.csv`` under ``run_dir``.

    Returns None if files are missing or assignments are incompatible with the current cohort pools.
    """
    tc_csv = run_dir / "train_control.csv"
    td_csv = run_dir / "train_disease.csv"
    vc_csv = (
        run_dir / "test_control.csv"
        if (run_dir / "test_control.csv").is_file()
        else run_dir / "val_control.csv"
    )
    vd_csv = (
        run_dir / "test_disease.csv"
        if (run_dir / "test_disease.csv").is_file()
        else run_dir / "val_disease.csv"
    )
    if not all(p.is_file() for p in (tc_csv, td_csv, vc_csv, vd_csv)):
        return None

    train_control = load_and_resolve_sample_paths(tc_csv, samples_base_path)
    train_disease = load_and_resolve_sample_paths(td_csv, samples_base_path)
    val_control = load_and_resolve_sample_paths(vc_csv, samples_base_path)
    val_disease = load_and_resolve_sample_paths(vd_csv, samples_base_path)

    if not _partition_matches_pool(train_control, val_control, control_paths):
        return None
    if not _partition_matches_pool(train_disease, val_disease, disease_paths):
        return None

    return train_control, train_disease, val_control, val_disease


def try_load_multiclass_split_from_run_dir(
    run_dir: Path,
    cohort_paths_list: List[Tuple[str, List[str]]],
    cohort_labels: List[str],
    samples_base_path: str,
) -> Optional[Tuple[Dict[str, List[str]], Dict[str, List[str]]]]:
    """
    Load per-label train/val from ``training_<label>.csv`` / ``testing_<label>.csv`` (safe filenames).
    """
    pool_by_label = dict(cohort_paths_list)
    train_m: Dict[str, List[str]] = {}
    val_m: Dict[str, List[str]] = {}
    for lbl in cohort_labels:
        if lbl not in pool_by_label:
            return None
        safe = _safe_cohort_filename_label(lbl)
        tr = run_dir / f"training_{safe}.csv"
        canonical_test = run_dir / f"test_{safe}.csv"
        te = canonical_test if canonical_test.is_file() else run_dir / f"testing_{safe}.csv"
        if not tr.is_file() or not te.is_file():
            return None
        train_m[lbl] = load_and_resolve_sample_paths(tr, samples_base_path)
        val_m[lbl] = load_and_resolve_sample_paths(te, samples_base_path)
        if not _partition_matches_pool(train_m[lbl], val_m[lbl], pool_by_label[lbl]):
            return None
    return train_m, val_m


def resolve_iteration_split(
    *,
    layout: str,
    iteration_index: int,
    split_source_root: Path,
    cohort_paths_list: List[Tuple[str, List[str]]],
    cohort_labels: List[str],
    control_paths: List[str],
    disease_paths: List[str],
    train_fraction: float,
    seed_i: Optional[int],
    samples_base_path: str,
) -> Tuple[object, str]:
    """
    Try ``split_source_root/run_{iteration+1:04d}/`` first; fall back to stratified split.

    Returns:
        (split_payload, source_tag) where ``source_tag`` is ``\"reused\"`` or ``\"generated\"``.

    ``split_payload`` is a 4-tuple for binary layout, or ``(train_m, val_m)`` dict pair for multiclass.
    """
    run_dir = resolve_run_metadata_dir(
        split_source_root / f"run_{iteration_index + 1:04d}"
    )
    if layout == "binary":
        loaded = try_load_binary_split_from_run_dir(
            run_dir, control_paths, disease_paths, samples_base_path
        )
        if loaded is not None:
            return loaded, "reused"
        return (
            stratified_split(
                control_paths,
                disease_paths,
                train_fraction,
                seed=seed_i,
            ),
            "generated",
        )

    loaded_m = try_load_multiclass_split_from_run_dir(
        run_dir, cohort_paths_list, cohort_labels, samples_base_path
    )
    if loaded_m is not None:
        return loaded_m, "reused"
    return (
        stratified_split_multiclass(
            cohort_paths_list,
            train_fraction,
            seed=seed_i,
        ),
        "generated",
    )


def write_split_reuse_summary(path: Path, payload: Dict[str, object]) -> Path:
    """Write ``split_reuse_summary.json`` under the given directory (or exact path)."""
    out = path if path.suffix == ".json" else path / "split_reuse_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return out


def _hash_split_csv_part(path: Path) -> Optional[str]:
    """Return ``name:size:digest16`` for one split CSV, or None if unreadable."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        st = path.stat()
    except OSError:
        return None
    return f"{path.name}:{st.st_size}:{h.hexdigest()[:16]}"


def fingerprint_run_split_csvs(run_dir: Path) -> Optional[str]:
    """Content fingerprint of train/val(/test) split CSVs under a run metadata dir.

    Used by worker CAAS signatures so model-MC / plan_iterations keys change when
    reused partitions change — aligning split-reuse identity with content keys.
    """
    meta = resolve_run_metadata_dir(run_dir)
    binary_names = (
        "train_control.csv",
        "train_disease.csv",
        "val_control.csv",
        "val_disease.csv",
        "test_control.csv",
        "test_disease.csv",
    )
    parts: List[str] = []
    seen: set[str] = set()
    for name in binary_names:
        part = _hash_split_csv_part(meta / name)
        if part is None:
            continue
        parts.append(part)
        seen.add(name)
    # Multiclass training_*/test_* — skip binary names already covered above
    # (test_*.csv would otherwise re-hash test_control.csv / test_disease.csv).
    for path in sorted(meta.glob("training_*.csv")) + sorted(meta.glob("test_*.csv")):
        if path.name in seen:
            continue
        part = _hash_split_csv_part(path)
        if part is None:
            continue
        parts.append(part)
        seen.add(path.name)
    if not parts:
        return None
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def fingerprint_mc_splits_root(mc_root: Path, *, max_runs: int = 64) -> Optional[str]:
    """Roll up :func:`fingerprint_run_split_csvs` across ``run_*`` under ``mc_root``."""
    if not mc_root.is_dir():
        return None
    bits: List[str] = []
    for run_dir in sorted(p for p in mc_root.glob("run_*") if p.is_dir())[:max_runs]:
        fp = fingerprint_run_split_csvs(run_dir)
        if fp:
            bits.append(f"{run_dir.name}:{fp}")
    if not bits:
        return None
    return hashlib.sha256("\n".join(bits).encode("utf-8")).hexdigest()[:16]
