"""Direct-DB action catalog seed helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List


def _dispatch_fields(action: dict[str, Any]) -> dict[str, Any]:
    argv_map = action.get("argv_map")
    dispatch = action.get("dispatch") if isinstance(action.get("dispatch"), dict) else {}
    max_per_worker = dispatch.get("max_per_worker")
    affinity_key_field = dispatch.get("affinity_key_field")
    return {
        "execution_mode": action.get("execution_mode"),
        "cli_tool": action.get("cli_tool"),
        "in_process_handler": action.get("in_process_handler"),
        "argv_map": dict(argv_map) if isinstance(argv_map, dict) else None,
        "max_per_worker": int(max_per_worker) if max_per_worker is not None else None,
        "exclusive_worker": bool(dispatch.get("exclusive_worker", False)),
        "affinity_key_field": str(affinity_key_field) if affinity_key_field else None,
        "prefer_previous_worker": bool(dispatch.get("prefer_previous_worker", False)),
        "prefer_continue_group": bool(dispatch.get("prefer_continue_group", False)),
    }


def seed_action_catalog(
    db: Any,
    body: Dict[str, Any],
    *,
    upsert_workflow_action: Callable[..., None],
    upsert_action_schema: Callable[..., None],
) -> Dict[str, Any]:
    catalog = body.get("catalog") if isinstance(body.get("catalog"), dict) else body
    actions: List[dict[str, Any]] = list(catalog.get("actions") or [])
    schemas: List[dict[str, Any]] = list(body.get("schemas") or [])

    action_count = 0
    errors: List[str] = []
    for action in actions:
        try:
            upsert_workflow_action(
                db,
                str(action["action_name"]),
                str(action.get("capability") or "") or None,
                str(action.get("schema_id") or action["action_name"]),
                **_dispatch_fields(action),
            )
            action_count += 1
        except Exception as exc:
            errors.append(f"{action.get('action_name')}: {exc}")

    schema_count = 0
    for item in schemas:
        try:
            upsert_action_schema(
                db,
                str(item["action_name"]),
                str(item["direction"]),
                dict(item["schema_json"]),
                str(item.get("schema_id") or item["action_name"]),
            )
            schema_count += 1
        except Exception as exc:
            errors.append(f"{item.get('action_name')}/{item.get('direction')}: {exc}")

    return {
        "actions_upserted": action_count,
        "schemas_upserted": schema_count,
        "errors": errors,
    }
