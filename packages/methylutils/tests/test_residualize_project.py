"""Worker isolation: baked resolvedConfig must not re-merge site/profile knobs."""

from __future__ import annotations

from types import SimpleNamespace

from methyl_utils.residualize_config import ResidualizeStepConfig
from methyl_utils.residualize_project import resolve_named_step_config


class _StubProject:
    def get_derived_paths(self):
        return SimpleNamespace(output_base="/tmp/out")

    def get_resolved_groups(self):
        return [("all", ["/work/samples/S1"])]


def test_resolved_config_does_not_call_resolve_for_project(monkeypatch, tmp_path):
    calls = []

    def _boom(*_args, **_kwargs):
        calls.append(1)
        return {"m_value_eps": 0.01, "min_coverage": 99}

    monkeypatch.setattr("methyl_utils.residualize_project.resolve_for_project", _boom)
    monkeypatch.setattr("methyl_utils.residualize_project.load_project", lambda _p: _StubProject())

    cfg, samples, out = resolve_named_step_config(
        "residualize",
        ResidualizeStepConfig,
        tmp_path / "project.json",
        resolved_config={"m_value_eps": 1e-4, "min_coverage": None},
        default_output_subdir="residualize",
    )
    assert calls == []
    assert cfg.m_value_eps == 1e-4
    assert cfg.min_coverage is None
    assert samples == [("S1", "/work/samples/S1")]
    assert out.endswith("residualize")


def test_nested_resolved_config_slice(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "methyl_utils.residualize_project.resolve_for_project",
        lambda *_a, **_k: {"m_value_eps": 0.01},
    )
    monkeypatch.setattr("methyl_utils.residualize_project.load_project", lambda _p: _StubProject())

    cfg, _samples, _out = resolve_named_step_config(
        "residualize",
        ResidualizeStepConfig,
        tmp_path / "project.json",
        resolved_config={"residualize": {"m_value_eps": 1e-5, "variance_threshold": None}},
        default_output_subdir="residualize",
    )
    assert cfg.m_value_eps == 1e-5
    assert cfg.variance_threshold is None


def test_standalone_without_resolved_config_uses_project_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "methyl_utils.residualize_project.resolve_for_project",
        lambda *_a, **_k: {"min_coverage": 7},
    )
    monkeypatch.setattr("methyl_utils.residualize_project.load_project", lambda _p: _StubProject())

    cfg, _samples, _out = resolve_named_step_config(
        "residualize",
        ResidualizeStepConfig,
        tmp_path / "project.json",
        resolved_config=None,
        default_output_subdir="residualize",
    )
    assert cfg.min_coverage == 7
