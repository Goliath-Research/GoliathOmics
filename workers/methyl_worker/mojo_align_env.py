"""GOLIATH_ALIGN_* env contract for the GoliathOmics worker.

``MOJO_ALIGN_*`` and ``METHYLGRAPHER_MOJO_*`` remain one-cycle fallbacks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

INSTALL_PREFIX = "/opt/goliath-align"
_MID_PREFIX = "/opt/mojo-align"
_LEGACY_PREFIX = "/opt/methylgrapher-mojo"
_OVERLAY_DEFAULT = Path("/work/goliath/images/goliath-align-overlay")
_OVERLAY_MID = Path("/work/goliath/images/mojo-align-overlay")
_OVERLAY_LEGACY = Path("/work/goliath/images/methylgrapher-mojo-overlay")
_warned: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    print(msg, file=sys.stderr)


def getenv(suffix: str, default: str = "") -> str:
    """Read ``GOLIATH_ALIGN_<suffix>``, then ``MOJO_ALIGN_``, then ``METHYLGRAPHER_MOJO_``."""
    canon_key = f"GOLIATH_ALIGN_{suffix}"
    value = os.environ.get(canon_key, "").strip()
    if value:
        return value
    mid_key = f"MOJO_ALIGN_{suffix}"
    mid = os.environ.get(mid_key, "").strip()
    if mid:
        _warn_once(mid_key, f"warning: {mid_key} is deprecated; use {canon_key}")
        return mid
    old_key = f"METHYLGRAPHER_MOJO_{suffix}"
    legacy = os.environ.get(old_key, "").strip()
    if legacy:
        _warn_once(old_key, f"warning: {old_key} is deprecated; use {canon_key}")
        return legacy
    return default


def image_pin(default: str = "") -> str:
    """Host Docker image pin.

    ``GOLIATH_ALIGN_IMAGE`` is canonical. ``METHYL_MOJO_ALIGN_IMAGE`` remains
    a one-cycle alias.
    """
    value = os.environ.get("GOLIATH_ALIGN_IMAGE", "").strip()
    if value:
        return value
    mid = os.environ.get("METHYL_MOJO_ALIGN_IMAGE", "").strip()
    if mid:
        _warn_once(
            "METHYL_MOJO_ALIGN_IMAGE",
            "warning: METHYL_MOJO_ALIGN_IMAGE is deprecated; use GOLIATH_ALIGN_IMAGE",
        )
        return mid
    legacy = os.environ.get("METHYL_METHYLGRAPHER_MOJO_IMAGE", "").strip()
    if legacy:
        _warn_once(
            "METHYL_METHYLGRAPHER_MOJO_IMAGE",
            "warning: METHYL_METHYLGRAPHER_MOJO_IMAGE is deprecated; "
            "use GOLIATH_ALIGN_IMAGE",
        )
        return legacy
    return default


def overlay_dir() -> Path:
    explicit = getenv("OVERLAY")
    if explicit:
        return Path(explicit)
    if _OVERLAY_DEFAULT.is_dir():
        return _OVERLAY_DEFAULT
    if _OVERLAY_MID.is_dir():
        _warn_once(
            str(_OVERLAY_MID),
            f"warning: {_OVERLAY_MID} is deprecated; use {_OVERLAY_DEFAULT}",
        )
        return _OVERLAY_MID
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
    if os.path.isdir(_MID_PREFIX):
        _warn_once(
            _MID_PREFIX,
            f"warning: {_MID_PREFIX} is deprecated; use {INSTALL_PREFIX}",
        )
        return _MID_PREFIX
    if os.path.isdir(_LEGACY_PREFIX):
        _warn_once(
            _LEGACY_PREFIX,
            f"warning: {_LEGACY_PREFIX} is deprecated; use {INSTALL_PREFIX}",
        )
        return _LEGACY_PREFIX
    return INSTALL_PREFIX
