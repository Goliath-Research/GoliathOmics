"""``append_work_text`` for a stale in-memory ``work_share`` module.

Sisters can have ``work_share`` already imported without ``append_work_text``.
The fallback must still honor the multi-UID ``/work`` contract: share the path
and retry once on ``PermissionError``. A plain ``Path.open("a")`` aborts on a
root-owned log or leaves a file later sisters cannot append to.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any


def stale_append_work_text(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    work_share: ModuleType | Any | None = None,
) -> Path:
    """Append + share + one EACCES retry using whatever ``work_share`` still exports."""
    if work_share is None:
        import methyl_worker.work_share as work_share  # noqa: PLC0415

    p = Path(path)
    payload = text if text.endswith("\n") else text + "\n"

    open_work = getattr(work_share, "open_work", None)
    share_path = getattr(work_share, "share_work_path", None)
    if callable(open_work):
        with open_work(p, "a", encoding=encoding) as fh:
            fh.write(payload)
        if p.exists() and callable(share_path):
            share_path(p)
        return p

    def _share_for_write() -> None:
        share_anc = getattr(work_share, "share_work_ancestors", None)
        if callable(share_anc):
            share_anc(p)
        elif callable(share_path) and p.parent.exists():
            share_path(p.parent)
        else:
            share_tree = getattr(work_share, "share_work_tree", None)
            if callable(share_tree) and p.parent.exists():
                share_tree(p.parent)
        if p.exists() and callable(share_path):
            share_path(p)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _share_for_write()
        p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        _share_for_write()
    try:
        with p.open("a", encoding=encoding) as fh:
            fh.write(payload)
    except PermissionError:
        _share_for_write()
        with p.open("a", encoding=encoding) as fh:
            fh.write(payload)
    if p.exists() and callable(share_path):
        share_path(p)
    return p
