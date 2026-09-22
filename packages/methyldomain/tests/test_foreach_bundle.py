"""Tests for FOREACH iteration-bundle CAAS."""

from __future__ import annotations

from pathlib import Path

from methyl_domain.foreach_bundle import (
    commit_iteration_bundle,
    compute_iteration_bundle_key,
    extend_foreach_ancestry,
    fingerprint_foreach_ancestry,
    read_iteration_bundle,
    try_short_circuit_foreach_iteration,
)
from methyl_domain.sample_content_store import (
    resolve_sample_caas_root,
    sample_caas_enabled_for_action,
    sample_caas_opt_out_reason,
)


def test_bundle_key_stable() -> None:
    kwargs = dict(
        foreach_node_key="fe",
        collection_var="items",
        iteration_index=2,
        item_payload={"a": 1},
        child_action_revisions=["pipeline.centroid:x"],
    )
    assert compute_iteration_bundle_key(**kwargs) == compute_iteration_bundle_key(**kwargs)


def test_nested_identical_leaf_items_do_not_collide() -> None:
    """Control vs disease seed groups both bind context=CG; keys must differ."""
    shared = dict(
        foreach_node_key="foreach_context",
        collection_var="contexts",
        iteration_index=0,
        item_payload="CG",
        child_action_revisions=["pipeline.centroid:rev"],
    )
    control_fp = fingerprint_foreach_ancestry(
        extend_foreach_ancestry(
            [],
            foreach_node_key="foreach_seed",
            collection_var="centroidSeedGroups",
            iteration_index=0,
            item_payload={"label": "all", "centroidDir": "/seed/controls"},
        )
    )
    disease_fp = fingerprint_foreach_ancestry(
        extend_foreach_ancestry(
            [],
            foreach_node_key="foreach_seed",
            collection_var="centroidSeedGroups",
            iteration_index=1,
            item_payload={"label": "pca_low", "centroidDir": "/seed/diseases"},
        )
    )
    assert control_fp != disease_fp
    control_key = compute_iteration_bundle_key(
        **shared, extra_input_fingerprint=control_fp
    )
    disease_key = compute_iteration_bundle_key(
        **shared, extra_input_fingerprint=disease_fp
    )
    assert control_key != disease_key
    # Regression: without ancestry, both would share one key.
    bare = compute_iteration_bundle_key(**shared)
    assert bare != control_key
    assert bare != disease_key


def test_nested_short_circuit_requires_matching_ancestry(tmp_path: Path) -> None:
    shared = dict(
        foreach_node_key="foreach_context",
        collection_var="contexts",
        iteration_index=0,
        item_payload="CG",
    )
    control_fp = fingerprint_foreach_ancestry(
        [{"foreach_node_key": "foreach_seed", "item": {"label": "all"}}]
    )
    disease_fp = fingerprint_foreach_ancestry(
        [{"foreach_node_key": "foreach_seed", "item": {"label": "pca_low"}}]
    )
    control_key = compute_iteration_bundle_key(
        **shared, extra_input_fingerprint=control_fp
    )
    commit_iteration_bundle(
        tmp_path,
        control_key,
        foreach_node_key=shared["foreach_node_key"],
        collection_var=shared["collection_var"],
        iteration_index=0,
        item_payload="CG",
    )
    assert (
        try_short_circuit_foreach_iteration(
            tmp_path,
            **shared,
            extra_input_fingerprint=control_fp,
        )
        is not None
    )
    assert (
        try_short_circuit_foreach_iteration(
            tmp_path,
            **shared,
            extra_input_fingerprint=disease_fp,
        )
        is None
    )


def test_commit_read(tmp_path: Path) -> None:
    key = compute_iteration_bundle_key(
        foreach_node_key="fe",
        collection_var="items",
        iteration_index=0,
        item_payload="x",
    )
    commit_iteration_bundle(
        tmp_path,
        key,
        foreach_node_key="fe",
        collection_var="items",
        iteration_index=0,
        item_payload="x",
    )
    assert read_iteration_bundle(tmp_path, key)["status"] == "completed"


def test_sample_opt_outs(monkeypatch) -> None:
    monkeypatch.delenv("METHYL_SAMPLE_CAAS_ENABLED", raising=False)
    assert sample_caas_opt_out_reason("sample.delete_bam") == "destructive"
    assert sample_caas_opt_out_reason("sample.archive_sample") == "remote_upload_etag_only"
    assert sample_caas_enabled_for_action("sample.download_fastq") is True
    assert sample_caas_enabled_for_action("sample.delete_bam") is False


def test_sample_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SAMPLES_BASE", str(tmp_path))
    root = resolve_sample_caas_root({"sampleId": "S1"})
    assert root == tmp_path / "S1"


def test_sample_caas_root_arm_leaf_uses_parent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SAMPLES_BASE", str(tmp_path))
    arm = tmp_path / "S1" / "align.linear.parabricks"
    assert resolve_sample_caas_root({"sampleDir": str(arm)}) == (tmp_path / "S1").resolve()
    assert resolve_sample_caas_root(
        {"sampleRoot": str(tmp_path / "S1"), "sampleDir": str(arm)}
    ) == (tmp_path / "S1").resolve()
