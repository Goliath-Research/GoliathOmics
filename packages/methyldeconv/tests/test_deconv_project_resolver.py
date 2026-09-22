"""Tests for project sample and resolved-group propagation."""

from __future__ import annotations

from methyl_deconv.project_resolver import _all_sample_dirs


class _StubProject:
    def get_resolved_groups(self):
        return [
            ("all", ["/work/samples/H1", "/work/samples/shared"]),
            ("PCa", ["/work/samples/P1", "/work/samples/shared"]),
        ]


def test_all_sample_dirs_includes_group_and_first_group_wins_duplicates() -> None:
    assert _all_sample_dirs(_StubProject()) == [
        ("H1", "/work/samples/H1", "all"),
        ("shared", "/work/samples/shared", "all"),
        ("P1", "/work/samples/P1", "PCa"),
    ]
