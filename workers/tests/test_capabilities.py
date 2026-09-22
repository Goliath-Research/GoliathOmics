"""Tests for worker capability detection and guards."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from methyl_worker.capabilities import (
    GPU_REQUIRED_CAPABILITIES,
    HOST_SAMTOOLS_CAPABILITIES,
    OMNIBUS_WILDCARD,
    assert_execute_gpu_prereqs,
    assert_execute_host_tool_prereqs,
    assert_node_can_serve_capability,
    capability_requires_gpu,
    resolve_worker_capabilities,
)


def test_resolve_explicit_omnibus() -> None:
    assert resolve_worker_capabilities(explicit=["*"]) == [OMNIBUS_WILDCARD]


def test_resolve_explicit_list() -> None:
    caps = resolve_worker_capabilities(explicit=["methyl-qc", "methyl-qc", "validation.plan-iterations"])
    assert caps == ["methyl-qc", "validation.plan-iterations"]


def test_gpu_capabilities_flagged() -> None:
    assert "methyl-centroid" in GPU_REQUIRED_CAPABILITIES
    assert "parabricks.fq2bam" in GPU_REQUIRED_CAPABILITIES
    assert capability_requires_gpu("methyl-qc") is False


def test_assert_node_rejects_gpu_capability_without_gpu() -> None:
    with patch("methyl_worker.capabilities._gpu_available", return_value=False):
        with pytest.raises(RuntimeError, match="no functional GPU"):
            assert_node_can_serve_capability("methyl-centroid")


def test_assert_node_rejects_parabricks_giraffe_without_image() -> None:
    with patch("methyl_worker.capabilities._gpu_available", return_value=True):
        with patch("methyl_worker.capabilities._parabricks_available", return_value=False):
            with pytest.raises(RuntimeError, match="parabricks\\.giraffe"):
                assert_node_can_serve_capability("parabricks.giraffe")


def test_assert_execute_gpu_prereqs() -> None:
    with patch("methyl_worker.capabilities._gpu_available", return_value=False):
        with pytest.raises(RuntimeError, match="requires a GPU"):
            assert_execute_gpu_prereqs("methyl-centroid", "pipeline.centroid")


def test_auto_detect_includes_validation_caps() -> None:
    with patch("methyl_worker.capabilities._gpu_available", return_value=False):
        with patch("methyl_worker.capabilities._cli_on_path", return_value=False):
            caps = resolve_worker_capabilities()
    assert "validation.plan-iterations" in caps


def test_auto_detect_empty_does_not_become_omnibus() -> None:
    """Probe failure must return [] — never silent ['*'] (enroll-safety invariant)."""
    with patch(
        "methyl_worker.capabilities._catalog_capability_rows",
        return_value=[],
    ), patch("methyl_worker.capabilities._gpu_available", return_value=False):
        caps = resolve_worker_capabilities()
    assert caps == []
    assert OMNIBUS_WILDCARD not in caps


def test_wgbs_align_omitted_without_samtools() -> None:
    assert "methylgrapher.wgbs_align" in HOST_SAMTOOLS_CAPABILITIES

    def _which(name: str):
        return None if name == "samtools" else f"/usr/bin/{name}"

    with patch("methyl_worker.capabilities._gpu_available", return_value=True), patch(
        "methyl_worker.capabilities.shutil.which", side_effect=_which
    ), patch(
        "methyl_worker.capabilities._catalog_capability_rows",
        return_value=[
            ("methylgrapher.wgbs_align", "always", None),
            ("validation.plan-iterations", "always", None),
        ],
    ):
        caps = resolve_worker_capabilities()
    assert "methylgrapher.wgbs_align" not in caps
    assert "methylgrapher.wgbs_gpu_align" not in caps
    assert "validation.plan-iterations" in caps


def test_assert_execute_host_tool_prereqs_samtools() -> None:
    with patch("methyl_worker.capabilities._cli_on_path", return_value=False):
        with pytest.raises(RuntimeError, match="samtools"):
            assert_execute_host_tool_prereqs(
                "methylgrapher.wgbs_align", "sample.methylgrapher_wgbs_align"
            )
