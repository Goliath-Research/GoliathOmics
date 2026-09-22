"""Unit tests for the shared action result manifest + atomic-write layer.

``action_result`` is imported by every action that emits artifacts (manifests,
idempotency records). A regression in manifest construction or atomic writes
would corrupt outputs across the pipeline, so these lock the behavior directly
rather than relying on indirect coverage via workers/tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_domain.action_result import (
    ActionExecutionRecord,
    ArtifactRef,
    action_results_dir,
    artifact_ref_for,
    atomic_write_action_result,
    atomic_write_json,
    manifest_path_for,
    read_action_result,
    utc_now,
)


# --------------------------------------------------------------------------- #
# Manifest path construction
# --------------------------------------------------------------------------- #
def test_action_results_dir_is_hidden_subdir(tmp_path: Path):
    assert action_results_dir(tmp_path) == tmp_path / ".action_results"


def test_manifest_path_sanitizes_action_and_key(tmp_path: Path):
    path = manifest_path_for(tmp_path, "pipeline.centroid", run_key="chr 1/CG")
    assert path.parent == tmp_path / ".action_results"
    # dots in action -> underscores; slashes/spaces in key -> underscores.
    assert path.name == "pipeline_centroid.chr_1_CG.json"


def test_manifest_path_default_run_key(tmp_path: Path):
    path = manifest_path_for(tmp_path, "sample.methyl_qc")
    assert path.name == "sample_methyl_qc.default.json"


def test_caas_path_helpers(tmp_path: Path):
    from methyl_domain.action_result import (
        caas_entry_dir,
        caas_entry_manifest_path,
        caas_root,
        instance_ledger_path,
    )

    project_root = tmp_path / "Study"
    assert caas_root(project_root) == project_root / ".caas"
    entry = caas_entry_dir(project_root, "pipeline.centroid", "abc123")
    assert entry == project_root / ".caas" / "pipeline_centroid" / "abc123"
    assert caas_entry_manifest_path(project_root, "pipeline.centroid", "abc123") == entry / "manifest.json"
    assert instance_ledger_path(project_root, "hpset-1") == project_root / ".caas" / "instances" / "hpset-1.json"


# --------------------------------------------------------------------------- #
# Atomic writes
# --------------------------------------------------------------------------- #
def test_atomic_write_json_creates_parents_and_content(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "out.json"
    atomic_write_json(target, {"b": 2, "a": 1})
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"b": 2, "a": 1}
    # No leftover temp files in the parent directory.
    assert list(target.parent.glob(".*tmp*")) == []


def test_atomic_write_json_overwrites_existing(tmp_path: Path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"v": 1})
    atomic_write_json(target, {"v": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}


def test_atomic_write_json_cleans_temp_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.json"

    def boom(*_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("methyl_domain.action_result.json.dump", boom)
    with pytest.raises(OSError, match="simulated write failure"):
        atomic_write_json(target, {"v": 1})
    assert not target.exists()
    assert list(tmp_path.glob(".*tmp*")) == []


def test_atomic_write_and_read_action_record_roundtrip(tmp_path: Path):
    now = utc_now()
    record = ActionExecutionRecord(
        action_name="pipeline.centroid",
        capability="methyl-centroid",
        started_at_utc=now,
        finished_at_utc=now,
        duration_ms=1200,
        artifacts=[ArtifactRef(path="/work/x/centroid.h5", bytes=1024)],
        action_revision="rev1",
        input_signature="in-sig",
        output_signature="out-sig",
    )
    path = manifest_path_for(tmp_path, record.action_name)
    written = atomic_write_action_result(path, record)
    assert written == path

    loaded = read_action_result(path, ActionExecutionRecord)
    assert loaded.action_name == "pipeline.centroid"
    assert loaded.input_signature == "in-sig"
    assert loaded.artifacts[0].path == "/work/x/centroid.h5"
    assert loaded.artifacts[0].bytes == 1024
    assert loaded.skipped is False


# --------------------------------------------------------------------------- #
# ArtifactRef helpers
# --------------------------------------------------------------------------- #
def test_artifact_ref_for_existing_file_records_size(tmp_path: Path):
    f = tmp_path / "artifact.txt"
    f.write_text("hello", encoding="utf-8")
    ref = artifact_ref_for(f)
    assert ref.bytes == 5
    assert ref.kind == "file"
    assert Path(ref.path).name == "artifact.txt"


def test_artifact_ref_for_missing_file_has_no_size(tmp_path: Path):
    ref = artifact_ref_for(tmp_path / "missing.txt")
    assert ref.bytes is None


def test_artifact_ref_forbids_extra_fields():
    with pytest.raises(Exception):
        ArtifactRef(path="/x", unexpected=True)
