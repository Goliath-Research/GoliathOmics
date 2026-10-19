"""Direct-DB workflow definition deploy helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ops._paths import ensure_import_paths


def deploy_workflow_definition(
    db: Any,
    body: Dict[str, Any],
    *,
    create_workflow_definition: Callable[..., Dict[str, Any]],
    delete_workflow_definition: Callable[..., Dict[str, int]],
) -> Dict[str, Any]:
    ensure_import_paths()
    from workflow_definition_spec import WorkflowDefinitionSpec

    replace = bool(body.get("replace", False))
    spec = body.get("spec")

    if spec is None:
        spec = WorkflowDefinitionSpec.model_validate(body).to_db_spec()
    elif isinstance(spec, dict) and "nodes" in spec:
        spec = WorkflowDefinitionSpec.model_validate(spec).to_db_spec()
    else:
        raise ValueError("spec is required (compiled WorkflowDefinitionSpec JSON)")

    name = str(spec.get("name") or "")
    if not name:
        raise ValueError("workflow spec missing name")

    if replace:
        # sp_delete_workflow_def RAISES when the name is absent; first-time
        # catalog sync must still create. Treat not-found as a no-op.
        # With delete_instances=false some gateway builds leave workflow_edge
        # rows and fail FK when deleting workflow_version — retry with True.
        delete_instances = bool(body.get("delete_instances", True))
        try:
            delete_workflow_definition(db, name, delete_instances)
        except Exception as exc:  # noqa: BLE001 — gateway raises DatabaseError/KeyError
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg:
                pass
            elif (not delete_instances) and (
                "foreign key" in msg or "workflow_edge" in msg
            ):
                delete_workflow_definition(db, name, True)
            else:
                raise

    return create_workflow_definition(db, spec)


def list_workflow_definitions(
    db: Any, *, source: Optional[str] = None
) -> Dict[str, Any]:
    if source is not None and source not in ("system", "portal"):
        raise ValueError("source must be 'system' or 'portal'")
    rows = db.list_workflow_definitions(source_filter=source)
    return {"definitions": rows}


def get_workflow_definition_by_name(db: Any, name: str) -> Dict[str, Any]:
    return db.get_workflow_definition_by_name(name)
