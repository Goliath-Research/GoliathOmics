"""Legacy SamplePrep run migration: remapping logic (unit) and a two-database copy (PostgreSQL)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from shutil import which
from typing import Any, Dict, Iterator, List

import pytest

from ops.legacy_run_migration import (
    LEGACY_VERSION_MAJOR,
    MigrationError,
    VersionGraph,
    assert_read_only,
    build_graph_spec,
    graph_fingerprint,
    instance_labels,
    legacy_version_minor,
    match_versions,
    next_free_minor,
    order_node_executions,
    remap_scope_node_execution_id,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "migrate_legacy_sampleprep_runs.py"


def _node(node_type: str, action: str | None = None, **extra: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "node_type": node_type,
        "action_name": action,
        "input_template": None,
        "input_bindings": [],
        "output_bindings": [],
        "scope_defaults": [],
    }
    base.update(extra)
    return base


def _graph(version_id: int = 33, *, extra_edge: bool = False, ids_offset: int = 0) -> VersionGraph:
    g = VersionGraph(
        version_id=version_id,
        def_name="SamplePrepPipeline",
        version_major=1,
        version_minor=0,
        description=None,
        root_node_key="root",
        nodes={
            "root": _node("SEQUENCE"),
            "align": _node("ACTION", "sample.align", input_template={"x": 1},
                           output_bindings=[{"var_name": "bam", "source_kind": "output_path", "source_json_path": "$.bam"}]),
            "qc": _node("ACTION", "sample.qc", scope_defaults=[{"var_name": "k", "default_expr": "1"}]),
        },
        node_id_to_key={100 + ids_offset: "root", 101 + ids_offset: "align", 102 + ids_offset: "qc"},
        edges=[
            {"parent_node_key": "root", "child_node_key": "align", "child_order": 0, "branch_kind": "SEQUENCE",
             "condition_expr": None, "switch_case_value": None, "is_default": False},
            {"parent_node_key": "root", "child_node_key": "qc", "child_order": 1, "branch_kind": "SEQUENCE",
             "condition_expr": None, "switch_case_value": None, "is_default": False},
        ],
        collection_bindings=[{"bind_order": 0, "scope_var": "samples", "source_kind": "json_path",
                              "path_var": None, "base_var": None, "json_path": "$.samples"}],
    )
    if extra_edge:
        g.edges.append({"parent_node_key": "align", "child_node_key": "qc", "child_order": 0, "branch_kind": None,
                        "condition_expr": None, "switch_case_value": None, "is_default": False})
    return g


def test_fingerprint_ignores_ids_and_version_numbers() -> None:
    a = _graph(33)
    b = _graph(47, ids_offset=500)
    b.version_major, b.version_minor = 3, 0
    b.edges = list(reversed(b.edges))
    assert graph_fingerprint(a) == graph_fingerprint(b)


def test_fingerprint_changes_with_structure() -> None:
    assert graph_fingerprint(_graph()) != graph_fingerprint(_graph(extra_edge=True))
    changed = _graph()
    changed.nodes["qc"]["action_name"] = "sample.qc_v2"
    assert graph_fingerprint(_graph()) != graph_fingerprint(changed)


def test_match_versions_by_fingerprint() -> None:
    fp_same = graph_fingerprint(_graph())
    fp_other = graph_fingerprint(_graph(extra_edge=True))
    got = match_versions({33: fp_other, 47: fp_same}, {58: fp_same, 12: fp_same})
    assert got == {33: None, 47: 12}


def test_legacy_version_numbering_never_outranks_active() -> None:
    assert LEGACY_VERSION_MAJOR == 0
    assert legacy_version_minor(1, 0) == 1000
    assert legacy_version_minor(3, 2) == 3002
    assert next_free_minor(2000, [1000, 2000, 2001]) == 2002


def test_build_graph_spec_is_inactive_and_complete() -> None:
    spec = build_graph_spec(_graph(), version_minor=1000, source_label="legacy")
    assert spec["is_active"] is False
    assert (spec["version_major"], spec["version_minor"]) == (0, 1000)
    assert spec["root_node_key"] == "root"
    keys = {n["node_key"] for n in spec["nodes"]}
    assert keys == {"root", "align", "qc"}
    align = next(n for n in spec["nodes"] if n["node_key"] == "align")
    assert align["action_name"] == "sample.align" and align["input_template"] == {"x": 1}
    assert spec["output_bindings"] == [
        {"node_key": "align", "var_name": "bam", "source_kind": "output_path", "source_json_path": "$.bam"}
    ]
    assert spec["scope_defaults"] == [{"node_key": "qc", "var_name": "k", "default_expr": "1"}]
    assert spec["collection_bindings"][0]["kind"] == "json_path"
    assert "legacy" in spec["description"]


def test_build_graph_spec_requires_root() -> None:
    g = _graph()
    g.root_node_key = None
    with pytest.raises(MigrationError):
        build_graph_spec(g, version_minor=1000, source_label="legacy")


def test_order_node_executions_parents_first() -> None:
    rows = [
        {"id": 5, "parent_node_execution_id": 9},
        {"id": 9, "parent_node_execution_id": None},
        {"id": 7, "parent_node_execution_id": 5},
    ]
    assert [r["id"] for r in order_node_executions(rows)] == [9, 5, 7]


def test_order_node_executions_detects_cycle() -> None:
    with pytest.raises(MigrationError):
        order_node_executions([
            {"id": 1, "parent_node_execution_id": 2},
            {"id": 2, "parent_node_execution_id": 1},
        ])


def test_scope_variable_instance_scope_sentinel() -> None:
    assert remap_scope_node_execution_id(0, {}) == 0
    assert remap_scope_node_execution_id(63, {63: 7}) == 7
    with pytest.raises(MigrationError):
        remap_scope_node_execution_id(64, {63: 7})


def test_instance_labels_from_context() -> None:
    ctx = {
        "project": {"project_name": "SamplePrep_e2e_clara_linear_canary"},
        "samples": [{"sampleId": "canary-SRR28293403-linear-subset"}, {"other": 1}],
    }
    assert instance_labels(ctx) == {
        "study_label": "SamplePrep_e2e_clara_linear_canary",
        "sample_ids": ["canary-SRR28293403-linear-subset"],
    }
    assert instance_labels(None) == {"study_label": None, "sample_ids": []}


class _Cur:
    def __init__(self, value: str) -> None:
        self.value = value

    def execute(self, *_a: Any) -> None:
        return None

    def fetchone(self) -> Dict[str, str]:
        return {"transaction_read_only": self.value}


def test_source_must_be_read_only() -> None:
    assert_read_only(_Cur("on"))
    with pytest.raises(MigrationError):
        assert_read_only(_Cur("off"))


# --------------------------------------------------------------------------
# PostgreSQL: two databases with GoliathApp wf schema, copy, rerun no-op
# --------------------------------------------------------------------------

APP_SQL = Path(os.environ.get("GOLIATH_APP_SQL_DIR", REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg"))


def _pg_env(db: str | None = None) -> Dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    env.setdefault("PGPASSWORD", "methyl")
    env["PGDATABASE"] = db or os.environ.get("POSTGRES_DB", "postgres")
    return env


def _psql(sql: str, env: Dict[str, str]) -> str:
    proc = subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        env=env, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def _dsn(env: Dict[str, str]) -> str:
    return (
        f"host={env['PGHOST']} port={env['PGPORT']} user={env['PGUSER']} "
        f"password={env['PGPASSWORD']} dbname={env['PGDATABASE']}"
    )


GRAPH_V1: Dict[str, Any] = {
    "name": "SamplePrepPipeline",
    "root_node_key": "root",
    "nodes": [
        {"node_key": "root", "node_type": "SEQUENCE"},
        {"node_key": "prep", "node_type": "ACTION", "input_template": {"step": "prep"}},
        {"node_key": "loop", "node_type": "REPEAT", "repeat_count": 2},
        {"node_key": "body", "node_type": "ACTION"},
    ],
    "edges": [
        {"parent_node_key": "root", "child_node_key": "prep", "child_order": 0, "branch_kind": "SEQUENCE"},
        {"parent_node_key": "root", "child_node_key": "loop", "child_order": 1, "branch_kind": "SEQUENCE"},
        {"parent_node_key": "loop", "child_node_key": "body", "child_order": 0, "branch_kind": "BODY"},
    ],
    "output_bindings": [{"node_key": "prep", "var_name": "out", "source_kind": "output_path", "source_json_path": "$.o"}],
}
GRAPH_V2: Dict[str, Any] = {
    **GRAPH_V1,
    "nodes": GRAPH_V1["nodes"] + [{"node_key": "final", "node_type": "ACTION"}],
    "edges": GRAPH_V1["edges"] + [
        {"parent_node_key": "root", "child_node_key": "final", "child_order": 2, "branch_kind": "SEQUENCE"}
    ],
}


def _create_graph(env: Dict[str, str], spec: Dict[str, Any]) -> Dict[str, Any]:
    created: Dict[str, Any] = json.loads(
        _psql(f"SELECT wf.wf_repo_create_workflow_graph('{json.dumps(spec)}'::jsonb)", env)
    )
    return created


def _seed_source(env: Dict[str, str]) -> List[int]:
    v1 = _create_graph(env, {**GRAPH_V1, "is_active": False})
    v2 = _create_graph(env, {**GRAPH_V2, "version_major": 2})
    # Same definition, two versions (as deploy keep-instances leaves them).
    _psql(f"UPDATE wf.workflow_version SET workflow_def_id = {v1['workflow_def_id']} WHERE id = {v2['workflow_version_id']}", env)
    _psql(f"DELETE FROM wf.workflow_def WHERE id = {v2['workflow_def_id']}", env)
    scope = _psql("SELECT wf.wf_repo_upsert_execution_scope('legacy-scope-a', NULL, '{}'::jsonb)", env)
    ids: List[int] = []
    for ver in (v1, v2):
        vid = ver["workflow_version_id"]
        ctx = json.dumps({"project": {"project_name": "SamplePrep_e2e_clara_linear_canary"},
                          "samples": [{"sampleId": "canary-SRR28293403-linear-subset"}]})
        inst = int(_psql(
            "INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json, started_at_utc, "
            f"completed_at_utc, execution_scope_id) VALUES ({vid}, 'COMPLETED', '{ctx}'::jsonb, "
            f"'2026-10-21T14:00:00Z', '2026-10-21T14:30:00Z', {scope}) RETURNING id", env))
        ids.append(inst)

        def node(key: str) -> int:
            return int(_psql(f"SELECT id FROM wf.workflow_node WHERE workflow_version_id = {vid} AND node_key = '{key}'", env))

        root_ne = int(_psql(
            "INSERT INTO wf.node_execution (workflow_instance_id, workflow_node_id, status) "
            f"VALUES ({inst}, {node('root')}, 'SUCCEEDED') RETURNING id", env))
        loop_ne = int(_psql(
            "INSERT INTO wf.node_execution (workflow_instance_id, workflow_node_id, status, parent_node_execution_id) "
            f"VALUES ({inst}, {node('loop')}, 'SUCCEEDED', {root_ne}) RETURNING id", env))
        body_ne = int(_psql(
            "INSERT INTO wf.node_execution (workflow_instance_id, workflow_node_id, status, parent_node_execution_id, "
            f"iteration_no, output_json) VALUES ({inst}, {node('body')}, 'SUCCEEDED', {loop_ne}, 1, "
            "'{\"ok\": true}'::jsonb) RETURNING id", env))
        _psql(f"INSERT INTO wf.execution_context (node_execution_id, context_key, context_value_json) "
              f"VALUES ({body_ne}, 'ctx.iterationNo', '1'::jsonb)", env)
        _psql(f"INSERT INTO wf.loop_state (workflow_instance_id, control_node_id, scope_node_execution_id, "
              f"current_iteration, repeat_target_count) VALUES ({inst}, {node('loop')}, {loop_ne}, 2, 2)", env)
        _psql(f"INSERT INTO wf.scope_variable (workflow_instance_id, scope_node_execution_id, var_name, value_json) "
              f"VALUES ({inst}, 0, 'project', '\"p\"'::jsonb), ({inst}, {loop_ne}, 'i', '2'::jsonb)", env)
        _psql(f"CALL wf.wf_repo_upsert_instance_extension({inst}, 'methyl.execution.scope', "
              "'{\"executionScopeId\": \"legacy-scope-a\"}'::jsonb)", env)
    return ids


@pytest.fixture(scope="module")
def two_dbs() -> Iterator[Dict[str, Dict[str, str]]]:
    if not which("psql"):
        pytest.skip("psql not installed")
    if not (APP_SQL / "apply_schema.sh").is_file():
        pytest.skip(f"GoliathApp apply_schema.sh not found at {APP_SQL}")
    pytest.importorskip("psycopg")
    admin = _pg_env()
    try:
        _psql("SELECT 1", admin)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable")
    suffix = uuid.uuid4().hex[:8]
    out: Dict[str, Dict[str, str]] = {}
    for role in ("src", "tgt"):
        name = f"legacy_mig_{role}_{suffix}"
        _psql(f'CREATE DATABASE "{name}"', admin)
        env = _pg_env(name)
        subprocess.run(["bash", str(APP_SQL / "apply_schema.sh")], env=env, check=True, cwd=str(APP_SQL),
                       capture_output=True)
        out[role] = env
    out["ids"] = {"ids": ",".join(str(i) for i in _seed_source(out["src"]))}
    # Target already has the newer graph as its active version.
    _create_graph(out["tgt"], GRAPH_V2)
    yield out
    for role in ("src", "tgt"):
        subprocess.run(["psql", "-X", "-c", f'DROP DATABASE IF EXISTS "{out[role]["PGDATABASE"]}" WITH (FORCE)'],
                       env=admin, capture_output=True, check=False)


def _run(two_dbs: Dict[str, Dict[str, str]], *extra: str) -> Dict[str, Any]:
    env = os.environ.copy()
    env["LEGACY_SOURCE_DSN"] = _dsn(two_dbs["src"])
    env["MIGRATION_TARGET_DSN"] = _dsn(two_dbs["tgt"])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--instance-ids", two_dbs["ids"]["ids"], *extra],
        env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report: Dict[str, Any] = json.loads(proc.stdout)
    return report


def _counts(env: Dict[str, str]) -> str:
    return _psql(
        "SELECT (SELECT count(*) FROM wf.workflow_instance) || '/' || (SELECT count(*) FROM wf.node_execution) "
        "|| '/' || (SELECT count(*) FROM wf.scope_variable) || '/' || (SELECT count(*) FROM wf.loop_state) "
        "|| '/' || (SELECT count(*) FROM wf.execution_context)",
        env,
    )


def test_dry_run_apply_rerun(two_dbs: Dict[str, Dict[str, str]]) -> None:
    src, tgt = two_dbs["src"], two_dbs["tgt"]
    src_before = _counts(src)

    dry = _run(two_dbs)
    assert dry["mode"].startswith("dry-run")
    assert dry["inserted"]["workflow_instance"] == 2
    assert _counts(tgt) == "0/0/0/0/0"

    applied = _run(two_dbs, "--apply")
    assert applied["inserted"]["workflow_instance"] == 2
    assert applied["inserted"]["node_execution"] == 6
    assert applied["inserted"]["scope_variable"] == 4
    assert applied["inserted"]["workflow_version_imported"] == 1
    assert _counts(tgt) == "2/6/4/2/2"
    resolutions = sorted(v["resolution"] for v in applied["versions"].values())
    assert resolutions == ["imported", "matched"]

    versions = _psql(
        "SELECT string_agg(v.version_major || '.' || v.version_minor || ':' || v.is_active, ',' ORDER BY v.id) "
        "FROM wf.workflow_version v JOIN wf.workflow_def d ON d.id = v.workflow_def_id "
        "WHERE d.name = 'SamplePrepPipeline'",
        tgt,
    )
    assert versions == "1.0:true,0.1000:false"
    assert _psql("SELECT count(*) FROM wf.workflow_def WHERE name = 'SamplePrepPipeline'", tgt) == "1"
    # Every copied node execution points at a node of its instance's version.
    assert _psql(
        "SELECT count(*) FROM wf.node_execution ne JOIN wf.workflow_instance wi ON wi.id = ne.workflow_instance_id "
        "JOIN wf.workflow_node n ON n.id = ne.workflow_node_id WHERE n.workflow_version_id <> wi.workflow_version_id",
        tgt,
    ) == "0"
    assert _psql(
        "SELECT count(*) FROM wf.instance_extension WHERE extension_key = 'legacy.migration.source' "
        "AND data_json->>'study_label' = 'SamplePrep_e2e_clara_linear_canary'",
        tgt,
    ) == "2"

    again = _run(two_dbs, "--apply")
    assert sum(again["inserted"].values()) == 0
    assert len(again["skipped_already_migrated"]) == 2
    assert _counts(tgt) == "2/6/4/2/2"
    assert _counts(src) == src_before
