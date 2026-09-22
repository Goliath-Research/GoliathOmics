"""Tests for pipeline.centroid stepOverride argv synthesis."""

from __future__ import annotations

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.actions.base import CliAction


def _centroid_action() -> CliAction:
    entry = find_catalog_entry("pipeline.centroid")
    assert entry is not None
    return CliAction(
        entry=entry,
        cli_tool="methyl-centroid",
        argv_map={
            "project": "--project",
            "group": "--group",
            "chromosome": "--chromosome",
            "context": "--context",
            "outputDir": "--output-dir",
            "stepOverride": "--step-override",
        },
    )


def test_centroid_build_argv_from_add_remove_samples() -> None:
    action = _centroid_action()
    cmd = action.build_argv(
        {
            "projectPath": "/work/project.json",
            "group": "all",
            "chromosome": "21",
            "context": "CG",
            "outputDir": "/work/out/centroids/all",
            "addSamples": ["/work/samples/S1", "/work/samples/S2"],
            "removeSamples": ["/work/samples/S0"],
        }
    )
    assert cmd[0] == "methyl-centroid"
    assert "--project" in cmd
    assert "--group" in cmd and "all" in cmd
    assert "--step-override" in cmd
    override_idx = cmd.index("--step-override")
    override_path = cmd[override_idx + 1]
    assert override_path.endswith(".json")


def test_centroid_build_argv_synthesizes_override_when_step_override_key_is_none() -> None:
    """Pydantic-serialized payloads include stepOverride: null; fallback must still apply."""
    action = _centroid_action()
    cmd = action.build_argv(
        {
            "projectPath": "/work/project.json",
            "group": "all",
            "chromosome": "21",
            "context": "CG",
            "outputDir": "/work/out/centroids/all",
            "stepOverride": None,
            "addSamples": ["/work/samples/S1"],
            "removeSamples": ["/work/samples/S0"],
        }
    )
    assert "--step-override" in cmd
