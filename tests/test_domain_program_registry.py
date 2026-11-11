"""Unit tests for cfg.domain_program registration helper."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from workflow_engine.ops.domain_program_registry import load_program_document, register_domain_program_after_deploy

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_mc_fixture_program_documents_load() -> None:
    fixture = REPO_ROOT / "workflow_engine/domain/fixtures/mc_stability.program.json"
    doc = load_program_document(fixture)
    assert doc["name"] == "MC_Stability"
    assert "programVersion" in doc
    json.dumps(doc)


class _DictCursor:
    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _DictCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.commands.append((sql, params or ()))

    def fetchone(self) -> dict[str, int]:
        return {"id": 99}


class _FakeConn:
    def __init__(self) -> None:
        self.committed = False

    def cursor(self) -> _DictCursor:
        return _DictCursor()

    def commit(self) -> None:
        self.committed = True


class _FakeDb:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    @contextmanager
    def _connection(self):
        yield self.conn


def test_register_domain_program_after_deploy_reads_dict_row_id() -> None:
    db = _FakeDb()
    program_id = register_domain_program_after_deploy(
        db,
        program_document={"name": "MC_Stability", "programVersion": 2, "body": []},
        workflow_def_id=10,
        workflow_version_id=20,
    )
    assert program_id == 99
    assert db.conn.committed is True
