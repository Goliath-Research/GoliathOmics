"""Dynamic epigenetic measures (channel capacity, RDE, turnover) — phase 2 scaffold."""

from __future__ import annotations

from typing import Any, Dict

from ..config import InfoTheoryStepConfig

DEFERRED_REASON = "deferred_v2_phase2"


def channel_capacity(*_args, **_kwargs) -> Dict[str, Any]:
    """
    Information-theoretic channel capacity of the local epigenetic state.

    Deferred: requires the Nat. Genet. 2017 potential-energy / birth-death dynamics model.
    """
    return {"status": "not_computed", "reason": DEFERRED_REASON}


def relative_dissipated_energy(*_args, **_kwargs) -> Dict[str, Any]:
    """
    Relative dissipated energy (RDE) — thermodynamic cost of maintaining methylation patterns.

    Deferred: requires fitted de novo / maintenance / demethylation rates.
    """
    return {"status": "not_computed", "reason": DEFERRED_REASON}


def turnover_ratio(*_args, **_kwargs) -> Dict[str, Any]:
    """
    Turnover ratio from the dynamic methylation model.

    Deferred: requires phase-2 dynamics estimation.
    """
    return {"status": "not_computed", "reason": DEFERRED_REASON}


def build_dynamics_report(cfg: InfoTheoryStepConfig) -> Dict[str, Any]:
    """Return dynamics section for confirmation report when dynamics_enabled is set."""
    if not cfg.dynamics_enabled:
        return {"status": "disabled"}
    return {
        "status": "not_computed",
        "reason": DEFERRED_REASON,
        "channel_capacity": channel_capacity(),
        "relative_dissipated_energy": relative_dissipated_energy(),
        "turnover_ratio": turnover_ratio(),
    }
