"""Tests for persist_secret_if_changed (load module without package __init__ / sqlmodel)."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PKG = Path(__file__).resolve().parents[1] / "methyl_mapper"
_spec = importlib.util.spec_from_file_location(
    "methyl_mapper.secure_credentials",
    _PKG / "secure_credentials.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["methyl_mapper.secure_credentials"] = _mod
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
SecureCredentialManager = _mod.SecureCredentialManager
persist_secret_if_changed = _mod.persist_secret_if_changed


def test_persist_secret_if_changed_skips_when_unchanged(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("METHYL_MAPPER_CREDENTIAL_PASSWORD", "test-salt-for-persist")
    mgr = SecureCredentialManager(
        credential_name="test_persist_key",
        encrypted_file_path=tmp_path / "t.enc",
        env_var_name="TEST_PERSIST_KEY",
    )
    assert mgr.save_credential("same-value", use_azure=False, use_encrypted_file=True)
    with patch.object(mgr, "save_credential") as mock_save:
        assert persist_secret_if_changed(mgr, "same-value", use_azure=False) is False
        mock_save.assert_not_called()


def test_persist_secret_if_changed_writes_when_new(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("METHYL_MAPPER_CREDENTIAL_PASSWORD", "test-salt-for-persist")
    mgr = SecureCredentialManager(
        credential_name="test_persist_key2",
        encrypted_file_path=tmp_path / "t2.enc",
        env_var_name="TEST_PERSIST_KEY2",
    )
    assert persist_secret_if_changed(mgr, "fresh-secret", use_azure=False) is True
    assert mgr.get_credential(explicit_key=None) == "fresh-secret"


def test_persist_empty_returns_false(tmp_path: Path):
    mgr = SecureCredentialManager(
        credential_name="test_persist_key3",
        encrypted_file_path=tmp_path / "t3.enc",
        env_var_name="TEST_PERSIST_KEY3",
    )
    assert persist_secret_if_changed(mgr, "  ", use_azure=False) is False
