"""
Collect per-run metrics and optional stability after distributed discovery tasks.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from .config import MonteCarloConfig
from .modeling_modes import resolve_gene_stability_preferences
from .stability import run_stability_analysis
from .validator_metrics import (
    build_metrics_table,
    compute_resource_summary,
    compute_summary,
    iteration_scalar_metrics_from_run_dir,
    write_all_metrics_csv,
    write_metrics_distribution_plotly,
    write_resource_summary_json,
    write_step_timings_csv,
    write_summary_json,
)

_RUN_RE = re.compile(r"^run_(\d{4})$")


def _run_dirs_sorted(monte_carlo_runs_root: Path) -> List[Path]:
    out: List[Path] = []
    for p in sorted(monte_carlo_runs_root.iterdir()):
        if p.is_dir() and _RUN_RE.match(p.name):
            out.append(p)
    return out


def aggregate_discovery_monte_carlo(
    monte_carlo_runs_root: Path,
    config: MonteCarloConfig,
    *,
    run_stability: bool = False,
) -> None:
    """
    Rebuild ``all_metrics.csv`` / ``metrics_summary.json`` from successful runs, merge
    ``queue_local_step_timings.json`` into ``step_timings.csv``, and optionally run
    ``run_stability_analysis`` (same as legacy ``--stability``).
    """
    rows: List[Dict[str, Any]] = []
    all_step_timings: List[Dict[str, Any]] = []
    for run_dir in _run_dirs_sorted(monte_carlo_runs_root):
        st = run_dir / "queue_task_status.json"
        if st.is_file():
            try:
                meta = json.loads(st.read_text(encoding="utf-8"))
                if meta.get("status") not in (None, "completed"):
                    continue
            except Exception:
                pass
        scalar = iteration_scalar_metrics_from_run_dir(run_dir)
        if not scalar:
            continue
        run_id = run_dir.name
        rows.append(
            {
                "iteration": int(run_id.split("_", 1)[1]),
                "run_id": run_id,
                "run_dir": str(run_dir),
                **scalar,
            }
        )
        tpath = run_dir / "queue_local_step_timings.json"
        if tpath.is_file():
            try:
                arr = json.loads(tpath.read_text(encoding="utf-8"))
                if isinstance(arr, list):
                    for t in arr:
                        if isinstance(t, dict):
                            all_step_timings.append(t)
            except Exception:
                pass

    if not rows:
        print("No run directories with metrics to aggregate; nothing to write.", file=sys.stderr)
        sys.exit(1)

    if run_stability:
        from .stability import stability_dmp_panel_source_label

        dmp_source = stability_dmp_panel_source_label(
            prefer_classifier_panel_dmps=bool(config.stability_featurecuts_enabled),
            min_frequency=float(config.stability_dmp_freq),
        )
        print(f"\nRunning stability analysis on existing detector outputs (DMP source: {dmp_source})...")
        run_stability_analysis(
            monte_carlo_runs_root=monte_carlo_runs_root,
            dmp_min_freq=config.stability_dmp_freq,
            gene_min_freq=config.stability_gene_freq,
            min_balanced_accuracy=config.stability_min_balanced_accuracy,
            prefer_classifier_panel_dmps=bool(config.stability_featurecuts_enabled),
            **resolve_gene_stability_preferences(config.model_dump(mode="python")),
            dual_cutoff_enabled=bool(config.stability_dual_cutoff_enabled),
            relaxed_cutoff_mode=config.stability_relaxed_cutoff_mode,
            relaxed_multiplier=config.stability_relaxed_multiplier,
            score_eps=config.stability_score_eps,
            tiered_stability_enabled=bool(config.stability_tiers_enabled),
            tier_core_frequency=config.stability_tier_core_freq,
            tier_extended_frequency=config.stability_tier_extended_freq,
            tier_exploratory_frequency=config.stability_tier_exploratory_freq,
            default_freeze_tier=config.stability_default_freeze_tier,
        )
        print("Stability analysis complete.")

    # Sort by iteration
    def _k(r: Dict[str, Any]) -> int:
        try:
            return int(r.get("iteration") or 0)
        except (TypeError, ValueError):
            return 0

    rows = sorted(rows, key=_k)

    df = build_metrics_table(rows)
    all_metrics_csv = monte_carlo_runs_root / "all_metrics.csv"
    write_all_metrics_csv(df, all_metrics_csv)
    summary = compute_summary(df)
    summary_path = monte_carlo_runs_root / "metrics_summary.json"
    write_summary_json(summary, summary_path)
    print(f"Wrote {all_metrics_csv}")
    print(f"Wrote {summary_path}")

    if all_step_timings:
        st_path = monte_carlo_runs_root / "step_timings.csv"
        write_step_timings_csv(all_step_timings, st_path)
        print(f"Wrote {st_path}")
        resource_summary = compute_resource_summary(all_step_timings)
        if resource_summary:
            write_resource_summary_json(
                resource_summary, monte_carlo_runs_root / "resource_summary.json"
            )
    chart = monte_carlo_runs_root / "metrics_distributions_plotly.html"
    write_metrics_distribution_plotly(df, chart)
    print(f"Wrote {chart}")
