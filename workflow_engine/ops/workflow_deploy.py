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
        delete_instances = bool(body.get("delete_instances", True))
        if hasattr(db, "_connection"):
            replace_delete_workflow_graph(db, name, delete_instances)
        else:
            try:
                delete_workflow_definition(db, name, delete_instances)
            except Exception as exc:  # noqa: BLE001 — gateway raises DatabaseError/KeyError
                msg = str(exc).lower()
                if "not found" not in msg and "does not exist" not in msg:
                    raise

    return create_workflow_definition(db, spec)


_DELETE_EDGES_SQL = """
DELETE FROM wf.workflow_edge e
USING wf.workflow_node n
JOIN wf.workflow_version v ON v.id = n.workflow_version_id
JOIN wf.workflow_def d ON d.id = v.workflow_def_id
WHERE d.name = %s
  AND (e.parent_node_id = n.id OR e.child_node_id = n.id)
"""


def replace_delete_workflow_graph(
    db: Any, name: str, delete_instances: bool
) -> Dict[str, int]:
    """Delete a workflow definition graph in FK order within one transaction.

    wf.workflow_edge references wf.workflow_node without ON DELETE CASCADE, so
    sp_delete_workflow_def (which deletes versions -> cascades nodes) fails
    while edges remain. Delete the edges first, then call the procedure, all
    on one connection/transaction. A missing definition is a no-op.
    """
    with db._connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM wf.workflow_def WHERE name = %s", (name,))
            if cur.fetchone() is None:
                conn.rollback()
                return {"deleted_instance_count": 0, "deleted_version_count": 0}
            cur.execute(_DELETE_EDGES_SQL, (name,))
            cur.execute(
                "SELECT * FROM wf.sp_delete_workflow_def(NULL, %s, %s)",
                (name, delete_instances),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return {"deleted_instance_count": 0, "deleted_version_count": 0}
    if isinstance(row, dict):
        return {
            "deleted_instance_count": int(row["deleted_instance_count"]),
            "deleted_version_count": int(row["deleted_version_count"]),
        }
    return {"deleted_instance_count": int(row[0]), "deleted_version_count": int(row[1])}


def list_workflow_definitions(
    db: Any, *, source: Optional[str] = None
) -> Dict[str, Any]:
    if source is not None and source not in ("system", "portal"):
        raise ValueError("source must be 'system' or 'portal'")
    rows = db.list_workflow_definitions(source_filter=source)
    return {"definitions": rows}


def get_workflow_definition_by_name(db: Any, name: str) -> Dict[str, Any]:
    return db.get_workflow_definition_by_name(name)
