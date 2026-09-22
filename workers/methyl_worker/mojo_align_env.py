"""MOJO_ALIGN_* env contract with one-release METHYLGRAPHER_MOJO_* dual-read."""

from __future__ import annotations

import os
import sys
from pathlib import Path

INSTALL_PREFIX = "/opt/mojo-align"
_LEGACY_PREFIX = "/opt/methylgrapher-mojo"
_OVERLAY_DEFAULT = Path("/work/goliath/images/mojo-align-overlay")
_OVERLAY_LEGACY = Path("/work/goliath/images/methylgrapher-mojo-overlay")
_warned: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    print(msg, file=sys.stderr)


def getenv(suffix: str, default: str = "") -> str:
    """Read ``MOJO_ALIGN_<suffix>``, then legacy ``METHYLGRAPHER_MOJO_<suffix>``."""
    new_key = f"MOJO_ALIGN_{suffix}"
    old_key = f"METHYLGRAPHER_MOJO_{suffix}"
    value = os.environ.get(new_key, "").strip()
    if value:
        return value
    legacy = os.environ.get(old_key, "").strip()
    if legacy:
        _warn_once(old_key, f"warning: {old_key} is deprecated; use {new_key}")
        return legacy
    return default


def image_pin(default: str = "") -> str:
    """Host Docker image pin: ``METHYL_MOJO_ALIGN_IMAGE``."""
    value = os.environ.get("METHYL_MOJO_ALIGN_IMAGE", "").strip()
    if value:
        return value
    legacy = os.environ.get("METHYL_METHYLGRAPHER_MOJO_IMAGE", "").strip()
    if legacy:
        _warn_once(
            "METHYL_METHYLGRAPHER_MOJO_IMAGE",
            "warning: METHYL_METHYLGRAPHER_MOJO_IMAGE is deprecated; "
            "use METHYL_MOJO_ALIGN_IMAGE",
        )
        return legacy
    return default


def overlay_dir() -> Path:
    explicit = getenv("OVERLAY")
    if explicit:
        return Path(explicit)
    if _OVERLAY_DEFAULT.is_dir():
        return _OVERLAY_DEFAULT
    if _OVERLAY_LEGACY.is_dir():
        _warn_once(
            str(_OVERLAY_LEGACY),
            f"warning: {_OVERLAY_LEGACY} is deprecated; use {_OVERLAY_DEFAULT}",
        )
        return _OVERLAY_LEGACY
    return _OVERLAY_DEFAULT


def install_prefix() -> str:
    if os.path.isdir(INSTALL_PREFIX):
        return INSTALL_PREFIX
    if os.path.isdir(_LEGACY_PREFIX):
        _warn_once(
            _LEGACY_PREFIX,
            f"warning: {_LEGACY_PREFIX} is deprecated; use {INSTALL_PREFIX}",
        )
        return _LEGACY_PREFIX
    return INSTALL_PREFIX
