"""
Typed contracts for portal/cfg-driven hyperparameter grid searches.

These Pydantic models are the source of truth for the JSON exchanged between the
portal (which owns the grid definition and the N trial instances) and the cfg
search ledger. They are exported to JSON Schema (``schemas/config/``) like every
other strongly-typed configuration surface, so operators tune grids through
schema-validated JSON rather than code.

The workflow engine (wf) never sees "hyperparameter": each trial resolves to a
process-agnostic ``execution_scope`` once its actionConfig overlay is baked.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .optimization import ObjectiveWeights


class HyperparamGridSpec(BaseModel):
    """A discrete grid: each named axis maps to the values it should sweep.

    Grid is the only supported strategy in this slice (Random / Hyperband /
    Bayesian are intentionally out of scope). Axis names are dotted ``actionConfig``
    paths (e.g. ``validation.stability_dmp_freq``) applied as per-trial overlays.
    """

    model_config = ConfigDict(extra="forbid")

    axes: Dict[str, List[Any]] = Field(
        default_factory=dict,
        description=(
            "Map of actionConfig overlay path to the discrete values to sweep. "
            "Operator-set per search from the portal; the Cartesian product defines the trials."
        ),
    )

    def expand(self) -> List["HyperparamTrialOverlay"]:
        """Cartesian product of axes -> one trial overlay per grid point."""
        keys = list(self.axes.keys())
        if not keys:
            return []
        value_lists = [list(self.axes[k]) for k in keys]
        overlays: List[HyperparamTrialOverlay] = []
        for index, combo in enumerate(itertools.product(*value_lists)):
            overrides = {keys[i]: combo[i] for i in range(len(keys))}
            overlays.append(HyperparamTrialOverlay(index=index, overrides=overrides))
        return overlays


class HyperparamTrialOverlay(BaseModel):
    """One grid point: a deep-merge delta applied to the base ``actionConfig``."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Zero-based trial index within the search.")
    overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description="Dotted actionConfig paths -> value for this trial.",
    )
    label: Optional[str] = Field(
        default=None,
        description="Optional operator label for the trial (display only).",
    )


class HyperparamSearchRequest(BaseModel):
    """Start payload for a portal-driven multi-instance grid search.

    The base study/instance references are the same fields the portal already uses
    to start a single validation instance; ``grid`` and ``objective`` extend them.
    """

    model_config = ConfigDict(extra="forbid")

    project_path: str = Field(
        description="Study manifest path (/work/projects/<study>/configs/project_*.json).",
    )
    pipeline_profile: Optional[str] = Field(
        default=None,
        description="Pipeline profile name resolved under METHYL_PROFILE_DIR / cfg.",
    )
    profile_path: Optional[str] = Field(
        default=None,
        description="Explicit profile path override (dev/CI); prefer pipeline_profile in production.",
    )
    program: Optional[str] = Field(
        default=None,
        description="DomainProgram name/path the trials instantiate (defaults to the study's program).",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Operator-facing search name shown in the portal.",
    )
    grid: HyperparamGridSpec = Field(
        default_factory=HyperparamGridSpec,
        description="Discrete grid expanded into one workflow instance per point.",
    )
    objective: ObjectiveWeights = Field(
        default_factory=ObjectiveWeights,
        description="Weights for scoring completed trials via objective J.",
    )
    baseline_metrics_summary_path: Optional[str] = Field(
        default=None,
        description="Optional baseline metrics_summary.json for rollout-style feasibility constraints.",
    )


class HyperparamScenarioRequest(BaseModel):
    """Start one validation instance from a trial overlay (no Cartesian grid).

    Use for assumption checks and stability-target scenarios without opening a
    full hyperparameter search. Optionally ledgered as a single cfg trial.
    """

    model_config = ConfigDict(extra="forbid")

    project_path: str = Field(
        description="Study manifest path (/work/projects/<study>/configs/project_*.json).",
    )
    pipeline_profile: Optional[str] = Field(
        default=None,
        description="Pipeline profile name resolved under METHYL_PROFILE_DIR / cfg.",
    )
    profile_path: Optional[str] = Field(
        default=None,
        description="Explicit profile path override (dev/CI); prefer pipeline_profile in production.",
    )
    program: Optional[str] = Field(
        default=None,
        description="DomainProgram name/path to instantiate.",
    )
    display_name: Optional[str] = Field(
        default=None,
        description="Operator-facing scenario name (also used as executionScopeName).",
    )
    overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dotted actionConfig paths → value (e.g. validation.stability_target_balanced_accuracy). "
            "JSON null clears a site/profile knob."
        ),
    )
    execution_scope_name: Optional[str] = Field(
        default=None,
        description="Optional executionScopeName; defaults to display_name or 'scenario-0'.",
    )
    objective: ObjectiveWeights = Field(
        default_factory=ObjectiveWeights,
        description="Optional objective weights when ledgering/scoring this scenario later.",
    )


class HyperparamTrialStatus(BaseModel):
    """UI-facing status for one trial (portal.sp_get_hyperparam_search row)."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Trial index within the search.")
    overrides: Dict[str, Any] = Field(
        default_factory=dict, description="actionConfig overlay applied to this trial."
    )
    workflow_instance_id: Optional[int] = Field(
        default=None, description="wf.workflow_instance id started for this trial."
    )
    execution_scope_key: Optional[str] = Field(
        default=None, description="wf.execution_scope set_key baked for this trial."
    )
    status: Optional[str] = Field(
        default=None, description="Instance/trial lifecycle status."
    )
    objective: Optional[float] = Field(
        default=None, description="Scored objective J (null until the trial is scored)."
    )
    feasible: Optional[bool] = Field(
        default=None, description="Whether the trial satisfied objective constraints."
    )


class HyperparamSearchStatus(BaseModel):
    """UI-facing search summary: trials + best winner."""

    model_config = ConfigDict(extra="forbid")

    search_id: str = Field(description="cfg.hyperparameter_search_run identifier.")
    status: Optional[str] = Field(default=None, description="Overall search status.")
    trials: List[HyperparamTrialStatus] = Field(
        default_factory=list, description="Per-trial status rows."
    )
    best_trial_index: Optional[int] = Field(
        default=None, description="Index of the highest-scoring feasible trial, if any."
    )
