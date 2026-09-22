"""ActionBase: execute validated input via CLI subprocess or in-process call."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from ..action_catalog import ActionCatalogEntry
from ..action_execution import (
    ActionExecutionResult,
    ExecutionTimer,
    execution_result_from_output,
    finalize_output,
    load_input_model,
    validate_input,
)
from ..collectors import ArtifactCollector, GenericPipelineCollector
from ..task_models.step_override_models import CentroidBaseConfigOverride, CentroidStepOverride

logger = logging.getLogger(__name__)

InProcessCallable = Callable[..., BaseModel]


def _call_in_process_handler(
    handler: InProcessCallable,
    capability: str,
    action_name: str,
    input_model: BaseModel,
    runtime: Any,
) -> BaseModel:
    """Invoke handler via worker-local DI (``Depends``) with legacy runtime fallback."""
    from ..depends import call_in_process_handler

    return call_in_process_handler(
        handler,
        capability,
        action_name,
        input_model,
        runtime,
    )


DEFAULT_PIPELINE_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "project_path": "--project",
    "group": "--group",
    "chromosome": "--chromosome",
    "context": "--context",
    "comparison": "--comparison",
    "outputDir": "--output-dir",
    "centroid1Dir": "--centroid1-dir",
    "centroid2Dir": "--centroid2-dir",
    "stepOverride": "--step-override",
    "fixedDmpPanel": "--fixed-dmp-panel",
}


@runtime_checkable
class ActionBase(Protocol):
    execution_mode: str
    entry: ActionCatalogEntry

    def execute(
        self,
        input_json: Mapping[str, Any],
        *,
        handle: Any = None,
    ) -> ActionExecutionResult: ...


class CliAction:
    """Run a console script built from catalog argv_map + resolved input_json."""

    execution_mode = "cli"

    def __init__(
        self,
        *,
        entry: ActionCatalogEntry,
        cli_tool: str,
        argv_map: Mapping[str, str],
        collector: Optional[ArtifactCollector] = None,
        project_keys: tuple[str, ...] = ("project", "projectPath", "project_path"),
    ) -> None:
        self.entry = entry
        self.cli_tool = cli_tool
        self.argv_map = dict(argv_map)
        self.collector = collector or GenericPipelineCollector()
        self.project_keys = project_keys

    def _project_path(self, input_json: Mapping[str, Any]) -> str:
        for key in self.project_keys:
            val = input_json.get(key)
            if val:
                return str(val)
        task_cfg = input_json.get("taskConfig")
        if isinstance(task_cfg, dict):
            for key in self.project_keys + ("projectJson",):
                val = task_cfg.get(key)
                if val:
                    return str(val)
        raise RuntimeError("input_json missing project / projectPath")

    def _materialize_resolved_config_path(self, input_json: Mapping[str, Any]) -> Optional[str]:
        """Write task resolvedConfig slice to a temp JSON file for --resolved-config."""
        resolved = input_json.get("resolvedConfig")
        if not isinstance(resolved, dict) or not resolved:
            return None
        fd, path = tempfile.mkstemp(suffix=".json", prefix="resolved-config-")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(resolved, f)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        return path

    def _argv_value(self, json_key: str, val: Any) -> Optional[str]:
        if val is None or val == "":
            return None
        if json_key == "stepOverride" and isinstance(val, dict):
            fd, path = tempfile.mkstemp(suffix=".json", prefix="step-override-")
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    json.dump(val, f)
            except Exception:
                Path(path).unlink(missing_ok=True)
                raise
            return path
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        return str(val)

    def build_argv(self, input_json: Mapping[str, Any]) -> List[str]:
        data = dict(input_json)
        resolved_path = self._materialize_resolved_config_path(data)
        if resolved_path is not None:
            data["resolvedConfigPath"] = resolved_path
        cmd = [self.cli_tool]
        project_set = False
        step_override: Optional[Dict[str, Any]] = data.get("stepOverride")  # type: ignore[assignment]
        if step_override is None:
            add_samples = data.get("addSamples")
            remove_samples = data.get("removeSamples")
            if add_samples is not None or remove_samples is not None:
                step_override = CentroidStepOverride(
                    base_config=CentroidBaseConfigOverride(
                        add_samples=list(add_samples or []),
                        remove_samples=list(remove_samples or []),
                    )
                ).model_dump(mode="json", exclude_none=True)
        if step_override is not None and data.get("stepOverride") is None:
            data = {**data, "stepOverride": step_override}
        for json_key, flag in self.argv_map.items():
            if json_key in self.project_keys:
                if project_set:
                    continue
                val = self._project_path(data)
                cmd.extend([flag, val])
                project_set = True
                continue
            val = data.get(json_key)
            argv_val = self._argv_value(json_key, val)
            if argv_val is not None:
                cmd.extend([flag, argv_val])
        if not project_set and any(k in self.argv_map for k in self.project_keys):
            cmd.extend([self.argv_map[self.project_keys[0]], self._project_path(data)])
        return cmd

    @staticmethod
    def _format_subprocess_failure(cmd: List[str], proc: subprocess.CompletedProcess[str]) -> str:
        parts = [f"{cmd[0]} exited {proc.returncode}"]
        for label, text in (("stderr", proc.stderr), ("stdout", proc.stdout)):
            tail = (text or "").strip()
            if not tail:
                continue
            lines = tail.splitlines()
            if len(lines) > 40:
                tail = "\n".join(lines[-40:])
                parts.append(f"{label} (last 40 lines):\n{tail}")
            else:
                parts.append(f"{label}:\n{tail}")
        if len(parts) == 1:
            parts.append("no stderr/stdout captured")
        return "\n".join(parts)

    def execute(
        self,
        input_json: Mapping[str, Any],
        *,
        handle: Any = None,
    ) -> ActionExecutionResult:
        from ..execution_handle import WorkerStoppedError, bind_execution_handle, run_cancellable
        from ..task_validation import extract_runtime_input, strip_runtime_input

        payload = dict(input_json)
        input_model_cls = load_input_model(self.entry)
        runtime = extract_runtime_input(payload, input_model_cls)
        task_payload = strip_runtime_input(payload, input_model_cls)
        input_model = validate_input(self.entry, task_payload)
        argv_payload = {**input_model.model_dump(mode="json"), **runtime}
        timer = ExecutionTimer()
        cmd = self.build_argv(argv_payload)
        logger.info("Running: %s", " ".join(cmd))
        with bind_execution_handle(handle):
            try:
                proc = run_cancellable(cmd, handle=handle)
            except WorkerStoppedError:
                raise
        try:
            from methyl_utils.gpu_detection import cleanup_gpu_memory

            cleanup_gpu_memory()
        except Exception:
            pass
        finished_at, duration_ms = timer.finish()
        if proc.returncode != 0:
            raise RuntimeError(self._format_subprocess_failure(cmd, proc))
        collected = self.collector.collect(
            argv_payload,
            action_name=self.entry.action_name,
            stdout=proc.stdout or "",
        )
        collected.setdefault("tool", self.cli_tool)
        collected.setdefault("stdout_tail", (proc.stdout or "")[-500:])
        manifest_path = collected.get("manifest_path")
        output = finalize_output(
            self.entry,
            collected,
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=proc.returncode,
            manifest_path=str(manifest_path) if manifest_path else None,
        )
        return execution_result_from_output(output)


class InProcessAction:
    """Invoke a Python handler with validated input; returns typed output."""

    execution_mode = "in_process"

    def __init__(
        self,
        handler: InProcessCallable,
        *,
        entry: ActionCatalogEntry,
    ) -> None:
        self.handler = handler
        self.entry = entry

    def execute(
        self,
        input_json: Mapping[str, Any],
        *,
        handle: Any = None,
    ) -> ActionExecutionResult:
        from ..execution_handle import WorkerStoppedError, bind_execution_handle
        from ..task_validation import parse_task_envelope

        input_model, runtime = parse_task_envelope(
            self.entry.action_name,
            self.entry.capability,
            input_json,
        )
        if handle is not None and handle.is_cancelled:
            raise WorkerStoppedError()
        timer = ExecutionTimer()
        with bind_execution_handle(handle):
            raw = _call_in_process_handler(
                self.handler,
                self.entry.capability,
                self.entry.action_name,
                input_model,
                runtime,
            )
        if handle is not None and handle.is_cancelled:
            raise WorkerStoppedError()
        finished_at, duration_ms = timer.finish()
        if not isinstance(raw, BaseModel):
            raise TypeError(
                f"In-process handler for {self.entry.action_name!r} must return BaseModel, got {type(raw)!r}"
            )
        output = finalize_output(
            self.entry,
            raw.model_dump(mode="json"),
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=0,
            manifest_path=getattr(raw, "manifest_path", None),
        )
        return execution_result_from_output(output)


def build_action_from_catalog(entry: ActionCatalogEntry, handlers_module: Any) -> ActionBase:
    """Dispatch via catalog execution_mode + CLI provider registry (no action_name switch)."""
    if entry.execution_mode == "in_process":
        handler_name = entry.in_process_handler or entry.handler
        handler = getattr(handlers_module, handler_name, None)
        if handler is None or not callable(handler):
            raise RuntimeError(f"Missing in-process handler {handler_name!r} for {entry.action_name}")
        return InProcessAction(handler, entry=entry)

    from .registry import build_cli_action

    return build_cli_action(entry)
