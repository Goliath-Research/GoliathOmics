"""Shared cytosine call helpers: prefer XM tags, fall back to sequence+XG."""

from __future__ import annotations


def meth_call_from_xm(xm_char: str) -> int:
    """Return 1 methylated, 0 unmethylated, -1 not a cytosine call."""
    if xm_char in "ZXHU":
        return 1
    if xm_char in "zxhu":
        return 0
    return -1


def meth_call_from_seq_xg(ref: str, base: str, xg: str) -> int:
    """MethylExtractor sequence+XG fallback (OT/CT vs OB/GA)."""
    ref_u = ref.upper()
    base_u = base.upper()
    ct = str(xg).upper().startswith("C")
    if ct and ref_u == "C":
        if base_u == "C":
            return 1
        if base_u == "T":
            return 0
    if (not ct) and ref_u == "G":
        if base_u == "G":
            return 1
        if base_u == "A":
            return 0
    return -1


def meth_call_prefer_xm(xm_char: str | None, ref: str, base: str, xg: str) -> int:
    """Prefer XM when present and informative; otherwise sequence+XG."""
    if xm_char:
        tagged = meth_call_from_xm(xm_char)
        if tagged >= 0:
            return tagged
    return meth_call_from_seq_xg(ref, base, xg)
