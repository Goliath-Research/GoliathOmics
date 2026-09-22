"""
Infer Monte Carlo cohort CSVs from a pipeline project JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def infer_monte_carlo_cohorts_from_project(
    project_data: Dict[str, Any],
    project_path: Path,
) -> List[Dict[str, str]]:
    """
    Build MC cohorts from a project JSON using resolved leaf labels.

    For control/disease projects this yields:
      - control group labels (e.g. all)
      - disease leaf labels (e.g. pca_pca1, pca_pca2, ...)
    For flat groups it yields group labels as-is.
    """
    def _norm_csv_path(p: str) -> str:
        pp = Path(str(p))
        return str(pp) if pp.is_absolute() else str(p)

    cohorts: List[Dict[str, str]] = []

    groups = project_data.get("groups")
    if isinstance(groups, list) and groups:
        for g in groups:
            if not isinstance(g, dict):
                continue
            label = str(g.get("label") or "").strip()
            paths = g.get("sample_paths") or []
            if label and isinstance(paths, list) and len(paths) > 0:
                cohorts.append({"label": label, "csv": _norm_csv_path(str(paths[0]))})
        return cohorts

    controls = project_data.get("controls") or project_data.get("control") or {}
    diseases = project_data.get("diseases") or project_data.get("disease") or {}

    ctrl_groups = controls.get("groups") if isinstance(controls, dict) else None
    if isinstance(ctrl_groups, list):
        for g in ctrl_groups:
            if not isinstance(g, dict):
                continue
            label = str(g.get("label") or "").strip()
            paths = g.get("sample_paths") or []
            if label and isinstance(paths, list) and len(paths) > 0:
                cohorts.append({"label": label, "csv": _norm_csv_path(str(paths[0]))})

    dis_groups = diseases.get("groups") if isinstance(diseases, dict) else None
    if isinstance(dis_groups, list):
        for g in dis_groups:
            if not isinstance(g, dict):
                continue
            parent = str(g.get("label") or "").strip()
            stages = g.get("stages")
            if isinstance(stages, list) and stages:
                for st in stages:
                    if not isinstance(st, dict):
                        continue
                    stage_label = str(st.get("label") or "").strip()
                    paths = st.get("sample_paths") or []
                    if parent and stage_label and isinstance(paths, list) and len(paths) > 0:
                        cohorts.append(
                            {"label": f"{parent}_{stage_label}", "csv": _norm_csv_path(str(paths[0]))}
                        )
            else:
                label = parent
                paths = g.get("sample_paths") or []
                if label and isinstance(paths, list) and len(paths) > 0:
                    cohorts.append({"label": label, "csv": _norm_csv_path(str(paths[0]))})

    return cohorts
