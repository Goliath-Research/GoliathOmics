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
            kept = replace_delete_workflow_graph(db, name, delete_instances)
            created = create_workflow_definition(db, spec)
            if kept["kept_version_count"]:
                return attach_new_version(db, created, int(kept["workflow_def_id"]))
            return created
        try:
            delete_workflow_definition(db, name, delete_instances)
        except Exception as exc:  # noqa: BLE001 — gateway raises DatabaseError/KeyError
            msg = str(exc).lower()
            if "not found" not in msg and "does not exist" not in msg:
                raise

    return create_workflow_definition(db, spec)


# Versions of the definition that no workflow_instance references. With
# delete_instances=false only these are removed; referenced versions (and the
# nodes/edges their instances' node_execution rows point at) stay.
_UNREFERENCED_VERSIONS_SQL = """
SELECT v.id
FROM wf.workflow_version v
WHERE v.workflow_def_id = %s
  AND NOT EXISTS (
    SELECT 1 FROM wf.workflow_instance wi WHERE wi.workflow_version_id = v.id
  )
"""

# wf.workflow_edge references wf.workflow_node without ON DELETE CASCADE, so
# edges go first; deleting the version then cascades its nodes.
_DELETE_EDGES_SQL = """
DELETE FROM wf.workflow_edge e
USING wf.workflow_node n
WHERE n.workflow_version_id = ANY(%s)
  AND (e.parent_node_id = n.id OR e.child_node_id = n.id)
"""

_DELETE_VERSIONS_SQL = "DELETE FROM wf.workflow_version WHERE id = ANY(%s)"

_DEACTIVATE_VERSIONS_SQL = (
    "UPDATE wf.workflow_version SET is_active = false WHERE workflow_def_id = %s"
)

_ALL_VERSIONS_SQL = "SELECT v.id FROM wf.workflow_version v WHERE v.workflow_def_id = %s"


def _col(row: Any, key: str, idx: int = 0) -> Any:
    return row[key] if isinstance(row, dict) else row[idx]


def replace_delete_workflow_graph(
    db: Any, name: str, delete_instances: bool
) -> Dict[str, int]:
    """Remove a workflow definition graph in FK order within one transaction.

    delete_instances=True: delete every version's edges, then call
    wf.sp_delete_workflow_def (instances, versions -> cascaded nodes, def).

    delete_instances=False: delete only versions no instance references (edges
    first, then the version, which cascades its nodes). Versions still used by
    instances keep their nodes and edges and are deactivated; the definition
    row stays so the caller registers the new graph as another version of it.
    A missing definition is a no-op.
    """
    result = {
        "workflow_def_id": 0,
        "deleted_instance_count": 0,
        "deleted_version_count": 0,
        "kept_version_count": 0,
    }
    with db._connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM wf.workflow_def WHERE name = %s", (name,))
            def_row = cur.fetchone()
            if def_row is None:
                conn.rollback()
                return result
            def_id = int(_col(def_row, "id"))
            result["workflow_def_id"] = def_id

            if delete_instances:
                cur.execute(_ALL_VERSIONS_SQL, (def_id,))
                version_ids = [int(_col(r, "id")) for r in cur.fetchall()]
                cur.execute(_DELETE_EDGES_SQL, (version_ids,))
                cur.execute(
                    "SELECT * FROM wf.sp_delete_workflow_def(%s, NULL, %s)",
                    (def_id, True),
                )
                row = cur.fetchone()
                if row:
                    result["deleted_instance_count"] = int(
                        _col(row, "deleted_instance_count", 0)
                    )
                    result["deleted_version_count"] = int(
                        _col(row, "deleted_version_count", 1)
                    )
            else:
                cur.execute(_ALL_VERSIONS_SQL, (def_id,))
                all_ids = [int(_col(r, "id")) for r in cur.fetchall()]
                cur.execute(_UNREFERENCED_VERSIONS_SQL, (def_id,))
                drop_ids = [int(_col(r, "id")) for r in cur.fetchall()]
                kept = len(all_ids) - len(drop_ids)
                if drop_ids:
                    cur.execute(_DELETE_EDGES_SQL, (drop_ids,))
                    cur.execute(_DELETE_VERSIONS_SQL, (drop_ids,))
                if kept:
                    cur.execute(_DEACTIVATE_VERSIONS_SQL, (def_id,))
                else:
                    cur.execute("DELETE FROM wf.workflow_def WHERE id = %s", (def_id,))
                result["deleted_version_count"] = len(drop_ids)
                result["kept_version_count"] = kept
        conn.commit()
    return result


_ATTACH_VERSION_SQL = """
UPDATE wf.workflow_version
SET workflow_def_id = %s,
    version_major = (
      SELECT COALESCE(MAX(version_major), 0) + 1
      FROM wf.workflow_version WHERE workflow_def_id = %s
    ),
    version_minor = 0,
    is_active = true
WHERE id = %s
"""


def attach_new_version(
    db: Any, created: Dict[str, Any], workflow_def_id: int
) -> Dict[str, Any]:
    """Move a freshly created graph's version under the kept definition.

    wf_repo_create_workflow_graph always inserts a new wf.workflow_def row.
    When older versions survive (instances reference them), the new graph
    becomes the next major version of the existing definition and the
    temporary definition row is removed, so the name maps to one definition.
    """
    new_def_id = int(created["workflow_def_id"])
    version_id = int(created["workflow_version_id"])
    if new_def_id == workflow_def_id:
        return created
    with db._connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _ATTACH_VERSION_SQL, (workflow_def_id, workflow_def_id, version_id)
            )
            cur.execute("DELETE FROM wf.workflow_def WHERE id = %s", (new_def_id,))
        conn.commit()
    return {**created, "workflow_def_id": workflow_def_id}


def list_workflow_definitions(
    db: Any, *, source: Optional[str] = None
) -> Dict[str, Any]:
    if source is not None and source not in ("system", "portal"):
        raise ValueError("source must be 'system' or 'portal'")
    rows = db.list_workflow_definitions(source_filter=source)
    return {"definitions": rows}


def get_workflow_definition_by_name(db: Any, name: str) -> Dict[str, Any]:
    return db.get_workflow_definition_by_name(name)
