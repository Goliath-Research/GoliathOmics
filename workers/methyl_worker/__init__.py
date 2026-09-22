"""Python REST worker for the MethylPipeline workflow engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import WorkflowRestClient
    from .runner import WorkerRunner

__all__ = ["WorkflowRestClient", "WorkerRunner"]


def __getattr__(name: str) -> object:
    if name == "WorkflowRestClient":
        from .client import WorkflowRestClient

        return WorkflowRestClient
    if name == "WorkerRunner":
        from .runner import WorkerRunner

        return WorkerRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
