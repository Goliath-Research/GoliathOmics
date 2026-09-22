"""QC parser treats align.* arm leaves as mode dirs so sampleId is not the arm name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_alignment_qc.core.metrics_family import MetricsFamily, detect_metrics_family
from methyl_alignment_qc.core.parser import (
    _ALIGN_ARM_DIRNAMES,
    _EXPERIMENT_MODE_DIRNAMES,
    find_metrics_in_sample_dir,
    parse_metrics_from_sample_paths,
    resolve_sample_artifact_id,
)

_ALIGN_ARM_LEAVES = (
    "align.linear.parabricks",
    "align.linear.mojo",
    "align.pangenome_wgbs.vg",
    "align.pangenome_wgbs.mojo",
    "align.pangenome.parabricks",
)

_DEDUP = (
    "## METRICS CLASS\tpicard.sam.markduplicates.MarkDuplicatesMetrics\n"
    "LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\t"
    "SECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\t"
    "READ_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\t"
    "ESTIMATED_LIBRARY_SIZE\n"
    "lib1\t0\t100\t0\t0\t0\t8\t1\t0.08\t1000\n"
)


def test_align_arm_dirnames_are_mode_leaves() -> None:
    for arm in _ALIGN_ARM_LEAVES:
        assert arm in _ALIGN_ARM_DIRNAMES
        assert arm in _EXPERIMENT_MODE_DIRNAMES
    for legacy in ("linear", "pangenome", "pangenome_wgbs"):
        assert legacy in _EXPERIMENT_MODE_DIRNAMES


@pytest.mark.parametrize("arm", _ALIGN_ARM_LEAVES)
def test_resolve_sample_artifact_id_align_arm_from_artifacts(tmp_path: Path, arm: str) -> None:
    sample_id = "HBCST-051425-74294"
    arm_dir = tmp_path / "samples" / sample_id / arm
    arm_dir.mkdir(parents=True)
    (arm_dir / f"{sample_id}.bam").write_bytes(b"BAM")
    (arm_dir / f"{sample_id}.qc-metrics.tar").write_bytes(b"tar")
    assert resolve_sample_artifact_id(arm_dir) == sample_id
    assert resolve_sample_artifact_id(arm_dir) != arm


@pytest.mark.parametrize("arm", _ALIGN_ARM_LEAVES)
def test_resolve_sample_artifact_id_align_arm_from_parent(tmp_path: Path, arm: str) -> None:
    """Empty arm leaf still takes sampleId from /work/samples/{id}/, not the arm name."""
    sample_id = "DPLST-051425-111148"
    arm_dir = tmp_path / "samples" / sample_id / arm
    arm_dir.mkdir(parents=True)
    assert resolve_sample_artifact_id(arm_dir) == sample_id
    assert resolve_sample_artifact_id(arm_dir, sample_id) == sample_id


def test_resolve_sample_artifact_id_does_not_scan_sibling_arms(tmp_path: Path) -> None:
    sample_id = "S1"
    root = tmp_path / sample_id
    clara = root / "align.linear.parabricks"
    wgbs = root / "align.pangenome_wgbs.mojo"
    clara.mkdir(parents=True)
    wgbs.mkdir(parents=True)
    (wgbs / "WRONG.bam").write_bytes(b"BAM")
    (wgbs / "WRONG.deduplicate_metrics.txt").write_text("x", encoding="utf-8")
    assert resolve_sample_artifact_id(clara) == sample_id
    assert resolve_sample_artifact_id(clara) != "WRONG"
    assert resolve_sample_artifact_id(wgbs) == "WRONG"


def test_find_metrics_in_sample_dir_uses_parent_id_on_arm_leaf(tmp_path: Path) -> None:
    sample_id = "HBCST-TEST"
    arm_dir = tmp_path / sample_id / "align.linear.parabricks"
    arm_dir.mkdir(parents=True)
    metrics = arm_dir / f"{sample_id}.deduplicate_metrics.txt"
    metrics.write_text(_DEDUP, encoding="utf-8")
    found = find_metrics_in_sample_dir(arm_dir)
    assert found == metrics
    parsed = parse_metrics_from_sample_paths([arm_dir])
    assert sample_id in parsed
    assert "align.linear.parabricks" not in parsed


def test_detect_metrics_family_on_arm_leaf_uses_alignment_mode_not_siblings(
    tmp_path: Path,
) -> None:
    """alignmentMode selects the metrics family; sibling align.* dirs are ignored."""
    sample_id = "S1"
    root = tmp_path / sample_id
    clara = root / "align.linear.parabricks"
    wgbs = root / "align.pangenome_wgbs.mojo"
    clara.mkdir(parents=True)
    wgbs.mkdir(parents=True)
    (clara / f"{sample_id}.json").write_text(
        json.dumps({"sample_id": sample_id, "quality_yield": {"total_reads": 1}}),
        encoding="utf-8",
    )
    (wgbs / f"{sample_id}.alignment_metrics.json").write_text(
        json.dumps(
            {
                "tool": "methylGrapher",
                "action": "sample.methylgrapher_wgbs_align",
                "sample_id": sample_id,
            }
        ),
        encoding="utf-8",
    )
    family, _, _ = detect_metrics_family(
        clara, sample_id, alignment_mode="linear", parabricks_available=True
    )
    assert family == MetricsFamily.PARABRICKS
    family_wgbs, _, _ = detect_metrics_family(
        wgbs, sample_id, alignment_mode="pangenome_wgbs"
    )
    assert family_wgbs == MetricsFamily.METHYLGRAPHER_WGBS
