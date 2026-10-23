"""Entry-point surface for FOREACH iteration-bundle helpers."""

from __future__ import annotations

from methyl_domain.foreach_bundle import (
    commit_iteration_bundle,
    compute_iteration_bundle_key,
    resolve_study_root_from_scope,
    try_short_circuit_foreach_iteration,
)

__all__ = [
    "commit_iteration_bundle",
    "compute_iteration_bundle_key",
    "resolve_study_root_from_scope",
    "try_short_circuit_foreach_iteration",
]
