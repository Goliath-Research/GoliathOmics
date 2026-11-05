"""Neutral platform env resolution with legacy fallbacks."""

from __future__ import annotations

from methyl_worker.platform_env import (
    resolve_api_base,
    resolve_arc_env_path,
    resolve_database_url_override,
    resolve_work_root,
)


def test_resolve_api_base_prefers_goliath(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_API_BASE", raising=False)
    monkeypatch.setenv("GOLIATH_API_BASE", "https://gw.example/v1")
    monkeypatch.delenv("METHYL_API_BASE", raising=False)
    assert resolve_api_base() == "https://gw.example/v1"


def test_resolve_api_base_legacy_methyl(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_API_BASE", raising=False)
    monkeypatch.delenv("GOLIATH_API_BASE", raising=False)
    monkeypatch.setenv("METHYL_API_BASE", "http://legacy/v1")
    assert resolve_api_base() == "http://legacy/v1"


def test_resolve_database_url_override(monkeypatch) -> None:
    monkeypatch.setenv("WF_DATABASE_URL", "postgresql://u:p@h/db")
    assert resolve_database_url_override() == "postgresql://u:p@h/db"


def test_resolve_work_root_goliath(monkeypatch) -> None:
    monkeypatch.setenv("GOLIATH_WORK_ROOT", "/data/work")
    monkeypatch.delenv("METHYL_WORK_ROOT", raising=False)
    assert str(resolve_work_root()) == "/data/work"


def test_resolve_arc_env_default() -> None:
    assert str(resolve_arc_env_path()).endswith("goliath/arc.env")
