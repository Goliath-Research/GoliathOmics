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
