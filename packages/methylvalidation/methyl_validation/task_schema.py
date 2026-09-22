"""
JSON task descriptors for distributed queue workers (Pydantic).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from methyl_domain.types import CentroidSeedGroup
from pydantic import BaseModel, ConfigDict, Field


class TaskMode(str, Enum):
    """High-level task kind."""

    DISCOVERY = "discovery"  # centroid + detector (+optional mapper pipeline)
    PREDICTOR_ONLY = "predictor_only"


class DiscoveryRunTaskV1(BaseModel):
    """
    One Monte Carlo \"discovery\" iteration: run centroid/detector (or predictor-only) for a
    pre-planned `run_####` directory.
    """

    model_config = ConfigDict(extra="forbid")

    task_schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(..., min_length=1, description="Stable id for the queue, e.g. discovery_run_0001")
    mode: TaskMode = TaskMode.DISCOVERY
    run_id: str = Field(..., min_length=1, description="e.g. run_0001")
    iteration: int = Field(..., ge=0, description="0-based index within the MC plan")
    layout: Literal["binary", "multiclass", "hierarchical_multiclass"]
    project_json: str = Field(..., description="Absolute path to the run's project.json")
    monte_carlo_runs_root: str
    run_dir: str
    skip_centroid: bool = False
    per_cancer_group: bool = False
    predictor_only: bool = False
    frozen_project_path: Optional[str] = None
    centroid_seed_root: Optional[str] = None
    centroid_seed_groups: Optional[List[CentroidSeedGroup]] = None
    # Binary paths (relative/absolute as written by project_gen)
    val_control_csv: Optional[str] = None
    val_disease_csv: Optional[str] = None
    # Multiclass
    val_groups_json: Optional[str] = None
    centroid_group1_override: Optional[str] = None
    centroid_group2_override: Optional[str] = None
    detector_step_override_path: Optional[str] = None
    # Worker loads full config
    mc_config_path: str = Field(..., description="Path to mc_config.json snapshot in queue/")


def parse_discovery_task_file(path: str) -> DiscoveryRunTaskV1:
    import json
    from pathlib import Path

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    legacy_override = raw.pop("detector_step_override", None)
    if legacy_override is not None and raw.get("detector_step_override_path") is None:
        if isinstance(legacy_override, str):
            raw["detector_step_override_path"] = legacy_override
    return DiscoveryRunTaskV1.model_validate(raw)
