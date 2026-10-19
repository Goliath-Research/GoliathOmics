"""replace=True must create when the definition is absent."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

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
    def __init__(self, log: list, def_exists: bool) -> None:
        self.log = log
        self.def_exists = def_exists
        self._last = ""

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = ()) -> None:
        self._last = sql
        self.log.append(" ".join(sql.split()))

    def fetchone(self) -> Any:
        if "FROM wf.workflow_def WHERE" in self._last:
            return (1,) if self.def_exists else None
        if "sp_delete_workflow_def" in self._last:
            return {"deleted_instance_count": 0, "deleted_version_count": 1}
        return None


class _FakeConn:
    def __init__(self, log: list, def_exists: bool) -> None:
        self.log = log
        self.def_exists = def_exists

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.log, self.def_exists)

    def commit(self) -> None:
        self.log.append("COMMIT")

    def rollback(self) -> None:
        self.log.append("ROLLBACK")


class _ConnDb:
    def __init__(self, def_exists: bool) -> None:
        self.log: list = []
        self.def_exists = def_exists

    def _connection(self) -> _FakeConn:
        return _FakeConn(self.log, self.def_exists)


def _sample_prep_spec() -> Dict[str, Any]:
    fixture = REPO / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"
    return compile_domain_program_file(fixture, enrich_context=False).workflow.model_dump(
        mode="json"
    )


def _no_delete(*_a: Any) -> Dict[str, int]:
    raise AssertionError("gateway delete must not be used when db has a connection")


def test_replace_deletes_edges_before_nodes_in_one_transaction() -> None:
    db = _ConnDb(def_exists=True)
    result = deploy_workflow_definition(
        db,
        {"spec": _sample_prep_spec(), "replace": True, "delete_instances": False},
        create_workflow_definition=lambda _db, spec: {"workflow_version_id": 9},
        delete_workflow_definition=_no_delete,
    )
    assert result["workflow_version_id"] == 9
    edge_idx = next(i for i, s in enumerate(db.log) if s.startswith("DELETE FROM wf.workflow_edge"))
    sp_idx = next(i for i, s in enumerate(db.log) if "sp_delete_workflow_def" in s)
    assert edge_idx < sp_idx < db.log.index("COMMIT")
    assert db.log.count("COMMIT") == 1


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
