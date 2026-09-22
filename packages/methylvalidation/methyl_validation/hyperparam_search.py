"""
Outer loop: small grid (or list) of MonteCarloConfig overrides, run ``methyl-validation`` per
candidate, score with :func:`optimization.objective_from_monte_carlo_artifacts`.

DEPRECATED for production. This host subprocess driver is retained for development
and experimentation only. The production path expands a grid into N workflow
instances via the portal / ``methyl-study-start hyperparam-grid-start`` (see
``workflow_engine/ops/hyperparam_grid.py`` and
``docs/architecture/portal-remote-control.md``). Both paths share ``grid_to_dicts``
and the same objective J.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from methyl_utils import load_project

from .config import MonteCarloConfig
from .optimization import ConstraintSet, ObjectiveWeights, objective_from_monte_carlo_artifacts


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        o = json.load(f)
    if not isinstance(o, dict):
        raise ValueError(f"expected object in {path}")
    return o


def merge_monte_carlo_dict(base: Dict[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in overrides.items():
        if v is None and k in out:
            continue
        out[k] = v
    return out


def build_config_from_base(
    base_path: Path,
    overrides: Mapping[str, Any],
) -> MonteCarloConfig:
    data = _load_json(base_path)
    if (
        "cohorts" in data
        and data.get("base_project")
        and "train_fraction" in data
        and "n_iterations" in data
    ):
        c = MonteCarloConfig.model_validate(merge_monte_carlo_dict(data, dict(overrides)))
        return c
    if "step_config" in data and "validation" in (data.get("step_config") or {}):
        from argparse import Namespace

        from .mc_config_load import load_monte_carlo_config

        ns = Namespace(project=base_path, config=None)
        c, _ = load_monte_carlo_config(ns, None)
        merged = merge_monte_carlo_dict(c.model_dump(), dict(overrides))
        return MonteCarloConfig.model_validate(merged)
    raise ValueError(
        f"Expected a dedicated monte carlo config JSON (cohorts, n_iterations) "
        f"or a project with step_config.validation: {base_path}"
    )


def grid_to_dicts(grid: Mapping[str, Sequence[Any]]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    vals = [list(grid[k]) for k in keys]
    rows: List[Dict[str, Any]] = []
    for combo in itertools.product(*vals):
        rows.append({keys[i]: combo[i] for i in range(len(keys))})
    return rows


def monte_carlo_runs_dir_for_config(config: MonteCarloConfig) -> Path:
    bp = Path(config.base_project)
    p = load_project(str(bp))
    return Path(config.output_base) / p.project_name / "monte_carlo_runs"


def run_methyl_validation(
    config_path: Path,
    *,
    extra_args: Optional[List[str]] = None,
) -> int:
    import shutil

    m = shutil.which("methyl-validation")
    if m:
        cmd = [m, "--config", str(config_path)]
    else:
        cmd = [sys.executable, "-m", "methyl_validation.cli", "--config", str(config_path)]
    if extra_args:
        cmd.extend(extra_args)
    return int(subprocess.call(cmd, env={**os.environ, "METHYL_HYPERPARAM": "1"}))


def run_search(
    *,
    base_config_path: Path,
    grid: Mapping[str, Sequence[Any]],
    work_dir: Path,
    weights: ObjectiveWeights,
    constraints: Optional[ConstraintSet] = None,
    extra_methyl_validation_args: Optional[List[str]] = None,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """
    For each grid point, write ``work_dir/trial_####/mc_config.json`` with merged config,
    ``output_base=work_dir/trial_####/out``, run ``python -m methyl_validation.cli --config ...``,
    then compute objective on ``out/<project>/monte_carlo_runs``.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for idx, ovr in enumerate(grid_to_dicts(grid)):
        trial = work_dir / f"trial_{idx:04d}"
        out_base = trial / "out"
        ovr = dict(ovr)
        ovr["output_base"] = str(out_base)
        cfg = build_config_from_base(base_config_path, ovr)
        tdir = trial
        tdir.mkdir(parents=True, exist_ok=True)
        cfg_path = tdir / "mc_config.json"
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(cfg.dump_clean_json(indent=2))
        mcr = monte_carlo_runs_dir_for_config(cfg)
        rec: Dict[str, Any] = {
            "trial": idx,
            "overrides": ovr,
            "config_path": str(cfg_path),
            "monte_carlo_runs": str(mcr),
        }
        if not dry_run:
            rc = run_methyl_validation(
                cfg_path, extra_args=extra_methyl_validation_args or ["--stability"]
            )
            rec["returncode"] = rc
            if rc != 0:
                rec["objective"] = None
                rec["feasible"] = False
                rec["reason"] = "methyl_validation_failed"
            else:
                ores = objective_from_monte_carlo_artifacts(mcr, weights, constraints)
                rec["objective"] = ores.value
                rec["feasible"] = ores.feasible
                rec["reason"] = ores.reason
                rec["result"] = ores.to_json_friendly()
                rec["details"] = ores.details
        else:
            rec["dry_run"] = True
        results.append(rec)
    feas = [r for r in results if r.get("feasible") is True]
    best: Optional[Dict[str, Any]] = None
    if feas:
        best = max(feas, key=lambda r: r.get("objective", float("-inf")))
    with open(work_dir / "search_summary.json", "w", encoding="utf-8") as f:
        json.dump({"n_trials": len(results), "best": best, "all": results}, f, indent=2)
    return results


def main() -> None:
    p = argparse.ArgumentParser(
        description="Grid search over MonteCarloConfig fields using methyl-validation subprocess."
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Dedicated monte carlo config JSON (cohorts, n_iterations, ...).",
    )
    p.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Project with step_config.validation (same as methyl-validation --project).",
    )
    p.add_argument("--work-dir", type=Path, required=True, help="Output root for trials (trial_0000/...)")
    p.add_argument(
        "--grid",
        type=str,
        required=True,
        help='JSON object, e.g. {"stability_dmp_freq": [0.6,0.7], "stability_min_balanced_accuracy": [null,0.5]}',
    )
    p.add_argument(
        "--weights-json",
        type=Path,
        default=None,
        help="Optional JSON for ObjectiveWeights (default: w_balanced_accuracy=1, w_macro_f1=1, stat=median).",
    )
    p.add_argument(
        "--baseline-summary",
        type=Path,
        default=None,
        help="If set, enforce rollout-style constraints against this metrics_summary.json.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Write configs and summary paths only, do not run pipeline.",
    )
    p.epilog = (
        "Extra methyl-validation flags may follow the search options (optional leading "
        "-- is ignored), e.g. `--stability` for a full search, or `--stability "
        "--resume 1 --skip-centroid` to reuse existing centroids and continue "
        "detector->mapper->gene-select. NOTE: --skip-detection only recomputes "
        "stability frequency from existing detections; it does NOT re-run mapper/"
        "gene-select, so it cannot recover trials that failed at those steps."
    )
    # Unknown options (e.g. --stability) are forwarded to methyl-validation.
    # parse_args()+REMAINDER rejects those unless a bare "--" precedes them.
    args, unknown = p.parse_known_args()
    if (args.project is None) == (args.config is None):
        p.error("Provide exactly one of --config or --project")
    if args.project and args.config:
        p.error("Use only one of --config or --project")
    base: Path = (args.project or args.config)  # type: ignore[assignment]
    grid = json.loads(args.grid)
    if not isinstance(grid, dict) or not all(isinstance(x, list) for x in grid.values()):
        p.error("--grid must be a JSON object of lists")
    w = _load_json(args.weights_json) if args.weights_json and args.weights_json.is_file() else {}
    weights = ObjectiveWeights.model_validate(w) if w else ObjectiveWeights()
    cset: Optional[ConstraintSet] = None
    if args.baseline_summary is not None:
        cset = ConstraintSet(baseline_metrics_summary_path=args.baseline_summary)
    extra = list(unknown or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    if not extra and not args.dry_run:
        extra = ["--stability"]
    run_search(
        base_config_path=base,
        grid=grid,
        work_dir=args.work_dir,
        weights=weights,
        constraints=cset,
        extra_methyl_validation_args=extra if extra else None,
        dry_run=args.dry_run,
    )
    print(f"Wrote {args.work_dir / 'search_summary.json'}")


if __name__ == "__main__":
    main()
