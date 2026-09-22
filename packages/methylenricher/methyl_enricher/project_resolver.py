"""
Resolve MethylEnricher paths from a project config.

The canonical downstream layout is comparison-based, matching mapper outputs:

    mapper/<control_group>/<disease_group>/
    enricher/<control_group>/<disease_group>/

Legacy flat-group projects are normalized onto the same directory contract.
"""

from pathlib import Path
from typing import List, Optional, Tuple
import warnings

from pydantic import BaseModel, Field

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

DEFAULT_METHYL_ENRICHER_HOME = "/work/cache/methyl_enricher"


def _warn_enricher_alias_keys(cfg: dict) -> None:
    if "input" in cfg:
        warnings.warn(
            "step_config.enricher.input is deprecated; use step_config.enricher.input_file.",
            DeprecationWarning,
            stacklevel=2,
        )
    if "outdir" in cfg:
        warnings.warn(
            "step_config.enricher.outdir is deprecated; use step_config.enricher.output_dir.",
            DeprecationWarning,
            stacklevel=2,
        )


class EnricherStepPaths(BaseModel):
    """Paths for the enricher step derived from a project (and optional overrides)."""

    input_file: str = Field(
        ...,
        description="Input combined gene CSV (e.g. mapper all-gene_name-combined.csv)",
    )
    output_dir: str = Field(
        ...,
        description="Output directory for enrichment results (enricher_dir)",
    )


def resolve_enricher_paths_per_cancer_group(
    project_path: Path,
    step_override_path: Optional[Path] = None,
    control_index: int = 0,
    disease_subdir: str = "cancer",  # deprecated compatibility argument; comparison layout is canonical
    combined_csv_name: str = "all-gene_name-combined.csv",
) -> List[Tuple[EnricherStepPaths, str]]:
    """
    Build one EnricherStepPaths per comparison.
    When project uses control/disease + comparisons: one entry per get_comparisons().
    Otherwise: one per non-control group (flat groups).

    `disease_subdir` is ignored for the canonical comparison layout and is only
    kept to avoid breaking older callers.
    """
    project = load_project(project_path)
    step_cfg = resolve_for_project("enricher", project)
    if step_override_path and step_override_path.exists():
        import json
        with open(step_override_path) as f:
            overrides = json.load(f)
        step_cfg = {**step_cfg, **overrides}
    _warn_enricher_alias_keys(step_cfg)
    csv_name = step_cfg.get("combined_csv_name") or combined_csv_name

    if getattr(project, "uses_control_disease", lambda: False)():
        out: List[Tuple[EnricherStepPaths, str]] = []
        for spec in project.get_comparisons():
            comp_label = spec.comparison_label or spec.disease_group
            map_dir = project.get_mapper_output_dir(spec.control_group, spec.disease_group)
            enr_dir = project.get_enricher_output_dir(spec.control_group, spec.disease_group)
            input_file = str(Path(map_dir) / csv_name)
            out.append((EnricherStepPaths(input_file=input_file, output_dir=enr_dir), comp_label))
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
        input_file = str(Path(project.get_mapper_output_dir(control_label, label)) / csv_name)
        output_dir = project.get_enricher_output_dir(control_label, label)
        out.append((EnricherStepPaths(input_file=input_file, output_dir=output_dir), label))
    return out


def resolve_enricher_paths(
    project_path: Path,
    step_override_path: Optional[Path] = None,
    combined_csv_name: str = "all-gene_name-combined.csv",
) -> EnricherStepPaths:
    """
    Build enricher step paths from a project config.

    Input is the mapper combined CSV; output goes to enricher_dir.
    Optional step_override_path JSON can override input_file and/or output_dir.
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    input_file = paths.mapper_combined_csv
    output_dir = paths.enricher_dir

    # Apply project-level step config (enricher) if present
    step_cfg = resolve_for_project("enricher", project)
    if step_cfg:
        _warn_enricher_alias_keys(step_cfg)
        if step_cfg.get("input_file") is not None:
            input_file = step_cfg["input_file"]
        if step_cfg.get("input") is not None:
            input_file = step_cfg["input"]
        if step_cfg.get("output_dir") is not None:
            output_dir = step_cfg["output_dir"]
        if step_cfg.get("outdir") is not None:
            output_dir = step_cfg["outdir"]

    overrides: dict = {}
    if step_override_path and step_override_path.exists():
        import json
        with open(step_override_path) as f:
            overrides = json.load(f)
    _warn_enricher_alias_keys(overrides)

    if overrides.get("input") is not None:
        input_file = overrides["input"]
    if overrides.get("input_file") is not None:
        input_file = overrides["input_file"]
    if overrides.get("output_dir") is not None:
        output_dir = overrides["output_dir"]
    if overrides.get("outdir") is not None:
        output_dir = overrides["outdir"]

    return EnricherStepPaths(input_file=input_file, output_dir=output_dir)


def resolve_methyl_enricher_home(
    project_path: Path,
    step_override_path: Optional[Path] = None,
    default_home: str = DEFAULT_METHYL_ENRICHER_HOME,
) -> str:
    """
    Resolve cache/home root for MethylEnricher persistent artifacts.

    Precedence:
      1) step override JSON `methyl_enricher_home`
      2) project `step_config.enricher.methyl_enricher_home`
      3) default `/work/cache/methyl_enricher`
    """
    project = load_project(project_path)
    step_cfg = resolve_for_project("enricher", project)
    home = step_cfg.get("methyl_enricher_home")
    if step_override_path and step_override_path.exists():
        import json

        with open(step_override_path, encoding="utf-8") as f:
            overrides = json.load(f)
        if isinstance(overrides, dict) and overrides.get("methyl_enricher_home"):
            home = overrides.get("methyl_enricher_home")
    resolved = str(home).strip() if home is not None else ""
    if not resolved:
        resolved = str(default_home)
    return str(Path(resolved))
