"""Unit tests for FASTQ source path helpers."""

from __future__ import annotations

from methyl_worker.fastq_source import _relative_to_prefix


def test_relative_to_prefix_strips_list_prefix() -> None:
    assert _relative_to_prefix("plasma/S1/S1_1.fastq.gz", "plasma/S1/") == "S1_1.fastq.gz"


def test_relative_to_prefix_keeps_subfolders() -> None:
    assert (
        _relative_to_prefix("plasma/S1/lane1/S1_1.fastq.gz", "plasma/S1/")
        == "lane1/S1_1.fastq.gz"
    )
