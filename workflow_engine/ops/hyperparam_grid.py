"""
Expand a hyperparameter grid into N workflow instances (multi-instance HPO).

The outer search loop lives here (portal middle-tier / admin CLI), never on
workers. Each grid point becomes one wf.workflow_instance with its own baked
``resolvedConfig`` and process-agnostic ``execution_scope``. The portal owns the
grid definition and the trial -> instance map; this module is the shared engine
that portal SQL callers and ``methyl-study-start`` both drive.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

from ops._paths import ensure_import_paths
from ops.study_lifecycle import resolve_workflow_version_id

logger = logging.getLogger(__name__)


def set_by_path(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set ``a.b.c`` -> value inside a nested dict, creating intermediate dicts."""
    parts = [p for p in str(dotted_key).split(".") if p]
    if not parts:
        return
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _delete_by_path(target: Dict[str, Any], dotted_key: str) -> None:
    """Remove ``a.b.c`` from a nested dict (no-op if missing)."""
    parts = [p for p in str(dotted_key).split(".") if p]
    if not parts:
        return
    cur: Any = target
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def apply_overlay(action_config: Mapping[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a deep copy of ``action_config`` with dotted-path overrides applied.

    Overlay value ``None`` (JSON null) deletes the target key so operators can
    clear / uncap site or profile knobs in a scenario or grid trial.
    """
    merged = copy.deepcopy(dict(action_config)) if action_config else {}
    for dotted_key, value in overrides.items():
        if value is None:
            _delete_by_path(merged, dotted_key)
        else:
            set_by_path(merged, dotted_key, value)
    return merged


class TrialLedger(Protocol):
    """Optional sink for cfg.hyperparameter_search_* rows (portal supplies its own)."""

    def start_search(self, request: Any) -> int: ...

    def add_trial(
        self,
        *,
        search_id: int,
        trial_index: int,
        overrides: Dict[str, Any],
        workflow_instance_id: int,
        execution_scope_key: Optional[str],
    ) -> None: ...


class DbTrialLedger:
    """TrialLedger backed by portal.sp_* procs (used by methyl-study-start / CI)."""

    def __init__(self, db: Any, *, study_row_id: Optional[int] = None, created_by: Optional[str] = None):
        self._db = db
        self._study_row_id = study_row_id
        self._created_by = created_by

    def start_search(self, request: Any) -> int:
        return self._db.start_hyperparam_search(
            study_row_id=self._study_row_id,
            display_name=getattr(request, "display_name", None),
            grid_json=request.grid.model_dump(mode="json"),
            objective_json=request.objective.model_dump(mode="json"),
            created_by=self._created_by,
        )

    def add_trial(
        self,
        *,
        search_id: int,
        trial_index: int,
        overrides: Dict[str, Any],
        workflow_instance_id: int,
        execution_scope_key: Optional[str],
    ) -> None:
        self._db.add_hyperparam_trial(
            search_id=search_id,
            trial_index=trial_index,
            overrides_json=overrides,
            workflow_instance_id=workflow_instance_id,
            execution_scope_key=execution_scope_key,
        )


@dataclass
class TrialStart:
    index: int
    overrides: Dict[str, Any]
    workflow_instance_id: int
    workflow_version_id: int
    execution_scope_key: Optional[str]


@dataclass
class GridStartResult:
    search_id: Optional[int]
    trials: List[TrialStart] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "search_id": self.search_id,
            "trials": [
                {
                    "index": t.index,
                    "overrides": t.overrides,
                    "instance_id": t.workflow_instance_id,
                    "workflow_version_id": t.workflow_version_id,
                    "executionScopeId": t.execution_scope_key,
                }
                for t in self.trials
            ],
        }


def expand_and_start_grid(
    db: Any,
    request: Any,
    *,
    create_workflow_definition,
    create_workflow_instance,
    start_workflow_instance,
    ledger: Optional[TrialLedger] = None,
) -> GridStartResult:
    """Plan once, then start one instance per grid point.

    ``request`` is a ``HyperparamSearchRequest`` (or a dict that validates as one).
    Returns the search id (if a ledger was supplied) and the trial -> instance map.
    """
    ensure_import_paths()
    from methyl_validation.hyperparam_models import HyperparamSearchRequest
    from methyl_validation.workflow_planner import plan_validation_context
    from rest.execution_scope import extract_execution_scope_payload
    from workflow_context import finalize_instance_context

    if not isinstance(request, HyperparamSearchRequest):
        request = HyperparamSearchRequest.model_validate(request)

    base_body: Dict[str, Any] = {"projectPath": request.project_path}
    if request.pipeline_profile:
        base_body["pipelineProfile"] = request.pipeline_profile
    if request.profile_path:
        base_body["profilePath"] = request.profile_path
    if request.program:
        base_body["program_path"] = request.program

    planned = plan_validation_context(dict(base_body))
    base_context = planned.model_dump(mode="json")

    overlays = request.grid.expand()

    # Compile/resolve the DomainProgram once — inputs are loop-invariant across trials.
    version_id = resolve_workflow_version_id(
        db,
        {**base_body, "projectPath": request.project_path},
        create_workflow_definition=create_workflow_definition,
    )

    search_id: Optional[int] = None
    if ledger is not None:
        search_id = ledger.start_search(request)

    result = GridStartResult(search_id=search_id)

    for overlay in overlays:
        trial_context = copy.deepcopy(base_context)
        trial_context["actionConfig"] = apply_overlay(
            trial_context.get("actionConfig") or {}, overlay.overrides
        )
        # Distinct, human-readable scope label per trial so provenance and the
        # baked execution_scope stay unique even when overrides coincide.
        label = overlay.label or (
            f"{request.display_name}#trial-{overlay.index}"
            if request.display_name
            else f"trial-{overlay.index}"
        )
        trial_context["executionScopeName"] = label
        trial_context["trialIndex"] = overlay.index

        context = finalize_instance_context(trial_context)

        instance_id = create_workflow_instance(db, version_id, context)

        scope = extract_execution_scope_payload(context)
        scope_key = scope["set_key"] if scope else None
        if scope is not None:
            try:
                db.apply_execution_scope(instance_id, **scope)
            except Exception:
                logger.warning(
                    "Failed to register execution scope for trial %s instance %s",
                    overlay.index,
                    instance_id,
                    exc_info=True,
                )
        start_workflow_instance(db, instance_id)

        if ledger is not None and search_id is not None:
            try:
                ledger.add_trial(
                    search_id=search_id,
                    trial_index=overlay.index,
                    overrides=overlay.overrides,
                    workflow_instance_id=instance_id,
                    execution_scope_key=scope_key,
                )
            except Exception:
                logger.warning(
                    "Failed to record trial %s in cfg ledger", overlay.index, exc_info=True
                )

        result.trials.append(
            TrialStart(
                index=overlay.index,
                overrides=overlay.overrides,
                workflow_instance_id=instance_id,
                workflow_version_id=version_id,
                execution_scope_key=scope_key,
            )
        )

    return result


def start_scenario_trial(
    db: Any,
    request: Any,
    *,
    create_workflow_definition,
    create_workflow_instance,
    start_workflow_instance,
    ledger: Optional[TrialLedger] = None,
) -> GridStartResult:
    """Start one workflow instance from a scenario overlay (no Cartesian grid).

    Reuses the same bake path as grid trials: plan → apply_overlay →
    finalize_instance_context → execution_scope. When a ledger is supplied the
    scenario is recorded as a single-trial search for later scoring/compare.
    """
    ensure_import_paths()
    from methyl_validation.hyperparam_models import (
        HyperparamGridSpec,
        HyperparamScenarioRequest,
        HyperparamSearchRequest,
        HyperparamTrialOverlay,
    )
    from methyl_validation.workflow_planner import plan_validation_context
    from rest.execution_scope import extract_execution_scope_payload
    from workflow_context import finalize_instance_context

    if not isinstance(request, HyperparamScenarioRequest):
        request = HyperparamScenarioRequest.model_validate(request)

    base_body: Dict[str, Any] = {"projectPath": request.project_path}
    if request.pipeline_profile:
        base_body["pipelineProfile"] = request.pipeline_profile
    if request.profile_path:
        base_body["profilePath"] = request.profile_path
    if request.program:
        base_body["program_path"] = request.program

    planned = plan_validation_context(dict(base_body))
    base_context = planned.model_dump(mode="json")

    version_id = resolve_workflow_version_id(
        db,
        {**base_body, "projectPath": request.project_path},
        create_workflow_definition=create_workflow_definition,
    )

    # Optional ledger: open a 1-trial search so scenarios can score/compare later.
    search_id: Optional[int] = None
    if ledger is not None:
        grid_request = HyperparamSearchRequest(
            project_path=request.project_path,
            pipeline_profile=request.pipeline_profile,
            profile_path=request.profile_path,
            program=request.program,
            display_name=request.display_name or "scenario",
            grid=HyperparamGridSpec(axes={}),
            objective=request.objective,
        )
        # Ledger APIs expect a grid_json; record empty axes + single trial manually.
        search_id = ledger.start_search(grid_request)

    overlay = HyperparamTrialOverlay(
        index=0,
        overrides=dict(request.overrides or {}),
        label=request.execution_scope_name
        or request.display_name
        or "scenario-0",
    )

    trial_context = copy.deepcopy(base_context)
    trial_context["actionConfig"] = apply_overlay(
        trial_context.get("actionConfig") or {}, overlay.overrides
    )
    trial_context["executionScopeName"] = overlay.label
    trial_context["trialIndex"] = 0

    context = finalize_instance_context(trial_context)
    instance_id = create_workflow_instance(db, version_id, context)

    scope = extract_execution_scope_payload(context)
    scope_key = scope["set_key"] if scope else None
    if scope is not None:
        try:
            db.apply_execution_scope(instance_id, **scope)
        except Exception:
            logger.warning(
                "Failed to register execution scope for scenario instance %s",
                instance_id,
                exc_info=True,
            )
    start_workflow_instance(db, instance_id)

    if ledger is not None and search_id is not None:
        try:
            ledger.add_trial(
                search_id=search_id,
                trial_index=0,
                overrides=overlay.overrides,
                workflow_instance_id=instance_id,
                execution_scope_key=scope_key,
            )
        except Exception:
            logger.warning("Failed to record scenario trial in cfg ledger", exc_info=True)

    return GridStartResult(
        search_id=search_id,
        trials=[
            TrialStart(
                index=0,
                overrides=overlay.overrides,
                workflow_instance_id=instance_id,
                workflow_version_id=version_id,
                execution_scope_key=scope_key,
            )
        ],
    )


def score_grid(
    db: Any,
    search_id: int,
    weights: Any,
    trial_mc_runs: Mapping[Any, str],
    *,
    constraints: Any = None,
) -> Dict[str, Any]:
    """Score completed trials with objective J and persist scores to the cfg ledger.

    ``trial_mc_runs`` maps trial index -> that trial's ``monte_carlo_runs`` directory
    (the portal/operator supplies concrete paths; workers never run the outer loop).
    Returns a summary with the best feasible trial index.
    """
    ensure_import_paths()
    from methyl_validation.optimization import (
        ObjectiveWeights,
        objective_from_monte_carlo_artifacts,
    )

    if not isinstance(weights, ObjectiveWeights):
        weights = ObjectiveWeights.model_validate(weights or {})

    scored: List[Dict[str, Any]] = []
    best_index: Optional[int] = None
    best_value = float("-inf")

    for row in db.get_hyperparam_search(search_id):
        idx = row.get("trial_index")
        if idx is None:
            continue
        mc = trial_mc_runs.get(idx)
        if mc is None:
            mc = trial_mc_runs.get(str(idx))
        if not mc:
            continue
        res = objective_from_monte_carlo_artifacts(mc, weights, constraints)
        db.score_hyperparam_trial(
            search_id=search_id,
            trial_index=int(idx),
            objective=res.value,
            feasible=res.feasible,
            result_json=res.to_json_friendly(),
        )
        scored.append(
            {"index": int(idx), "objective": res.value, "feasible": res.feasible, "reason": res.reason}
        )
        if res.feasible and res.value > best_value:
            best_value = res.value
            best_index = int(idx)

    return {"search_id": search_id, "scored": scored, "best_trial_index": best_index}


def winner_overlay(db: Any, search_id: int) -> Optional[Dict[str, Any]]:
    """Return the actionConfig overlay of the best feasible trial (operator-gated export).

    This never mutates a published profile: it only surfaces the recommended overlay
    for an operator to promote via ``portal.sp_promote_hyperparam_winner``, which
    applies these dotted keys onto the existing study ``actionConfig`` (same as
    ``apply_overlay``) rather than replacing the overlay wholesale.
    """
    rows = db.get_hyperparam_search(search_id)
    best: Optional[Dict[str, Any]] = None
    best_value = float("-inf")
    for row in rows:
        if not row.get("feasible"):
            continue
        obj = row.get("objective")
        if obj is None:
            continue
        if float(obj) > best_value:
            best_value = float(obj)
            best = row
    if best is None:
        return None
    return {
        "search_id": search_id,
        "trial_index": best.get("trial_index"),
        "objective": best.get("objective"),
        "execution_scope_key": best.get("execution_scope_key"),
        "overrides": best.get("overrides_json") or {},
    }
