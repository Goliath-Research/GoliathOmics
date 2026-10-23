"""Mirror CAAS instance ledger rows into wf.execution_scope_action_entry."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def _run_key(input_json: Mapping[str, Any]) -> str:
    from methyl_worker.collectors import _run_key as collector_run_key

    return collector_run_key(input_json)


def sync_after_submit(db: Any, node_execution_id: int, result_code: int) -> None:
    """After a successful task submit, sync CAAS ledger entry to execution scope."""
    if result_code != 0:
        return
    get_ctx = getattr(db, "get_action_submit_context", None)
    upsert = getattr(db, "upsert_execution_scope_action_entry", None)
    if get_ctx is None or upsert is None:
        return

    ctx = get_ctx(node_execution_id)
    if not ctx:
        return

    scope_key = ctx.get("execution_scope_key")
    action_name = ctx.get("action_name")
    workflow_instance_id = ctx.get("workflow_instance_id")
    input_json = ctx.get("input_json") or {}
    if not scope_key or not action_name or workflow_instance_id is None:
        return

    from methyl_domain.content_store import caas_enabled, resolve_caas_root
    from methyl_domain.action_result import instance_ledger_path

    if not caas_enabled(input_json):
        return
    project_root = resolve_caas_root(input_json, action_name=str(action_name))
    if project_root is None:
        return

    ledger_path = instance_ledger_path(project_root, str(scope_key))
    if not ledger_path.is_file():
        return

    try:
        with open(ledger_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        logger.debug("execution scope hook: ledger read failed", exc_info=True)
        return

    run_key = _run_key(input_json)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return
    entry = entries.get(f"{action_name}:{run_key}")
    if not isinstance(entry, dict):
        return
    content_key = entry.get("content_key")
    if not content_key:
        return

    try:
        upsert(
            set_key=str(scope_key),
            workflow_instance_id=int(workflow_instance_id),
            action_name=str(action_name),
            run_key=str(run_key),
            content_key=str(content_key),
        )
    except Exception:
        logger.debug("execution scope hook: upsert failed", exc_info=True)
