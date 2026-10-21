"""replace=True must create when the definition is absent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from ops.workflow_deploy import deploy_workflow_definition

REPO = Path(__file__).resolve().parents[2]
for _p in (
    REPO / "packages" / "methyldomain",
    REPO / "workers",
    REPO / "workflow_engine",
    REPO / "workflow_engine" / "domain",
    REPO / "workflow_engine" / "contract",
):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from compiler import compile_domain_program_file  # noqa: E402


class _MissingDeleteDb:
    pass


def test_replace_treats_missing_delete_as_noop() -> None:
    created: Dict[str, Any] = {}

    def delete(_db: Any, name: str, delete_instances: bool) -> Dict[str, int]:
        raise RuntimeError("Workflow definition not found")

    def create(_db: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
        created["spec"] = spec
        return {
            "workflow_def_id": 1,
            "workflow_version_id": 42,
            "root_node_id": 7,
            "name": spec["name"],
        }

    fixture = REPO / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"
    db_spec = compile_domain_program_file(fixture, enrich_context=False).workflow.model_dump(
        mode="json"
    )
    result = deploy_workflow_definition(
        _MissingDeleteDb(),
        {"spec": db_spec, "replace": True, "delete_instances": False},
        create_workflow_definition=create,
        delete_workflow_definition=delete,
    )
    assert result["workflow_version_id"] == 42
    assert created["spec"]["name"] == "SamplePrepPipeline"


class _FakeCursor:
    def __init__(self, db: "_ConnDb") -> None:
        self.db = db
        self._last = ""

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = ()) -> None:
        self._last = " ".join(sql.split())
        self.db.log.append(self._last)
        self.db.params.append(params)

    def fetchone(self) -> Any:
        if "wf.workflow_def" in self._last and "name = %s" in self._last:
            return {"id": 5} if self.db.def_exists else None
        if "sp_delete_workflow_def" in self._last:
            return {"deleted_instance_count": 2, "deleted_version_count": 2}
        return None

    def fetchall(self) -> Any:
        if "NOT EXISTS" in self._last:
            return [{"id": v} for v in self.db.versions if v not in self.db.referenced]
        if "FROM wf.workflow_version v WHERE" in self._last:
            return [{"id": v} for v in self.db.versions]
        return []


class _FakeConn:
    def __init__(self, db: "_ConnDb") -> None:
        self.db = db

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.db)

    def commit(self) -> None:
        self.db.log.append("COMMIT")
        self.db.params.append(None)

    def rollback(self) -> None:
        self.db.log.append("ROLLBACK")
        self.db.params.append(None)


class _ConnDb:
    def __init__(
        self,
        def_exists: bool,
        versions: tuple = (11, 12),
        referenced: tuple = (),
    ) -> None:
        self.log: List[str] = []
        self.params: List[Any] = []
        self.def_exists = def_exists
        self.versions = versions
        self.referenced = referenced

    def _connection(self) -> _FakeConn:
        return _FakeConn(self)


def _sample_prep_spec() -> Dict[str, Any]:
    fixture = REPO / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"
    return compile_domain_program_file(fixture, enrich_context=False).workflow.model_dump(
        mode="json"
    )


def _no_delete(*_a: Any) -> Dict[str, int]:
    raise AssertionError("gateway delete must not be used when db has a connection")


def _create(_db: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"workflow_def_id": 99, "workflow_version_id": 13, "root_node_id": 1}


def _index(log: List[str], prefix: str) -> int:
    return next(i for i, s in enumerate(log) if s.startswith(prefix))


def test_replace_with_delete_instances_removes_edges_then_calls_procedure() -> None:
    db = _ConnDb(def_exists=True)
    result = deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": True},
        create_workflow_definition=_create,
        delete_workflow_definition=_no_delete,
    )
    assert result["workflow_version_id"] == 13
    edge_idx = _index(db.log, "DELETE FROM wf.workflow_edge")
    sp_idx = next(i for i, s in enumerate(db.log) if "sp_delete_workflow_def" in s)
    assert edge_idx < sp_idx < db.log.index("COMMIT")
    assert db.params[edge_idx] == ([11, 12],)
    assert db.log.count("COMMIT") == 1


def test_keep_instances_without_references_drops_whole_definition() -> None:
    db = _ConnDb(def_exists=True)
    result = deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
        create_workflow_definition=_create,
        delete_workflow_definition=_no_delete,
    )
    assert result["workflow_def_id"] == 99
    assert not any("sp_delete_workflow_def" in s for s in db.log)
    edge_idx = _index(db.log, "DELETE FROM wf.workflow_edge")
    ver_idx = _index(db.log, "DELETE FROM wf.workflow_version")
    def_idx = _index(db.log, "DELETE FROM wf.workflow_def")
    assert edge_idx < ver_idx < def_idx
    assert db.params[ver_idx] == ([11, 12],)
    assert not any(s.startswith("UPDATE wf.workflow_version") for s in db.log)


def test_keep_instances_keeps_referenced_version_and_adds_new_version() -> None:
    db = _ConnDb(def_exists=True, versions=(11, 12), referenced=(12,))
    result = deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
        create_workflow_definition=_create,
        delete_workflow_definition=_no_delete,
    )
    assert result["workflow_def_id"] == 5
    assert result["workflow_version_id"] == 13
    edge_idx = _index(db.log, "DELETE FROM wf.workflow_edge")
    ver_idx = _index(db.log, "DELETE FROM wf.workflow_version")
    assert edge_idx < ver_idx
    assert db.params[edge_idx] == ([11],)
    assert db.params[ver_idx] == ([11],)
    deact = _index(db.log, "UPDATE wf.workflow_version SET is_active = false")
    assert db.params[deact] == (5,)
    attach = _index(db.log, "UPDATE wf.workflow_version SET workflow_def_id")
    assert db.params[attach] == (5, 5, 13)
    drop_tmp = len(db.log) - 1 - db.log[::-1].index("DELETE FROM wf.workflow_def WHERE id = %s")
    assert db.params[drop_tmp] == (99,)
    assert attach < drop_tmp
    assert db.log.index("DELETE FROM wf.workflow_def WHERE id = %s") == drop_tmp
    lookup = next(s for s in db.log if "wf.workflow_def" in s and "name = %s" in s)
    assert "workflow_instance" in lookup
    assert "ORDER BY" in lookup
    assert db.log.count("COMMIT") == 1
    assert db.log[-1] == "COMMIT"
    assert drop_tmp < db.log.index("COMMIT")


def test_keep_instances_all_versions_referenced_deletes_no_graph_rows() -> None:
    db = _ConnDb(def_exists=True, versions=(12,), referenced=(12,))
    deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
        create_workflow_definition=_create,
        delete_workflow_definition=_no_delete,
    )
    assert not any(s.startswith("DELETE FROM wf.workflow_edge") for s in db.log)
    assert not any(s.startswith("DELETE FROM wf.workflow_version") for s in db.log)


def test_keep_instances_create_commit_is_not_visible_before_attach() -> None:
    db = _ConnDb(def_exists=True, versions=(11, 12), referenced=(12,))

    def _create_and_commit(_db: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
        with _db._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT wf.wf_repo_create_workflow_graph(%s)", ("{}",))
            conn.commit()
        return {"workflow_def_id": 99, "workflow_version_id": 13, "root_node_id": 1}

    deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
        create_workflow_definition=_create_and_commit,
        delete_workflow_definition=_no_delete,
    )
    create_idx = _index(db.log, "SELECT wf.wf_repo_create_workflow_graph")
    deact = _index(db.log, "UPDATE wf.workflow_version SET is_active = false")
    attach = _index(db.log, "UPDATE wf.workflow_version SET workflow_def_id")
    assert deact < create_idx < attach < db.log.index("COMMIT")
    assert db.log.count("COMMIT") == 1


def test_keep_instances_create_failure_rolls_back_deactivation() -> None:
    db = _ConnDb(def_exists=True, versions=(11, 12), referenced=(12,))

    def _boom(_db: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("create failed")

    try:
        deploy_workflow_definition(
            db,
            {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
            create_workflow_definition=_boom,
            delete_workflow_definition=_no_delete,
        )
    except RuntimeError as exc:
        assert "create failed" in str(exc)
    else:
        raise AssertionError("create failure must propagate")
    deact = _index(db.log, "UPDATE wf.workflow_version SET is_active = false")
    assert deact < db.log.index("ROLLBACK")
    assert "COMMIT" not in db.log
    assert getattr(db._connection, "__func__", None) is _ConnDb._connection


def test_replace_missing_definition_with_connection_is_noop() -> None:
    db = _ConnDb(def_exists=False)
    result = deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True},
        create_workflow_definition=lambda _db, spec: {"workflow_version_id": 3},
        delete_workflow_definition=_no_delete,
    )
    assert result["workflow_version_id"] == 3
    assert not any("workflow_edge" in s for s in db.log)
