"""Shared action result telemetry and manifest I/O for workflow workers and CLIs."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T", bound=BaseModel)


class ArtifactRef(BaseModel):
    """Reference to a file or object written under shared storage (/work)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    kind: str = "file"
    bytes: Optional[int] = None
    sha256: Optional[str] = None


class ActionTelemetry(BaseModel):
    """Timing, branch code, and artifact index for one ACTION execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    action_name: Optional[str] = None
    capability: Optional[str] = None
    started_at_utc: datetime
    finished_at_utc: datetime
    duration_ms: int
    result_code: int = 0
    exit_code: int = 0
    manifest_path: Optional[str] = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class ActionExecutionRecord(BaseModel):
    """Persisted idempotency manifest for workflow ACTION skip/replay."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1", "1.2"] = "1.2"
    action_name: str
    capability: str
    started_at_utc: datetime
    finished_at_utc: datetime
    duration_ms: int
    result_code: int = 0
    exit_code: int = 0
    manifest_path: Optional[str] = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    action_revision: str
    input_signature: str
    output_signature: str
    content_key: Optional[str] = None
    hyperparam_set_id: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    task_output: dict = Field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def action_results_dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / ".action_results"


def manifest_path_for(
    output_dir: Path | str,
    action_name: str,
    run_key: str = "default",
) -> Path:
    safe_action = action_name.replace(".", "_")
    safe_key = run_key.replace("/", "_").replace(" ", "_")
    return action_results_dir(output_dir) / f"{safe_action}.{safe_key}.json"


def caas_root(project_root: Path | str) -> Path:
    """Content-addressed action store root under a project output tree."""
    return Path(project_root) / ".caas"


def caas_action_safe_name(action_name: str) -> str:
    return action_name.replace(".", "_")


def caas_entry_dir(
    project_root: Path | str,
    action_name: str,
    content_key: str,
) -> Path:
    """Directory holding one immutable CAAS entry for an action result."""
    return caas_root(project_root) / caas_action_safe_name(action_name) / content_key


def caas_entry_manifest_path(
    project_root: Path | str,
    action_name: str,
    content_key: str,
) -> Path:
    return caas_entry_dir(project_root, action_name, content_key) / "manifest.json"


def instance_ledger_path(project_root: Path | str, hyperparam_set_id: str) -> Path:
    """Per-hyperparameter-set ledger mapping action+run_key to content_key."""
    safe_id = hyperparam_set_id.replace("/", "_").replace(" ", "_")
    return caas_root(project_root) / "instances" / f"{safe_id}.json"


def atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_action_result(path: Path | str, model: BaseModel) -> Path:
    path = Path(path)
    payload = model.model_dump(mode="json")
    atomic_write_json(path, payload)
    return path


def read_action_result(path: Path | str, model: Type[T]) -> T:
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return model.model_validate(data)


def artifact_ref_for(path: Path | str, *, kind: str = "file") -> ArtifactRef:
    p = Path(path)
    size = p.stat().st_size if p.is_file() else None
    return ArtifactRef(path=str(p.resolve()), kind=kind, bytes=size)
