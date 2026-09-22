"""gpu_backend seam: numpy | cupy | mojo (unset keeps CuPy-or-NumPy)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from methyl_utils.array_backend import (
    get_array_module,
    normalize_gpu_backend,
    resolve_array_backend,
)
from methyl_utils.mojo_numeric import mojo_numeric_available


def test_normalize_gpu_backend_accepts_known_names():
    assert normalize_gpu_backend(None) is None
    assert normalize_gpu_backend("") is None
    assert normalize_gpu_backend("auto") is None
    assert normalize_gpu_backend("NumPy") == "numpy"
    assert normalize_gpu_backend("CUPY") == "cupy"
    assert normalize_gpu_backend("mojo") == "mojo"


def test_normalize_gpu_backend_rejects_unknown():
    with pytest.raises(ValueError, match="gpu_backend must be one of"):
        normalize_gpu_backend("opencl")


def test_resolve_unset_matches_get_array_module():
    name, xp, used = resolve_array_backend(prefer_gpu=False)
    assert name == "numpy"
    assert xp is np
    assert used is False
    xp2, used2 = get_array_module(prefer_gpu=False)
    assert xp2 is xp
    assert used2 is False


def test_resolve_numpy_explicit():
    name, xp, used = resolve_array_backend(prefer_gpu=True, gpu_backend="numpy")
    assert name == "numpy"
    assert xp is np
    assert used is False


def test_resolve_disable_env_forces_numpy(monkeypatch):
    monkeypatch.setenv("METHYL_DISABLE_GPU", "1")
    name, xp, used = resolve_array_backend(prefer_gpu=True, gpu_backend="cupy")
    assert name == "numpy"
    assert xp is np
    assert used is False


def test_resolve_mojo_when_numeric_present():
    if not mojo_numeric_available():
        pytest.skip("mojo-align numeric/ not on this host")
    name, xp, used = resolve_array_backend(prefer_gpu=True, gpu_backend="mojo")
    assert name == "mojo"
    assert xp is np
    assert used is True


def test_resolve_mojo_fails_closed_without_tree(monkeypatch):
    monkeypatch.delenv("MOJO_ALIGN_ROOT", raising=False)
    monkeypatch.setenv("MOJO_ALIGN_ROOT", "/tmp/mojo-align-missing-numeric")
    from methyl_utils import mojo_numeric

    mojo_numeric._load_kernels.cache_clear()
    monkeypatch.setattr(mojo_numeric, "find_mojo_align_root", lambda: None)
    with pytest.raises(RuntimeError, match="gpu_backend=mojo"):
        resolve_array_backend(prefer_gpu=True, gpu_backend="mojo")
    mojo_numeric._load_kernels.cache_clear()
