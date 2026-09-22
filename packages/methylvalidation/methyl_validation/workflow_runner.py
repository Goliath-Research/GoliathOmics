"""Run methyl-validation stages via the workflow engine REST gateway."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from methyl_worker.client import WorkflowRestClient

from .workflow_planner import ValidationPlanRequest, plan_validation_context


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_action_catalog() -> Dict[str, Dict[str, Any]]:
    catalog_path = _repo_root() / "schemas" / "actions" / "catalog.json"
    if not catalog_path.is_file():
        return {}
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    return {str(a["action_name"]): a for a in data.get("actions") or []}


def use_local_pipeline() -> bool:
    return os.environ.get("METHYL_USE_LOCAL_PIPELINE", "").lower() in {"1", "true", "yes"}


def enrich_context_for_engine(context_json: Dict[str, Any]) -> Dict[str, Any]:
    import sys

    domain = _repo_root() / "workflow_engine" / "domain"
    if str(domain) not in sys.path:
        sys.path.insert(0, str(domain))
    from workflow_context import enrich_instance_context

    return enrich_instance_context(context_json)


def start_workflow_instance(
    *,
    gateway_url: str,
    workflow_version_id: int,
    context_json: Dict[str, Any],
    start: bool = True,
) -> int:
    client = WorkflowRestClient(gateway_url)
    payload = enrich_context_for_engine(context_json)
    body = client.create_workflow_instance(workflow_version_id, payload, start=start)
    instance_id = int(body.get("id") or body.get("workflow_instance_id"))
    return instance_id


def wait_for_instance(
    *,
    gateway_url: str,
    instance_id: int,
    poll_seconds: float = 5.0,
    timeout_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    client = WorkflowRestClient(gateway_url)
    deadline = time.time() + timeout_seconds if timeout_seconds else None
    while True:
        summary = client.get_workflow_instance(instance_id)
        status = str(summary.get("status") or "").upper()
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return summary
        if deadline is not None and time.time() >= deadline:
            raise TimeoutError(f"workflow instance {instance_id} did not finish within {timeout_seconds}s")
        time.sleep(poll_seconds)


def run_validation_via_workflow(
    *,
    gateway_url: str,
    workflow_version_id: int,
    plan_request: ValidationPlanRequest | Dict[str, Any],
    poll_seconds: float = 5.0,
) -> Dict[str, Any]:
    """
    Plan Monte Carlo iterations, start ValidationPipeline instance, wait for completion.
    """
    context = plan_validation_context(plan_request)
    context_json = context.model_dump(mode="json")
    instance_id = start_workflow_instance(
        gateway_url=gateway_url,
        workflow_version_id=workflow_version_id,
        context_json=context_json,
    )
    summary = wait_for_instance(
        gateway_url=gateway_url,
        instance_id=instance_id,
        poll_seconds=poll_seconds,
    )
    if str(summary.get("status")).upper() != "COMPLETED":
        raise RuntimeError(
            f"ValidationPipeline instance {instance_id} ended with status {summary.get('status')!r}"
        )
    return {"instance_id": instance_id, "context_json": context_json, "summary": summary}


def write_planned_context(
    plan_request: ValidationPlanRequest | Dict[str, Any],
    output_path: Path,
) -> Dict[str, Any]:
    context = plan_validation_context(plan_request)
    enriched = enrich_context_for_engine(context.model_dump(mode="json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, indent=2) + "\n", encoding="utf-8")
    return enriched
