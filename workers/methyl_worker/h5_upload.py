"""Deprecated — use ``sample_archive``."""

from __future__ import annotations

from .sample_archive import (
    archive_from_task_input,
    upload_from_task_input,
    upload_h5_files,
    upload_h5_from_task_input,
)

__all__ = [
    "upload_h5_files",
    "upload_from_task_input",
    "upload_h5_from_task_input",
    "archive_from_task_input",
]
