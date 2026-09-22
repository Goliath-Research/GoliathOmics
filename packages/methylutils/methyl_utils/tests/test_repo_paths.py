"""Tests for repository root / schemas path discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_utils.repo_paths import find_repo_root, repo_schemas_dir


def test_find_repo_root_from_package_file() -> None:
    root = find_repo_root(Path(__file__))
    assert (root / "schemas" / "domain").is_dir()
    assert (root / "scripts" / "packages.list").is_file()


def test_find_repo_root_from_fake_site_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate schema_export living under .venv/lib/.../site-packages."""
    site = tmp_path / ".venv" / "lib" / "pythonX.Y" / "site-packages" / "methyl_domain"
    site.mkdir(parents=True)
    fake_file = site / "schema_export.py"
    fake_file.write_text("# fake\n", encoding="utf-8")

    repo = Path(__file__).resolve().parents[4]
    monkeypatch.chdir(repo)
    root = find_repo_root(fake_file)
    assert root == repo
    assert repo_schemas_dir("domain", start=fake_file) == repo / "schemas" / "domain"


def test_methyl_repo_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "fake_repo"
    (repo / "schemas" / "domain").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "packages.list").write_text("methylutils\n", encoding="utf-8")
    monkeypatch.setenv("METHYL_REPO_ROOT", str(repo))
    assert find_repo_root(tmp_path / "nowhere") == repo.resolve()
