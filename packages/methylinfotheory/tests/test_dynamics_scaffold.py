"""Tests for deferred dynamics scaffold."""

from __future__ import annotations

from methyl_infotheory.config import InfoTheoryStepConfig
from methyl_infotheory.core.dynamics import build_dynamics_report


def test_dynamics_disabled_by_default():
    cfg = InfoTheoryStepConfig()
    report = build_dynamics_report(cfg)
    assert report["status"] == "disabled"


def test_dynamics_enabled_returns_not_computed():
    cfg = InfoTheoryStepConfig(dynamics_enabled=True)
    report = build_dynamics_report(cfg)
    assert report["status"] == "not_computed"
    assert report["reason"] == "deferred_v2_phase2"
