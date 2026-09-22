"""CLI argument forwarding for methyl-hyperparam-search.

Locks the ``parse_known_args`` behavior: extra methyl-validation flags are
forwarded verbatim whether or not a bare ``--`` separator precedes them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from methyl_validation import hyperparam_search as hs


def _run_main(argv):
    captured = {}

    def _fake_run_search(**kwargs):
        captured.update(kwargs)
        return []

    with patch.object(hs, "run_search", _fake_run_search), patch.object(sys, "argv", argv):
        hs.main()
    return captured


def _base_argv(tmp_path: Path):
    cfg = tmp_path / "mc.json"
    cfg.write_text("{}", encoding="utf-8")
    return [
        "methyl-hyperparam-search",
        "--config",
        str(cfg),
        "--work-dir",
        str(tmp_path / "work"),
        "--grid",
        '{"stability_dmp_freq": [0.6, 0.7]}',
    ]


def test_extra_args_forwarded_without_separator(tmp_path: Path):
    argv = _base_argv(tmp_path) + ["--stability", "--resume", "1", "--skip-centroid"]
    captured = _run_main(argv)
    assert captured["extra_methyl_validation_args"] == [
        "--stability",
        "--resume",
        "1",
        "--skip-centroid",
    ]


def test_extra_args_forwarded_with_leading_separator(tmp_path: Path):
    argv = _base_argv(tmp_path) + ["--", "--stability", "--resume", "1", "--skip-centroid"]
    captured = _run_main(argv)
    assert captured["extra_methyl_validation_args"] == [
        "--stability",
        "--resume",
        "1",
        "--skip-centroid",
    ]


def test_defaults_to_stability_when_no_extra_args(tmp_path: Path):
    captured = _run_main(_base_argv(tmp_path))
    assert captured["extra_methyl_validation_args"] == ["--stability"]


def test_dry_run_does_not_inject_stability(tmp_path: Path):
    argv = _base_argv(tmp_path) + ["--dry-run"]
    captured = _run_main(argv)
    assert captured["dry_run"] is True
    assert captured["extra_methyl_validation_args"] is None
