"""
Resolve MethylMapper paths from a project config.

The canonical detector layout is comparison-based:

    detections/<control_group>/<disease_group>/

This resolver mirrors that contract for both explicit control/disease projects
and legacy flat-group projects, so mapper outputs land under the matching
comparison directory:

    mapper/<control_group>/<disease_group>/

By default the mapper consumes the detector's discovery CSV exports
(`dmps-*-discovery.csv`), which match ``detection_mode=discovery_only`` and the
legacy dual-export discovery stage. Callers may override the filename pattern via
step config or a step-override JSON (e.g. selected / classifier / stable panels).
"""

from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

# Canonical detector exports: discovery / selected / classifier(+extended).
# Historical ``dmps-*-biological-sorted.csv`` is no longer written by methyl-detector.
DMP_CSV_PATTERN_DISCOVERY = "dmps-*-discovery.csv"
# Backward-compatible alias (tests / older callers); same as discovery default.
DMP_CSV_PATTERN_BIOLOGICAL = DMP_CSV_PATTERN_DISCOVERY


def _resolve_detection_dir_with_case_fallback(project, control_group: str, disease_group: str) -> Path:
    """Backward-compatible wrapper around ProjectConfig.resolve_detection_output_dir."""
    return Path(project.resolve_detection_output_dir(control_group, disease_group))


class MapperStepPaths(BaseModel):
    """Paths for the mapper step derived from a project (and optional overrides)."""

    csv_pattern: str = Field(
        ...,
        description="Glob pattern for detector CSVs, typically under detections/<control>/<disease>/",
    )
    output_dir: str = Field(
        ...,
        description="Output directory for mapped results (mapper_dir)",
    )


def resolve_mapper_paths_per_cancer_group(
    project_path: Path,
    step_override_path: Optional[Path] = None,
    control_index: int = 0,
    disease_subdir: str = "disease",  # deprecated compatibility argument; comparison layout is canonical
    csv_filename_pattern: str = DMP_CSV_PATTERN_DISCOVERY,
) -> List[Tuple[MapperStepPaths, str]]:
    """
    Build one MapperStepPaths per comparison (control vs disease).
    When project uses control/disease + comparisons: one entry per get_comparisons().
    Otherwise: one per non-control group (flat groups).

    `disease_subdir` is ignored for the canonical comparison layout and is only
    kept to avoid breaking older callers.
    """
    project = load_project(project_path)
    step_cfg = resolve_for_project("mapper", project)
    if step_override_path and step_override_path.exists():
        import json
        with open(step_override_path) as f:
            overrides = json.load(f)
        step_cfg = {**step_cfg, **overrides}
    pattern = step_cfg.get("csv_filename_pattern") or step_cfg.get("csv_pattern") or csv_filename_pattern
    if "/" in pattern or "\\" in pattern:
        pattern = Path(pattern).name

    def _mapper_output_dir(control_group: str, disease_group: str) -> str:
        override = step_cfg.get("output_dir")
        if override is None:
            return str(project.get_mapper_output_dir(control_group, disease_group))
        override_path = Path(override)
        comparison_tail = (control_group, disease_group)
        if override_path.parts[-2:] == comparison_tail:
            return str(override_path)
        return str(override_path / control_group / disease_group)

    if getattr(project, "uses_control_disease", lambda: False)():
        out: List[Tuple[MapperStepPaths, str]] = []
        for spec in project.get_comparisons():
            comp_label = spec.comparison_label or spec.disease_group
            det_dir = _resolve_detection_dir_with_case_fallback(
                project,
                control_group=spec.control_group,
                disease_group=spec.disease_group,
            )
            map_dir = _mapper_output_dir(spec.control_group, spec.disease_group)
            group_csv = str(Path(det_dir) / pattern)
            out.append((MapperStepPaths(csv_pattern=group_csv, output_dir=map_dir), comp_label))
        return out

    resolved = getattr(project, "get_resolved_groups", lambda: [])()
    if len(resolved) < 2:
        return []
    control_label = resolved[control_index][0]
    out = []
    for i in range(len(resolved)):
        if i == control_index:
            continue
        label = resolved[i][0]
        group_csv = str(Path(project.get_detection_output_dir(control_label, label)) / pattern)
        group_out = _mapper_output_dir(control_label, label)
        out.append((MapperStepPaths(csv_pattern=group_csv, output_dir=group_out), label))
    return out


def resolve_mapper_paths(
    project_path: Path,
    step_override_path: Optional[Path] = None,
    csv_filename_pattern: str = DMP_CSV_PATTERN_DISCOVERY,
) -> MapperStepPaths:
    """
    Build mapper step paths from a project config.

    Build a single mapper input glob and output directory from the project.

    When the project is comparison-driven, the returned glob points at
    `detections/*/*/<pattern>` so all comparison directories are included. For
    legacy flat-group projects, it points at `detections/<control_group>/*/<pattern>`.
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    detection_dir = Path(paths.detection_dir)
    output_dir = paths.mapper_dir

    resolved_groups = getattr(project, "get_resolved_groups", lambda: [])()
    use_comparison_layout = len(resolved_groups) >= 2

    def _resolve_csv_pattern(pattern: str, use_comparison_dirs: bool) -> str:
        """Resolve pattern under the canonical comparison directory structure."""
        p = Path(pattern)
        if p.is_absolute():
            return pattern
        if use_comparison_dirs:
            if getattr(project, "uses_control_disease", lambda: False)():
                return str(detection_dir / "*" / "*" / pattern)
            control_label = resolved_groups[0][0]
            return str(detection_dir / control_label / "*" / pattern)
        return str(detection_dir / pattern)

    csv_pattern = _resolve_csv_pattern(csv_filename_pattern, use_comparison_layout)

    step_cfg = resolve_for_project("mapper", project)
    if step_cfg:
        if step_cfg.get("csv_filename_pattern") is not None:
            csv_pattern = _resolve_csv_pattern(step_cfg["csv_filename_pattern"], use_comparison_layout)
        elif step_cfg.get("csv_pattern") is not None:
            csv_pattern = _resolve_csv_pattern(step_cfg["csv_pattern"], use_comparison_layout)
        if step_cfg.get("output_dir") is not None:
            output_dir = step_cfg["output_dir"]

    overrides: dict = {}
    if step_override_path and step_override_path.exists():
        import json
        with open(step_override_path) as f:
            overrides = json.load(f)

    if overrides.get("csv_filename_pattern") is not None:
        csv_pattern = _resolve_csv_pattern(overrides["csv_filename_pattern"], use_comparison_layout)
    elif overrides.get("csv_pattern") is not None:
        csv_pattern = _resolve_csv_pattern(overrides["csv_pattern"], use_comparison_layout)
    if overrides.get("output_dir") is not None:
        output_dir = overrides["output_dir"]

    return MapperStepPaths(csv_pattern=csv_pattern, output_dir=output_dir)
