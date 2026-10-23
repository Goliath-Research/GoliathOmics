"""Tests for the unified action catalog and derived registries."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from methyl_worker.action_catalog import ACTION_CATALOG
from methyl_worker.action_runtime import (
    PROJECT_ACTION_CONFIG_KEYS,
    RUNTIME_CATALOG,
    build_capability_handlers,
    build_tool_cli_map,
    validate_catalog_linkage,
)
from methyl_worker.handlers import CAPABILITY_HANDLERS, TOOL_CLI, execute_task
from methyl_worker.task_schema_registry import TASK_SCHEMA_SPECS, list_task_schema_specs


def test_catalog_linkage_valid() -> None:
    assert validate_catalog_linkage() == []


def test_task_schema_specs_derived_from_catalog() -> None:
    assert len(TASK_SCHEMA_SPECS) == len(RUNTIME_CATALOG)
    assert {s.action_name for s in TASK_SCHEMA_SPECS} == {e.action_name for e in RUNTIME_CATALOG}


def test_capability_handlers_derived_from_catalog() -> None:
    handlers = build_capability_handlers()
    in_process = [e for e in RUNTIME_CATALOG if e.execution_mode == "in_process"]
    assert len(handlers) == len(in_process)
    assert CAPABILITY_HANDLERS == handlers
    assert handlers["methyl-qc"] == "_handle_methyl_qc"


def test_tool_cli_derived_from_catalog() -> None:
    assert TOOL_CLI == build_tool_cli_map()
    assert TOOL_CLI["MethylCentroid"] == "methyl-centroid"
    assert TOOL_CLI["MethylAlignmentQc"] == "methyl-qc"


def test_every_in_process_handler_exists_in_handlers_module() -> None:
    handlers_mod = importlib.import_module("methyl_worker.handlers")
    for entry in RUNTIME_CATALOG:
        if entry.execution_mode != "in_process":
            continue
        name = entry.resolved_in_process_handler()
        assert name and hasattr(handlers_mod, name), name


def test_catalog_entries_have_execution_mode() -> None:
    for entry in RUNTIME_CATALOG:
        assert entry.execution_mode in ("cli", "in_process")
        if entry.execution_mode == "cli":
            assert entry.cli_tool
        else:
            assert entry.resolved_in_process_handler()
    for entry in RUNTIME_CATALOG:
        if entry.action_config_key is not None:
            assert entry.action_config_key in PROJECT_ACTION_CONFIG_KEYS


def test_catalog_entries_export_control() -> None:
    for entry in RUNTIME_CATALOG:
        ctrl = entry.to_catalog_dict()["control"]
        assert set(ctrl) == {"can_pause", "can_continue", "can_stop"}
        assert isinstance(ctrl["can_stop"], bool)


def test_semantic_action_catalog_covers_runtime() -> None:
    semantic_names = {spec.action_name for spec in ACTION_CATALOG}
    runtime_names = {entry.action_name for entry in RUNTIME_CATALOG}
    assert runtime_names <= semantic_names
    plan = next(s for s in ACTION_CATALOG if s.action_name == "validation.plan_iterations")
    assert plan.overrides == "core"
    assert plan.schema_version == "2"


def test_committed_semantic_catalog_matches_export() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    catalog_path = repo_root / "schemas" / "actions" / "catalog.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "goliath_app.actions.schema_export",
            "--check",
            str(catalog_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_mark_failed_handler() -> None:
    result = execute_task(
        "sample.mark-failed",
        "sample.qc_failed",
        {"sampleId": "S1", "sampleDir": "/work/samples/S1", "reason": "test"},
    )
    assert result.output.model_dump()["status"] == "QC_FAILED"


def test_sample_prep_actions_have_domain_effects() -> None:
    sample_prep = [
        "sample.download_fastq",
        "sample.parabricks_fq2bam",
        "sample.parabricks_giraffe",
        "sample.methylgrapher_wgbs_align",
        "sample.methyl_qc",
        "sample.fragmentomics",
        "sample.methyl_extract",
        "sample.methylgrapher_wgbs_extract",
        "sample.extraction_qc",
        "sample.qc_failed",
    ]
    for action_name in sample_prep:
        entry = next(e for e in RUNTIME_CATALOG if e.action_name == action_name)
        assert entry.domain_effects is not None, action_name
        assert entry.domain_effects.writes_types

    qc = next(e for e in RUNTIME_CATALOG if e.action_name == "sample.methyl_qc")
    assert "qcPass" in [v for v, _ in qc.domain_effects.scope_bindings]


def test_gpu_align_actions_are_exclusive_one_per_worker() -> None:
    exclusive = {
        "sample.parabricks_fq2bam",
        "sample.parabricks_giraffe",
        "sample.parabricks_rna_fq2bam",
        "sample.methylgrapher_wgbs_align",
    }
    for action_name in exclusive:
        entry = next(e for e in RUNTIME_CATALOG if e.action_name == action_name)
        dispatch = entry.to_catalog_dict()["dispatch"]
        assert dispatch.get("exclusive_worker") is True, action_name
        assert dispatch.get("max_per_worker") == 1, action_name


def test_all_sample_prep_catalog_actions_have_golden_fixtures() -> None:
    from golden_fixtures_data import GOLDEN_INPUTS, GOLDEN_OUTPUTS

    sample_prep = [e.action_name for e in RUNTIME_CATALOG if e.category == "sample_prep"]
    missing_in = sorted(a for a in sample_prep if a not in GOLDEN_INPUTS)
    missing_out = sorted(a for a in sample_prep if a not in GOLDEN_OUTPUTS)
    assert not missing_in, missing_in
    assert not missing_out, missing_out
    golden_dir = Path(__file__).resolve().parent / "golden"
    for action_name in sample_prep:
        stem = action_name.replace(".", "_")
        assert (golden_dir / f"{stem}.input.json").is_file(), action_name
        assert (golden_dir / f"{stem}.output.json").is_file(), action_name
