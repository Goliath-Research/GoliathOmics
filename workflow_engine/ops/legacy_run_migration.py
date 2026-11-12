"""Copy completed workflow runs from a legacy GoliathOmics database into another one.

The source database is opened read only. Every write to the target happens in
one transaction, guarded by an advisory lock, and is rolled back unless the
caller asks for apply. Reruns are no-ops: each copied instance carries a
``legacy.migration.source`` instance extension naming the source label and
source instance id, and already-copied ids are skipped.

Workflow versions are matched by definition name plus a structural fingerprint
of the graph (node keys and types, action names, edges, templates, bindings),
never by numeric id. When the target has no version with the same fingerprint,
the legacy graph is created through ``wf.wf_repo_create_workflow_graph`` as an
inactive version ``0.<major*1000+minor>`` of the target definition so it never
replaces or outranks the active version.

Run history has no official engine API (starting an instance would execute it),
so instances, node executions, execution context, loop state and scope
variables are inserted directly into the ``wf`` tables. See
docs/operators/legacy-run-migration.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROVENANCE_KEY = "legacy.migration.source"
LOCK_KEY = "goliath_omics.legacy_run_migration"
TOOL_NAME = "scripts/migrate_legacy_sampleprep_runs.py"
LEGACY_VERSION_MAJOR = 0

NODE_FIELDS: Tuple[str, ...] = (
    "repeat_count",
    "condition_ref_node_key",
    "switch_ref_node_key",
    "condition_var",
    "switch_var",
    "foreach_collection_var",
    "foreach_item_var",
    "foreach_index_var",
    "foreach_parallel",
    "wait_event_type",
    "wait_correlation_var",
    "schedule_interval_seconds",
)

# Tables that reference wf.workflow_instance which this tool does not copy.
# Rows there for a selected instance stop the migration instead of being lost.
UNSUPPORTED_INSTANCE_TABLES: Tuple[Tuple[str, str], ...] = (
    ("wf.instance_cursor", "workflow_instance_id"),
    ("wf.execution_scope_action_entry", "workflow_instance_id"),
    ("cfg.study_instance_link", "workflow_instance_id"),
    ("cfg.hyperparameter_trial", "workflow_instance_id"),
    ("wf.event_delivery", "workflow_instance_id"),
    ("wf.test_bed_run", "workflow_instance_id"),
    ("wf.test_bed_task_log", "workflow_instance_id"),
)


class MigrationError(RuntimeError):
    """Raised when the source data cannot be copied safely."""


# --------------------------------------------------------------------------
# Pure helpers (unit tested without a database)
# --------------------------------------------------------------------------


@dataclass
class VersionGraph:
    """One workflow version's graph keyed by node_key (ids are not portable)."""

    version_id: int
    def_name: str
    version_major: int
    version_minor: int
    description: Optional[str]
    root_node_key: Optional[str]
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    node_id_to_key: Dict[int, str] = field(default_factory=dict)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    collection_bindings: List[Dict[str, Any]] = field(default_factory=list)
    event_triggers: List[Dict[str, Any]] = field(default_factory=list)


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sorted_dicts(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return sorted((dict(i) for i in items), key=_canon)


def graph_fingerprint(graph: VersionGraph) -> str:
    """sha256 over the id-free structure of a version graph."""
    nodes = []
    for key in sorted(graph.nodes):
        n = graph.nodes[key]
        nodes.append(
            {
                "node_key": key,
                "node_type": n.get("node_type"),
                "action_name": n.get("action_name"),
                **{f: n.get(f) for f in NODE_FIELDS},
                "input_template": n.get("input_template"),
                "input_bindings": _sorted_dicts(n.get("input_bindings") or []),
                "output_bindings": _sorted_dicts(n.get("output_bindings") or []),
                "scope_defaults": _sorted_dicts(n.get("scope_defaults") or []),
            }
        )
    payload = {
        "name": graph.def_name,
        "root_node_key": graph.root_node_key,
        "nodes": nodes,
        "edges": _sorted_dicts(graph.edges),
        "collection_bindings": _sorted_dicts(graph.collection_bindings),
        "event_triggers": _sorted_dicts(graph.event_triggers),
    }
    return hashlib.sha256(_canon(payload).encode("utf-8")).hexdigest()


def legacy_version_minor(major: int, minor: int) -> int:
    """Minor number for an imported legacy version (major is always 0)."""
    return int(major) * 1000 + int(minor)


def build_graph_spec(graph: VersionGraph, *, version_minor: int, source_label: str) -> Dict[str, Any]:
    """WorkflowDefinitionSpec-shaped JSON for wf.wf_repo_create_workflow_graph."""
    if not graph.root_node_key:
        raise MigrationError(f"version {graph.version_id} has no root node")
    nodes: List[Dict[str, Any]] = []
    input_bindings: List[Dict[str, Any]] = []
    output_bindings: List[Dict[str, Any]] = []
    scope_defaults: List[Dict[str, Any]] = []
    for key in sorted(graph.nodes):
        n = graph.nodes[key]
        node: Dict[str, Any] = {"node_key": key, "node_type": n["node_type"]}
        if n.get("action_name"):
            node["action_name"] = n["action_name"]
        for f in NODE_FIELDS:
            if n.get(f) is not None:
                node[f] = n[f]
        if n.get("input_template") is not None:
            node["input_template"] = n["input_template"]
        nodes.append(node)
        for b in n.get("input_bindings") or []:
            input_bindings.append({"node_key": key, **b})
        for b in n.get("output_bindings") or []:
            output_bindings.append({"node_key": key, **b})
        for b in n.get("scope_defaults") or []:
            scope_defaults.append({"node_key": key, **b})
    collection = [
        {
            "bind_order": c.get("bind_order"),
            "scope_var": c.get("scope_var"),
            "kind": c.get("source_kind"),
            "path_var": c.get("path_var"),
            "base_var": c.get("base_var"),
            "json_path": c.get("json_path"),
        }
        for c in graph.collection_bindings
    ]
    return {
        "name": graph.def_name,
        "description": (
            f"Legacy import of {graph.def_name} v{graph.version_major}.{graph.version_minor} "
            f"(source {source_label} workflow_version_id {graph.version_id})"
        ),
        "root_node_key": graph.root_node_key,
        "version_major": LEGACY_VERSION_MAJOR,
        "version_minor": version_minor,
        "is_active": False,
        "nodes": nodes,
        "edges": list(graph.edges),
        "input_bindings": input_bindings,
        "output_bindings": output_bindings,
        "scope_defaults": scope_defaults,
        "collection_bindings": collection,
        "event_triggers": list(graph.event_triggers),
    }


def order_node_executions(rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Parents before children so parent_node_execution_id can be remapped on insert."""
    by_id = {int(r["id"]): r for r in rows}
    ordered: List[Mapping[str, Any]] = []
    done: set[int] = set()
    visiting: set[int] = set()

    def visit(rid: int) -> None:
        if rid in done:
            return
        if rid in visiting:
            raise MigrationError(f"node_execution parent cycle at id {rid}")
        visiting.add(rid)
        parent = by_id[rid].get("parent_node_execution_id")
        if parent is not None and int(parent) in by_id:
            visit(int(parent))
        visiting.discard(rid)
        done.add(rid)
        ordered.append(by_id[rid])

    for rid in sorted(by_id):
        visit(rid)
    return ordered


def remap_scope_node_execution_id(value: Any, ne_map: Mapping[int, int]) -> int:
    """Scope variables use 0 for instance scope; other ids must be copied executions."""
    v = int(value)
    if v == 0:
        return 0
    if v not in ne_map:
        raise MigrationError(f"scope variable references unknown node_execution {v}")
    return ne_map[v]


def instance_labels(context_json: Any) -> Dict[str, Any]:
    """Study label and sample ids exactly as recorded in the instance context."""
    ctx = context_json if isinstance(context_json, dict) else {}
    project = ctx.get("project") if isinstance(ctx.get("project"), dict) else {}
    samples = ctx.get("samples") if isinstance(ctx.get("samples"), list) else []
    return {
        "study_label": project.get("project_name"),
        "sample_ids": [s.get("sampleId") for s in samples if isinstance(s, dict) and s.get("sampleId")],
    }


def match_versions(
    legacy: Mapping[int, str], target: Mapping[int, str]
) -> Dict[int, Optional[int]]:
    """Legacy version id -> target version id with the same fingerprint (or None)."""
    by_fp: Dict[str, int] = {}
    for vid in sorted(target):
        by_fp.setdefault(target[vid], vid)
    return {vid: by_fp.get(fp) for vid, fp in legacy.items()}


def next_free_minor(wanted: int, taken: Iterable[int]) -> int:
    taken_set = set(int(t) for t in taken)
    m = wanted
    while m in taken_set:
        m += 1
    return m


# --------------------------------------------------------------------------
# Database access (psycopg 3, dict rows)
# --------------------------------------------------------------------------


def _rows(cur: Any, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _table_columns(cur: Any, qualified: str) -> List[str]:
    schema, table = qualified.split(".", 1)
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [r["column_name"] for r in cur.fetchall()]


def _table_exists(cur: Any, qualified: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (qualified,))
    return bool(cur.fetchone()["ok"])


def _select_cols(available: Sequence[str], wanted: Sequence[str], alias: str) -> str:
    parts = []
    for c in wanted:
        parts.append(f'{alias}."{c}"' if c in available else f'NULL AS "{c}"')
    return ", ".join(parts)


def load_version_graphs(cur: Any, version_ids: Sequence[int]) -> Dict[int, VersionGraph]:
    if not version_ids:
        return {}
    ids = [int(v) for v in version_ids]
    graphs: Dict[int, VersionGraph] = {}
    for r in _rows(
        cur,
        """
        SELECT v.id, v.version_major, v.version_minor, v.root_node_id, d.name, d.description
        FROM wf.workflow_version v JOIN wf.workflow_def d ON d.id = v.workflow_def_id
        WHERE v.id = ANY(%s)
        """,
        (ids,),
    ):
        graphs[int(r["id"])] = VersionGraph(
            version_id=int(r["id"]),
            def_name=r["name"],
            version_major=int(r["version_major"]),
            version_minor=int(r["version_minor"]),
            description=r["description"],
            root_node_key=None,
        )
    root_ids = {
        int(r["id"]): r["root_node_id"]
        for r in _rows(cur, "SELECT id, root_node_id FROM wf.workflow_version WHERE id = ANY(%s)", (ids,))
    }
    node_cols = _table_columns(cur, "wf.workflow_node")
    sel = _select_cols(node_cols, NODE_FIELDS, "n")
    for r in _rows(
        cur,
        f"""
        SELECT n.id, n.workflow_version_id, n.node_type, n.node_key, a.action_name, {sel}
        FROM wf.workflow_node n
        LEFT JOIN wf.workflow_action a ON a.id = n.workflow_action_id
        WHERE n.workflow_version_id = ANY(%s)
        """,
        (ids,),
    ):
        g = graphs[int(r["workflow_version_id"])]
        g.node_id_to_key[int(r["id"])] = r["node_key"]
        g.nodes[r["node_key"]] = {
            "node_type": r["node_type"],
            "action_name": r["action_name"],
            **{f: r[f] for f in NODE_FIELDS},
            "input_template": None,
            "input_bindings": [],
            "output_bindings": [],
            "scope_defaults": [],
        }
    for vid, g in graphs.items():
        rid = root_ids.get(vid)
        g.root_node_key = g.node_id_to_key.get(int(rid)) if rid is not None else None

    node_to_graph: Dict[int, VersionGraph] = {}
    for g in graphs.values():
        for nid in g.node_id_to_key:
            node_to_graph[nid] = g
    node_ids = list(node_to_graph)

    def node_of(nid: Any) -> Tuple[VersionGraph, str]:
        g = node_to_graph[int(nid)]
        return g, g.node_id_to_key[int(nid)]

    for r in _rows(
        cur,
        "SELECT workflow_node_id, template_json FROM wf.workflow_input_template WHERE workflow_node_id = ANY(%s)",
        (node_ids,),
    ):
        g, key = node_of(r["workflow_node_id"])
        g.nodes[key]["input_template"] = r["template_json"]
    for r in _rows(
        cur,
        """SELECT workflow_node_id, target_json_path, source_expr, is_required
           FROM wf.workflow_input_binding WHERE workflow_node_id = ANY(%s)""",
        (node_ids,),
    ):
        g, key = node_of(r["workflow_node_id"])
        g.nodes[key]["input_bindings"].append(
            {"target_json_path": r["target_json_path"], "source_expr": r["source_expr"], "is_required": bool(r["is_required"])}
        )
    for r in _rows(
        cur,
        """SELECT workflow_node_id, var_name, source_kind, source_json_path
           FROM wf.variable_output_binding WHERE workflow_node_id = ANY(%s)""",
        (node_ids,),
    ):
        g, key = node_of(r["workflow_node_id"])
        g.nodes[key]["output_bindings"].append(
            {"var_name": r["var_name"], "source_kind": r["source_kind"], "source_json_path": r["source_json_path"]}
        )
    for r in _rows(
        cur,
        "SELECT workflow_node_id, var_name, default_expr FROM wf.node_scope_default WHERE workflow_node_id = ANY(%s)",
        (node_ids,),
    ):
        g, key = node_of(r["workflow_node_id"])
        g.nodes[key]["scope_defaults"].append({"var_name": r["var_name"], "default_expr": r["default_expr"]})
    for r in _rows(
        cur,
        """SELECT parent_node_id, child_node_id, child_order, branch_kind, condition_expr,
                  switch_case_value, is_default
           FROM wf.workflow_edge WHERE parent_node_id = ANY(%s)""",
        (node_ids,),
    ):
        g, parent_key = node_of(r["parent_node_id"])
        _g2, child_key = node_of(r["child_node_id"])
        g.edges.append(
            {
                "parent_node_key": parent_key,
                "child_node_key": child_key,
                "child_order": r["child_order"],
                "branch_kind": r["branch_kind"],
                "condition_expr": r["condition_expr"],
                "switch_case_value": r["switch_case_value"],
                "is_default": bool(r["is_default"]),
            }
        )
    for r in _rows(
        cur,
        """SELECT workflow_version_id, bind_order, scope_var, source_kind, path_var, base_var, json_path
           FROM wf.workflow_collection_binding WHERE workflow_version_id = ANY(%s)""",
        (ids,),
    ):
        graphs[int(r["workflow_version_id"])].collection_bindings.append(
            {k: r[k] for k in ("bind_order", "scope_var", "source_kind", "path_var", "base_var", "json_path")}
        )
    if _table_exists(cur, "wf.event_subscription") and _table_exists(cur, "wf.event_type"):
        for r in _rows(
            cur,
            """SELECT s.workflow_version_id, t.name AS event, s.mode, s.filter_json, s.enabled
               FROM wf.event_subscription s JOIN wf.event_type t ON t.id = s.event_type_id
               WHERE s.workflow_version_id = ANY(%s)""",
            (ids,),
        ):
            graphs[int(r["workflow_version_id"])].event_triggers.append(
                {"event": r["event"], "mode": r["mode"], "filter_json": r["filter_json"], "enabled": bool(r["enabled"])}
            )
    return graphs


@dataclass
class SourceRuns:
    instances: List[Dict[str, Any]]
    graphs: Dict[int, VersionGraph]
    scopes: Dict[int, Dict[str, Any]]
    node_executions: List[Dict[str, Any]]
    execution_context: List[Dict[str, Any]]
    loop_state: List[Dict[str, Any]]
    scope_variables: List[Dict[str, Any]]
    instance_extensions: List[Dict[str, Any]]

    def counts(self) -> Dict[str, int]:
        return {
            "workflow_instance": len(self.instances),
            "workflow_version": len(self.graphs),
            "execution_scope": len(self.scopes),
            "node_execution": len(self.node_executions),
            "execution_context": len(self.execution_context),
            "loop_state": len(self.loop_state),
            "scope_variable": len(self.scope_variables),
            "instance_extension": len(self.instance_extensions),
        }


def assert_read_only(cur: Any) -> None:
    cur.execute("SHOW transaction_read_only")
    row = cur.fetchone()
    value = row["transaction_read_only"] if isinstance(row, dict) else row[0]
    if str(value).lower() != "on":
        raise MigrationError("source session is not read only; refusing to continue")


def load_source_runs(
    cur: Any,
    instance_ids: Sequence[int],
    *,
    workflow_name: str,
    require_status: str = "COMPLETED",
) -> SourceRuns:
    assert_read_only(cur)
    ids = sorted(int(i) for i in instance_ids)
    instances = _rows(
        cur,
        """
        SELECT wi.id, wi.workflow_version_id, wi.status, wi.context_json,
               wi.started_at_utc, wi.completed_at_utc, wi.execution_scope_id, d.name AS def_name
        FROM wf.workflow_instance wi
        JOIN wf.workflow_version v ON v.id = wi.workflow_version_id
        JOIN wf.workflow_def d ON d.id = v.workflow_def_id
        WHERE wi.id = ANY(%s)
        ORDER BY wi.id
        """,
        (ids,),
    )
    found = {int(r["id"]) for r in instances}
    missing = [i for i in ids if i not in found]
    if missing:
        raise MigrationError(f"source instances not found: {missing}")
    for r in instances:
        if r["def_name"] != workflow_name:
            raise MigrationError(f"instance {r['id']} is {r['def_name']}, expected {workflow_name}")
        if require_status and r["status"] != require_status:
            raise MigrationError(f"instance {r['id']} status {r['status']}, expected {require_status}")
    for table, col in UNSUPPORTED_INSTANCE_TABLES:
        if _table_exists(cur, table):
            cur.execute(f"SELECT count(*) AS c FROM {table} WHERE {col} = ANY(%s)", (ids,))
            c = int(cur.fetchone()["c"])
            if c:
                raise MigrationError(f"{table} has {c} row(s) for the selected instances; not supported")
    graphs = load_version_graphs(cur, sorted({int(r["workflow_version_id"]) for r in instances}))
    scope_ids = sorted({int(r["execution_scope_id"]) for r in instances if r["execution_scope_id"] is not None})
    scopes = {
        int(r["id"]): r
        for r in _rows(
            cur,
            "SELECT id, set_key, display_name, config_json FROM wf.execution_scope WHERE id = ANY(%s)",
            (scope_ids,),
        )
    }
    ne_cols = [c for c in _table_columns(cur, "wf.node_execution")]
    node_executions = _rows(
        cur,
        f"SELECT {', '.join(chr(34) + c + chr(34) for c in ne_cols)} FROM wf.node_execution "
        "WHERE workflow_instance_id = ANY(%s) ORDER BY id",
        (ids,),
    )
    ne_ids = [int(r["id"]) for r in node_executions]
    execution_context = _rows(
        cur,
        "SELECT id, node_execution_id, context_key, context_value_json FROM wf.execution_context "
        "WHERE node_execution_id = ANY(%s) ORDER BY id",
        (ne_ids,),
    )
    loop_state = _rows(
        cur,
        "SELECT id, workflow_instance_id, control_node_id, scope_node_execution_id, current_iteration, "
        "repeat_target_count FROM wf.loop_state WHERE workflow_instance_id = ANY(%s) ORDER BY id",
        (ids,),
    )
    scope_variables = _rows(
        cur,
        "SELECT workflow_instance_id, scope_node_execution_id, var_name, value_json, updated_at_utc "
        "FROM wf.scope_variable WHERE workflow_instance_id = ANY(%s) "
        "ORDER BY workflow_instance_id, scope_node_execution_id, var_name",
        (ids,),
    )
    instance_extensions = _rows(
        cur,
        "SELECT workflow_instance_id, extension_key, data_json FROM wf.instance_extension "
        "WHERE workflow_instance_id = ANY(%s) ORDER BY workflow_instance_id, extension_key",
        (ids,),
    )
    return SourceRuns(
        instances=instances,
        graphs=graphs,
        scopes=scopes,
        node_executions=node_executions,
        execution_context=execution_context,
        loop_state=loop_state,
        scope_variables=scope_variables,
        instance_extensions=instance_extensions,
    )


def database_identity(cur: Any) -> Tuple[str, str]:
    """(database name, cluster id) used to refuse source == target."""
    cur.execute(
        """
        SELECT current_database() AS db,
               CASE WHEN has_function_privilege('pg_control_system()', 'EXECUTE')
                    THEN (SELECT system_identifier::text FROM pg_control_system())
                    ELSE coalesce(host(inet_server_addr()), 'local') || ':' || current_setting('port')
               END AS sysid
        """
    )
    r = cur.fetchone()
    return str(r["db"]), str(r["sysid"])


def already_migrated(cur: Any, source_label: str) -> Dict[int, int]:
    """Source instance id -> target instance id for earlier runs of this tool."""
    rows = _rows(
        cur,
        """
        SELECT workflow_instance_id, data_json
        FROM wf.instance_extension
        WHERE extension_key = %s AND data_json->>'source_label' = %s
        """,
        (PROVENANCE_KEY, source_label),
    )
    return {int(r["data_json"]["source_instance_id"]): int(r["workflow_instance_id"]) for r in rows}


def _pick_target_def(cur: Any, name: str) -> Optional[int]:
    cur.execute(
        """
        SELECT d.id FROM wf.workflow_def d
        WHERE d.name = %s
        ORDER BY
          EXISTS (SELECT 1 FROM wf.workflow_version v WHERE v.workflow_def_id = d.id AND v.is_active) DESC,
          EXISTS (SELECT 1 FROM wf.workflow_version v JOIN wf.workflow_instance wi
                  ON wi.workflow_version_id = v.id WHERE v.workflow_def_id = d.id) DESC,
          d.id
        LIMIT 1
        """,
        (name,),
    )
    r = cur.fetchone()
    return int(r["id"]) if r else None


def resolve_target_versions(
    cur: Any, source: SourceRuns, *, source_label: str
) -> Dict[int, Dict[str, Any]]:
    """Map each legacy version to a target version, importing missing graphs inactive."""
    result: Dict[int, Dict[str, Any]] = {}
    legacy_fp = {vid: graph_fingerprint(g) for vid, g in source.graphs.items()}
    for vid in sorted(source.graphs):
        g = source.graphs[vid]
        target_ids = [
            int(r["id"])
            for r in _rows(
                cur,
                """SELECT v.id FROM wf.workflow_version v JOIN wf.workflow_def d ON d.id = v.workflow_def_id
                   WHERE d.name = %s ORDER BY v.id""",
                (g.def_name,),
            )
        ]
        target_graphs = load_version_graphs(cur, target_ids)
        target_fp = {tid: graph_fingerprint(tg) for tid, tg in target_graphs.items()}
        matched = match_versions({vid: legacy_fp[vid]}, target_fp)[vid]
        if matched is not None:
            result[vid] = {
                "target_version_id": matched,
                "resolution": "matched",
                "fingerprint": legacy_fp[vid],
                "graph": target_graphs[matched],
            }
            continue
        def_id = _pick_target_def(cur, g.def_name)
        taken: List[int] = []
        if def_id is not None:
            cur.execute(
                "SELECT version_minor FROM wf.workflow_version WHERE workflow_def_id = %s AND version_major = %s",
                (def_id, LEGACY_VERSION_MAJOR),
            )
            taken = [int(r["version_minor"]) for r in cur.fetchall()]
        minor = next_free_minor(legacy_version_minor(g.version_major, g.version_minor), taken)
        spec = build_graph_spec(g, version_minor=minor, source_label=source_label)
        cur.execute("SELECT wf.wf_repo_create_workflow_graph(%s::jsonb) AS r", (json.dumps(spec),))
        created = cur.fetchone()["r"]
        new_vid = int(created["workflow_version_id"])
        new_def = int(created["workflow_def_id"])
        if def_id is not None and def_id != new_def:
            cur.execute(
                "UPDATE wf.workflow_version SET workflow_def_id = %s, is_active = false WHERE id = %s",
                (def_id, new_vid),
            )
            cur.execute("DELETE FROM wf.workflow_def WHERE id = %s", (new_def,))
        imported = load_version_graphs(cur, [new_vid])[new_vid]
        if graph_fingerprint(imported) != legacy_fp[vid]:
            raise MigrationError(f"imported graph for legacy version {vid} does not round-trip")
        result[vid] = {
            "target_version_id": new_vid,
            "resolution": "imported",
            "version": f"{LEGACY_VERSION_MAJOR}.{minor}",
            "fingerprint": legacy_fp[vid],
            "graph": imported,
        }
    return result


def _target_node_id(cur: Any, version_id: int, node_key: str, cache: Dict[Tuple[int, str], int]) -> int:
    k = (version_id, node_key)
    if k not in cache:
        cur.execute(
            "SELECT id FROM wf.workflow_node WHERE workflow_version_id = %s AND node_key = %s",
            (version_id, node_key),
        )
        r = cur.fetchone()
        if r is None:
            raise MigrationError(f"target version {version_id} has no node {node_key}")
        cache[k] = int(r["id"])
    return cache[k]


NE_SKIP = {"id", "workflow_instance_id", "workflow_node_id", "parent_node_execution_id", "completed_by_worker_id"}


def copy_runs(
    cur: Any,
    source: SourceRuns,
    *,
    source_label: str,
    source_database: str,
) -> Dict[str, Any]:
    """Insert the source runs into the target (caller owns commit/rollback)."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_KEY,))
    done = already_migrated(cur, source_label)
    todo = [r for r in source.instances if int(r["id"]) not in done]
    summary: Dict[str, Any] = {
        "skipped_already_migrated": {
            str(int(r["id"])): done[int(r["id"])] for r in source.instances if int(r["id"]) in done
        },
        "inserted": {k: 0 for k in (
            "workflow_instance", "node_execution", "execution_context", "loop_state",
            "scope_variable", "instance_extension", "execution_scope", "workflow_version_imported",
        )},
        "instances": [],
        "versions": {},
    }
    if not todo:
        return summary
    todo_ids = {int(r["id"]) for r in todo}
    needed_versions = {int(r["workflow_version_id"]) for r in todo}
    sub = SourceRuns(
        instances=todo,
        graphs={k: v for k, v in source.graphs.items() if k in needed_versions},
        scopes=source.scopes,
        node_executions=[r for r in source.node_executions if int(r["workflow_instance_id"]) in todo_ids],
        execution_context=source.execution_context,
        loop_state=[r for r in source.loop_state if int(r["workflow_instance_id"]) in todo_ids],
        scope_variables=[r for r in source.scope_variables if int(r["workflow_instance_id"]) in todo_ids],
        instance_extensions=[r for r in source.instance_extensions if int(r["workflow_instance_id"]) in todo_ids],
    )
    versions = resolve_target_versions(cur, sub, source_label=source_label)
    for vid, v in versions.items():
        summary["versions"][str(vid)] = {k: v[k] for k in v if k != "graph"}
        if v["resolution"] == "imported":
            summary["inserted"]["workflow_version_imported"] += 1

    scope_map: Dict[int, int] = {}
    for sid in sorted({int(r["execution_scope_id"]) for r in todo if r["execution_scope_id"] is not None}):
        s = sub.scopes[sid]
        cur.execute("SELECT id FROM wf.execution_scope WHERE set_key = %s", (s["set_key"],))
        r = cur.fetchone()
        if r is not None:
            scope_map[sid] = int(r["id"])
            continue
        cur.execute(
            "SELECT wf.wf_repo_upsert_execution_scope(%s, %s, %s::jsonb) AS id",
            (s["set_key"], s["display_name"], json.dumps(s["config_json"] or {})),
        )
        scope_map[sid] = int(cur.fetchone()["id"])
        summary["inserted"]["execution_scope"] += 1

    target_ne_cols = set(_table_columns(cur, "wf.node_execution"))
    node_cache: Dict[Tuple[int, str], int] = {}
    ne_map: Dict[int, int] = {}
    inst_map: Dict[int, int] = {}
    ne_by_instance: Dict[int, List[Dict[str, Any]]] = {}
    for r in sub.node_executions:
        ne_by_instance.setdefault(int(r["workflow_instance_id"]), []).append(r)

    for inst in todo:
        old_id = int(inst["id"])
        old_vid = int(inst["workflow_version_id"])
        v = versions[old_vid]
        new_vid = int(v["target_version_id"])
        cur.execute(
            """
            INSERT INTO wf.workflow_instance
              (workflow_version_id, status, context_json, started_at_utc, completed_at_utc, execution_scope_id)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            RETURNING id
            """,
            (
                new_vid,
                inst["status"],
                json.dumps(inst["context_json"]) if inst["context_json"] is not None else None,
                inst["started_at_utc"],
                inst["completed_at_utc"],
                scope_map.get(int(inst["execution_scope_id"])) if inst["execution_scope_id"] is not None else None,
            ),
        )
        new_id = int(cur.fetchone()["id"])
        inst_map[old_id] = new_id
        summary["inserted"]["workflow_instance"] += 1

        legacy_graph = sub.graphs[old_vid]
        legacy_workers: Dict[str, Any] = {}
        for ne in order_node_executions(ne_by_instance.get(old_id, [])):
            key = legacy_graph.node_id_to_key.get(int(ne["workflow_node_id"]))
            if key is None:
                raise MigrationError(f"node_execution {ne['id']} references a node outside version {old_vid}")
            cols = ["workflow_instance_id", "workflow_node_id", "parent_node_execution_id"]
            vals: List[Any] = [
                new_id,
                _target_node_id(cur, new_vid, key, node_cache),
                ne_map[int(ne["parent_node_execution_id"])] if ne.get("parent_node_execution_id") is not None else None,
            ]
            for c, val in ne.items():
                if c in NE_SKIP or c not in target_ne_cols:
                    continue
                cols.append(c)
                vals.append(json.dumps(val) if c in ("input_json", "output_json") and val is not None else val)
            placeholders = ", ".join(
                "%s::jsonb" if c in ("input_json", "output_json") else "%s" for c in cols
            )
            cur.execute(
                f"INSERT INTO wf.node_execution ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            ne_map[int(ne["id"])] = int(cur.fetchone()["id"])
            if ne.get("completed_by_worker_id") is not None:
                legacy_workers[str(ne_map[int(ne["id"])])] = int(ne["completed_by_worker_id"])
            summary["inserted"]["node_execution"] += 1

        labels = instance_labels(inst["context_json"])
        provenance = {
            "source_label": source_label,
            "source_database": source_database,
            "source_instance_id": old_id,
            "source_workflow_version_id": old_vid,
            "source_version": f"{legacy_graph.version_major}.{legacy_graph.version_minor}",
            "source_execution_scope_id": inst["execution_scope_id"],
            "target_workflow_version_id": new_vid,
            "version_resolution": v["resolution"],
            "graph_fingerprint": v["fingerprint"],
            "study_label": labels["study_label"],
            "sample_ids": labels["sample_ids"],
            "legacy_completed_by_worker_ids": legacy_workers,
            "tool": TOOL_NAME,
        }
        cur.execute("CALL wf.wf_repo_upsert_instance_extension(%s, %s, %s::jsonb)", (new_id, PROVENANCE_KEY, json.dumps(provenance)))
        summary["instances"].append(
            {
                "source_instance_id": old_id,
                "target_instance_id": new_id,
                "target_workflow_version_id": new_vid,
                "study_label": labels["study_label"],
                "sample_ids": labels["sample_ids"],
            }
        )

    for ec in sub.execution_context:
        old_ne = int(ec["node_execution_id"])
        if old_ne not in ne_map:
            continue
        cur.execute(
            "INSERT INTO wf.execution_context (node_execution_id, context_key, context_value_json) VALUES (%s, %s, %s::jsonb)",
            (ne_map[old_ne], ec["context_key"], json.dumps(ec["context_value_json"]) if ec["context_value_json"] is not None else None),
        )
        summary["inserted"]["execution_context"] += 1

    for ls in sub.loop_state:
        old_inst = int(ls["workflow_instance_id"])
        old_vid = int(next(i for i in todo if int(i["id"]) == old_inst)["workflow_version_id"])
        key = sub.graphs[old_vid].node_id_to_key[int(ls["control_node_id"])]
        cur.execute(
            """INSERT INTO wf.loop_state
                 (workflow_instance_id, control_node_id, scope_node_execution_id, current_iteration, repeat_target_count)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                inst_map[old_inst],
                _target_node_id(cur, int(versions[old_vid]["target_version_id"]), key, node_cache),
                ne_map[int(ls["scope_node_execution_id"])],
                ls["current_iteration"],
                ls["repeat_target_count"],
            ),
        )
        summary["inserted"]["loop_state"] += 1

    sv_rows = [
        (
            inst_map[int(sv["workflow_instance_id"])],
            remap_scope_node_execution_id(sv["scope_node_execution_id"], ne_map),
            sv["var_name"],
            json.dumps(sv["value_json"]),
            sv["updated_at_utc"],
        )
        for sv in sub.scope_variables
    ]
    if sv_rows:
        cur.executemany(
            """INSERT INTO wf.scope_variable
                 (workflow_instance_id, scope_node_execution_id, var_name, value_json, updated_at_utc)
               VALUES (%s, %s, %s, %s::jsonb, %s)""",
            sv_rows,
        )
    summary["inserted"]["scope_variable"] = len(sv_rows)

    for ext in sub.instance_extensions:
        if ext["extension_key"] == PROVENANCE_KEY:
            continue
        cur.execute(
            "CALL wf.wf_repo_upsert_instance_extension(%s, %s, %s::jsonb)",
            (inst_map[int(ext["workflow_instance_id"])], ext["extension_key"], json.dumps(ext["data_json"])),
        )
        summary["inserted"]["instance_extension"] += 1

    verify_copied(cur, sub, inst_map)
    return summary


def verify_copied(cur: Any, source: SourceRuns, inst_map: Mapping[int, int]) -> None:
    """Row counts in the target must equal the source for every copied instance."""
    new_ids = list(inst_map.values())
    copied_ne = {int(ne["id"]) for ne in source.node_executions}
    checks = {
        "node_execution": ("SELECT count(*) AS c FROM wf.node_execution WHERE workflow_instance_id = ANY(%s)", len(source.node_executions)),
        "scope_variable": ("SELECT count(*) AS c FROM wf.scope_variable WHERE workflow_instance_id = ANY(%s)", len(source.scope_variables)),
        "loop_state": ("SELECT count(*) AS c FROM wf.loop_state WHERE workflow_instance_id = ANY(%s)", len(source.loop_state)),
        "execution_context": (
            "SELECT count(*) AS c FROM wf.execution_context c JOIN wf.node_execution n ON n.id = c.node_execution_id "
            "WHERE n.workflow_instance_id = ANY(%s)",
            sum(1 for ec in source.execution_context if int(ec["node_execution_id"]) in copied_ne),
        ),
    }
    for name, (sql, expected) in checks.items():
        cur.execute(sql, (new_ids,))
        got = int(cur.fetchone()["c"])
        if got != expected:
            raise MigrationError(f"{name}: copied {got} rows, expected {expected}")


# --------------------------------------------------------------------------
# Study link (cfg.study + cfg.study_instance_link + omics/Study Meta object)
# --------------------------------------------------------------------------

STUDY_LOCK_KEY = "goliath_omics.legacy_run_study_link"
PROJECTS_ROOT = "/work/projects/"


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _ordered_union(lists: Iterable[Iterable[Any]]) -> List[Any]:
    out: List[Any] = []
    for items in lists:
        for item in items:
            if item not in out:
                out.append(item)
    return out


def _project(context_json: Any) -> Dict[str, Any]:
    ctx = context_json if isinstance(context_json, dict) else {}
    project = ctx.get("project")
    return project if isinstance(project, dict) else {}


def project_archive_id(output_base: Any) -> Optional[str]:
    """``/work/projects/<id>/...`` -> ``<id>`` (the study archive folder), else None."""
    if not isinstance(output_base, str) or not output_base.startswith(PROJECTS_ROOT):
        return None
    rest = output_base[len(PROJECTS_ROOT):].strip("/")
    head = rest.split("/", 1)[0]
    return head or None


def study_document(
    study_name: str,
    description: Optional[str],
    runs: Sequence[Mapping[str, Any]],
    *,
    source_label: str,
) -> Dict[str, Any]:
    """cfg.study document built only from what the copied runs recorded.

    ``runs`` rows carry ``context_json``, ``started_at_utc``, ``completed_at_utc``
    and the provenance ``data_json`` written by :func:`copy_runs`.
    """
    projects = [_project(r.get("context_json")) for r in runs]
    output_bases = _ordered_union([[p["output_base"]] for p in projects if p.get("output_base")])
    started = [r["started_at_utc"] for r in runs if r.get("started_at_utc") is not None]
    completed = [r["completed_at_utc"] for r in runs if r.get("completed_at_utc") is not None]
    doc: Dict[str, Any] = {
        "name": study_name,
        "description": description,
        "project": {
            "chromosomes": _ordered_union(p.get("chromosomes") or [] for p in projects),
            "contexts": _ordered_union(p.get("contexts") or [] for p in projects),
            "output_base": output_bases[0] if len(output_bases) == 1 else output_bases,
        },
        "sample_ids": _ordered_union((r["data_json"].get("sample_ids") or []) for r in runs),
        "runs": {
            "count": len(runs),
            "first_started_at_utc": _iso(min(started)) if started else None,
            "last_completed_at_utc": _iso(max(completed)) if completed else None,
        },
        "source": {
            "label": source_label,
            "tool": TOOL_NAME,
            "source_instance_ids": sorted(int(r["data_json"]["source_instance_id"]) for r in runs),
        },
    }
    if description is None:
        doc.pop("description")
    return doc


def link_runs_to_study(
    cur: Any,
    *,
    study_name: str,
    source_label: str,
    description: Optional[str] = None,
    scope_id: int = 1,
    status: str = "published",
    version: str = "1",
) -> Dict[str, Any]:
    """Create or refresh the study for copied runs and link them to it.

    Runs are the instances copied under ``source_label`` whose recorded study
    label equals ``study_name``. Uses the GoliathApp study APIs
    (``portal.sp_upsert_study``, ``portal.sp_link_study_instance``) and
    ``omics_clinical.sp_meta_mirror_study`` for the workbench object. Safe to
    rerun. A run already linked to a different study stops the link.
    """
    if not study_name or not study_name.strip():
        raise MigrationError("study name is required")
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (STUDY_LOCK_KEY,))
    runs = _rows(
        cur,
        """
        SELECT i.id, i.context_json, i.started_at_utc, i.completed_at_utc, e.data_json
        FROM wf.instance_extension e
        JOIN wf.workflow_instance i ON i.id = e.workflow_instance_id
        WHERE e.extension_key = %s
          AND e.data_json->>'source_label' = %s
          AND e.data_json->>'study_label' = %s
        ORDER BY i.id
        """,
        (PROVENANCE_KEY, source_label, study_name),
    )
    if not runs:
        raise MigrationError(
            f"no copied runs with source label {source_label!r} record study {study_name!r}"
        )
    run_ids = [int(r["id"]) for r in runs]

    doc = study_document(study_name, description, runs, source_label=source_label)
    archive_ids = _ordered_union(
        [[a] for a in (project_archive_id(_project(r.get("context_json")).get("output_base")) for r in runs) if a]
    )
    study_id = archive_ids[0] if len(archive_ids) == 1 else None

    existing = _rows(
        cur,
        "SELECT id, content_hash FROM cfg.study WHERE name = %s AND version = %s",
        (study_name, version),
    )
    cur.execute(
        "SELECT id FROM portal.sp_upsert_study(%s, %s, %s, %s::jsonb, %s)",
        (study_name, version, status, json.dumps(doc, sort_keys=True), study_id),
    )
    study_row_id = int(cur.fetchone()["id"])

    linked = {
        int(r["workflow_instance_id"]): int(r["study_row_id"])
        for r in _rows(
            cur,
            "SELECT workflow_instance_id, study_row_id FROM cfg.study_instance_link "
            "WHERE workflow_instance_id = ANY(%s)",
            (run_ids,),
        )
    }
    other = {i: s for i, s in linked.items() if s != study_row_id}
    if other:
        raise MigrationError(f"runs already linked to another study: {other}")
    new_links = 0
    for iid in run_ids:
        if iid in linked:
            continue
        cur.execute(
            "SELECT id FROM portal.sp_link_study_instance(%s, %s)",
            (study_row_id, iid),
        )
        new_links += 1

    cur.execute(
        "SELECT omics_clinical.sp_meta_mirror_study(%s, %s) AS obj_id",
        (study_row_id, scope_id),
    )
    meta_obj_id = int(cur.fetchone()["obj_id"])

    return {
        "study_name": study_name,
        "study_row_id": study_row_id,
        "study_created": not existing,
        "study_archive_id": study_id,
        "meta_obj_id": meta_obj_id,
        "scope_id": scope_id,
        "instance_ids": run_ids,
        "links_added": new_links,
        "links_existing": len(run_ids) - new_links,
    }
