"""Tests for production align-arm sample layout helpers."""

from __future__ import annotations

from pathlib import Path

from methyl_utils.sample_arm_layout import (
    align_arm_for_context,
    align_arm_for_mode_and_config,
    bind_production_sample_arms,
    bind_sample_arm_dirs,
    ensure_sample_dir_fastq_links,
    is_default_sample_dir,
    is_mode_leaf_dirname,
    link_root_fastqs_into_dir,
    sample_root_from_sample_dir,
)


def test_align_arm_for_mode_and_config() -> None:
    assert (
        align_arm_for_mode_and_config("linear", {"parabricks": {"engine": "parabricks"}})
        == "align.linear.parabricks"
    )
    assert (
        align_arm_for_mode_and_config("linear", {"parabricks": {"engine": "mojo"}})
        == "align.linear.mojo"
    )
    assert (
        align_arm_for_mode_and_config(
            "pangenome_wgbs", {"methylgrapher_wgbs": {"align_engine": "cpu_vg"}}
        )
        == "align.pangenome_wgbs.vg"
    )
    assert (
        align_arm_for_mode_and_config(
            "pangenome_wgbs", {"methylgrapher_wgbs": {"align_engine": "gpu_giraffe"}}
        )
        == "align.pangenome_wgbs.mojo"
    )
    assert align_arm_for_mode_and_config("pangenome", {}) == "align.pangenome.parabricks"
    assert (
        align_arm_for_mode_and_config(
            "pangenome_wgbs", {"methylgrapher_wgbs": {"align_engine": "mojo_giraffe"}}
        )
        == "align.pangenome_wgbs.mojo"
    )
    assert align_arm_for_mode_and_config(None, {}) == "align.linear.parabricks"


def test_sample_root_from_arm_leaf(tmp_path: Path) -> None:
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    assert sample_root_from_sample_dir(arm, "S1") == root
    assert is_mode_leaf_dirname("align.linear.parabricks")
    assert is_mode_leaf_dirname("linear")
    assert not is_mode_leaf_dirname("S1")
    assert is_default_sample_dir(root, root)
    assert not is_default_sample_dir(arm, root)


def test_bind_sample_arm_dirs_default_and_explicit(tmp_path: Path) -> None:
    samples_base = tmp_path / "samples"
    root = samples_base / "S1"
    root.mkdir(parents=True)
    ctx = {
        "alignmentMode": "linear",
        "actionConfig": {"parabricks": {"engine": "mojo"}},
        "samples": [
            {
                "sampleId": "S1",
                "sampleDir": str(root),
                "sampleRoot": str(root),
            }
        ],
    }
    bind_sample_arm_dirs(ctx)
    assert ctx["samples"][0]["sampleRoot"] == str(root)
    assert ctx["samples"][0]["sampleDir"].endswith("align.linear.mojo")
    assert align_arm_for_context(ctx) == "align.linear.mojo"

    explicit = str(root / "align.linear.parabricks")
    ctx2 = {
        "alignmentMode": "linear",
        "actionConfig": {"parabricks": {"engine": "mojo"}},
        "samples": [{"sampleId": "S1", "sampleDir": explicit, "sampleRoot": str(root)}],
    }
    bind_sample_arm_dirs(ctx2)
    assert ctx2["samples"][0]["sampleDir"] == explicit


def test_bind_production_sample_arms_skips_rnaseq(tmp_path: Path) -> None:
    root = tmp_path / "samples" / "S1"
    root.mkdir(parents=True)
    ctx = {
        "alignmentMode": "linear",
        "samples": [{"sampleId": "S1", "sampleDir": str(root), "sampleRoot": str(root)}],
        "program_path": str(
            Path("/tmp") / "sample_prep_rnaseq.program.json"
        ),
    }
    bind_production_sample_arms(ctx)
    assert ctx["samples"][0]["sampleDir"] == str(root)
    assert "align." not in ctx["samples"][0]["sampleDir"]


def test_link_root_fastqs_into_dir(tmp_path: Path) -> None:
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    root.mkdir()
    (root / "S1_1.fastq.gz").write_bytes(b"r1")
    (root / "S1_2.fastq.gz").write_bytes(b"r2")
    linked = link_root_fastqs_into_dir(root, arm, sample_id="S1")
    assert len(linked) == 2
    assert (arm / "S1_1.fastq.gz").read_bytes() == b"r1"


def test_ensure_sample_dir_fastq_links_after_root_only_restore(tmp_path: Path) -> None:
    """CAAS skip restores at sampleRoot; arm sampleDir must be relinked for align."""
    root = tmp_path / "S1"
    arm = root / "align.linear.parabricks"
    arm.mkdir(parents=True)
    (root / "S1_1.fastq.gz").write_bytes(b"r1")
    (root / "S1_2.fastq.gz").write_bytes(b"r2")
    linked = ensure_sample_dir_fastq_links(arm, sample_id="S1", sample_root=root)
    assert len(linked) == 2
    assert (arm / "S1_1.fastq.gz").read_bytes() == b"r1"
    assert (arm / "S1_2.fastq.gz").read_bytes() == b"r2"
    same = ensure_sample_dir_fastq_links(arm, sample_id="S1", sample_root=root)
    assert len(same) == 2
    empty_arm = tmp_path / "S2" / "align.linear.parabricks"
    empty_arm.mkdir(parents=True)
    assert ensure_sample_dir_fastq_links(empty_arm, sample_id="S2") == []
