"""Unit tests for typed workflow.compute in-process handlers."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_worker.depends import call_in_process_handler
from methyl_worker.handlers.workflow_compute import (
    _handle_workflow_const_bool,
    _handle_workflow_const_int,
    _handle_workflow_fs_stat,
    _handle_workflow_json_path_bool,
)
from methyl_worker.task_models.workflow_compute_models import (
    ConstBoolTaskInput,
    ConstIntTaskInput,
    FsStatTaskInput,
    JsonPathBoolTaskInput,
)


def test_const_bool_and_int():
    out_b = call_in_process_handler(
        _handle_workflow_const_bool,
        "workflow.const-bool",
        "workflow.const_bool",
        ConstBoolTaskInput(value=True),
    )
    assert out_b.value is True
    out_i = call_in_process_handler(
        _handle_workflow_const_int,
        "workflow.const-int",
        "workflow.const_int",
        ConstIntTaskInput(value=7),
    )
    assert out_i.value == 7


def test_json_path_bool(tmp_path: Path):
    doc = tmp_path / "page.json"
    doc.write_text(json.dumps({"hasMore": False, "meta": {"n": 1}}), encoding="utf-8")
    out = call_in_process_handler(
        _handle_workflow_json_path_bool,
        "workflow.json-path-bool",
        "workflow.json_path_bool",
        JsonPathBoolTaskInput(documentPath=str(doc), jsonPath="$.hasMore"),
    )
    assert out.value is False


def test_fs_stat_missing_and_present(tmp_path: Path):
    missing = call_in_process_handler(
        _handle_workflow_fs_stat,
        "workflow.fs-stat",
        "workflow.fs_stat",
        FsStatTaskInput(path=str(tmp_path / "nope")),
    )
    assert missing.exists is False
    assert missing.value is False

    f = tmp_path / "f.txt"
    f.write_text("hi", encoding="utf-8")
    present = call_in_process_handler(
        _handle_workflow_fs_stat,
        "workflow.fs-stat",
        "workflow.fs_stat",
        FsStatTaskInput(path=str(f)),
    )
    assert present.exists is True
    assert present.value is True
    assert present.size == 2
    assert present.mtimeUtc
