"""Worker-local dependency injection for in-process handlers.

Kept **out of** the SQL engine, gateway, and DomainProgram compiler. Those layers
continue to see only ``action_name`` + JSON. This module only resolves optional
handler kwargs (``TaskRuntimeContext``, logger, path helpers) when invoking
catalog ``in_process_handler`` callables.

Usage::

    from methyl_worker.depends import Depends, get_logger, get_runtime

    def _handle_example(
        _capability: str,
        _action_name: str,
        input: ExampleTaskInput,
        runtime: TaskRuntimeContext = Depends(get_runtime),
        log: logging.Logger = Depends(get_logger),
    ) -> ExampleTaskOutput:
        ...
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, get_type_hints

from pydantic import BaseModel

from .task_models.runtime_models import TaskRuntimeContext

Provider = Callable[["HandlerInvokeContext"], Any]


@dataclass(frozen=True)
class Depends:
    """Marker for a handler parameter default that should be injected."""

    dependency: Provider


@dataclass(frozen=True)
class HandlerInvokeContext:
    """Per-invocation bag available to dependency providers."""

    capability: str
    action_name: str
    input_model: BaseModel
    runtime: TaskRuntimeContext
    logger: logging.Logger

    def input_json(self) -> dict[str, Any]:
        return self.input_model.model_dump(mode="json")


def get_runtime(ctx: HandlerInvokeContext) -> TaskRuntimeContext:
    return ctx.runtime


def get_logger(ctx: HandlerInvokeContext) -> logging.Logger:
    return ctx.logger


def get_capability(ctx: HandlerInvokeContext) -> str:
    return ctx.capability


def get_action_name(ctx: HandlerInvokeContext) -> str:
    return ctx.action_name


def get_project_path(ctx: HandlerInvokeContext) -> Path:
    """Resolve ``projectPath`` / ``project`` from the validated task input."""
    data = ctx.input_json()
    raw = data.get("projectPath") or data.get("project")
    if not raw:
        raise RuntimeError(f"{ctx.action_name} requires projectPath")
    return Path(str(raw)).expanduser()


def get_monte_carlo_runs_root(ctx: HandlerInvokeContext) -> Path:
    """Resolve Monte Carlo runs root from task input (explicit or project layout)."""
    data = ctx.input_json()
    project_path = data.get("projectPath") or data.get("project")
    if not project_path:
        raise RuntimeError(f"{ctx.action_name} requires projectPath")
    explicit = data.get("monteCarloRunsRoot")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    from methyl_utils import load_project

    project = load_project(str(project_path))
    return Path(project.output_base) / project.project_name / "monte_carlo_runs"


def _normalize_runtime(runtime: Any) -> TaskRuntimeContext:
    if isinstance(runtime, TaskRuntimeContext):
        return runtime
    if runtime is None:
        return TaskRuntimeContext.from_wire({})
    if isinstance(runtime, BaseModel):
        return TaskRuntimeContext.model_validate(runtime.model_dump(mode="json"))
    if isinstance(runtime, dict):
        return TaskRuntimeContext.from_wire(runtime)
    return TaskRuntimeContext.from_wire({})


def _is_depends(value: Any) -> bool:
    return isinstance(value, Depends)


def _is_concrete_model(annotation: Any) -> bool:
    return (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and annotation is not BaseModel
    )


def _handler_input_model_cls(
    handler: Callable[..., Any],
    params: list[inspect.Parameter],
) -> type[BaseModel] | None:
    """Return the concrete BaseModel subclass annotated on the third parameter."""
    if len(params) < 3:
        return None
    param = params[2]
    annotation: Any = param.annotation
    if annotation is inspect.Parameter.empty:
        return None
    if isinstance(annotation, str):
        try:
            hints = get_type_hints(handler)
            annotation = hints.get(param.name)
        except Exception:
            annotation = handler.__globals__.get(annotation)
    if _is_concrete_model(annotation):
        return annotation
    return None


def coerce_handler_input(
    input_model: BaseModel,
    model_cls: type[BaseModel] | None,
) -> BaseModel:
    """Validate ``input_model`` as ``model_cls`` when the handler asked for a concrete type.

    Catalog dispatch already called ``model_validate`` on the action input class.
    This second pass only runs when the handler annotation is a *different*
    concrete model (catalog/handler drift) so the operator sees a Pydantic
    ``ValidationError`` instead of a handler ``RuntimeError``.
    """
    if model_cls is None or isinstance(input_model, model_cls):
        return input_model
    return model_cls.model_validate(input_model.model_dump(mode="json"))


def call_in_process_handler(
    handler: Callable[..., BaseModel],
    capability: str,
    action_name: str,
    input_model: BaseModel,
    runtime: Any = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> BaseModel:
    """Invoke an in-process handler, injecting ``Depends(...)`` defaults.

    Always binds the first three parameters positionally as
    ``(capability, action_name, input_model)``. When the third parameter is
    annotated with a concrete ``BaseModel`` subclass, ``input_model`` is
    coerced to that class (Pydantic ``ValidationError`` on mismatch).
    Additional parameters whose default is ``Depends(provider)`` are filled
    from ``HandlerInvokeContext``.

    Backward compatible: a parameter named ``runtime`` without ``Depends`` still
    receives ``TaskRuntimeContext`` (positional if required, else keyword).
    """
    runtime_ctx = _normalize_runtime(runtime)
    log = logger or logging.getLogger(f"methyl_worker.action.{action_name}")

    try:
        sig = inspect.signature(handler)
        params = list(sig.parameters.values())
    except (TypeError, ValueError):
        params = []
        sig = None

    if sig is not None:
        input_model = coerce_handler_input(
            input_model, _handler_input_model_cls(handler, params)
        )

    invoke = HandlerInvokeContext(
        capability=capability,
        action_name=action_name,
        input_model=input_model,
        runtime=runtime_ctx,
        logger=log,
    )

    if sig is None:
        return handler(capability, action_name, input_model, runtime_ctx)

    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params):
        return handler(capability, action_name, input_model, runtime_ctx)

    positional_values = [capability, action_name, input_model]
    args: list[Any] = []
    kwargs: dict[str, Any] = {}

    for index, param in enumerate(params):
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if index < 3:
            args.append(positional_values[index])
            continue

        default = param.default
        if _is_depends(default):
            kwargs[param.name] = default.dependency(invoke)
            continue

        if param.name == "runtime":
            # Keyword-only params must be passed by name; positional would TypeError.
            if param.kind is inspect.Parameter.KEYWORD_ONLY or default is not inspect.Parameter.empty:
                kwargs[param.name] = runtime_ctx
            else:
                args.append(runtime_ctx)
            continue

        # Leave other defaults / required kwargs to the caller (should not happen
        # for catalog handlers). Required non-Depends params after the third are
        # treated as an error so miswired handlers fail loudly.
        if default is inspect.Parameter.empty:
            raise TypeError(
                f"In-process handler {handler.__name__!r} parameter {param.name!r} "
                f"has no default and is not injectable; use Depends(...) or name it 'runtime'"
            )

    return handler(*args, **kwargs)


# Convenience aliases for handler annotations / defaults.
RuntimeDep = Depends(get_runtime)
LoggerDep = Depends(get_logger)
ProjectPathDep = Depends(get_project_path)
MonteCarloRunsRootDep = Depends(get_monte_carlo_runs_root)

__all__ = [
    "Depends",
    "HandlerInvokeContext",
    "LoggerDep",
    "MonteCarloRunsRootDep",
    "ProjectPathDep",
    "RuntimeDep",
    "call_in_process_handler",
    "coerce_handler_input",
    "get_action_name",
    "get_capability",
    "get_logger",
    "get_monte_carlo_runs_root",
    "get_project_path",
    "get_runtime",
]
