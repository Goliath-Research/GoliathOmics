"""Sample path resolution for gene selection validation cohorts."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List


def _resolve_entry(entry: str, base: Path) -> str:
    entry = entry.strip()
    if not entry:
        return ""
    if not _looks_like_absolute_path(entry):
        return str(base.resolve() / entry)
    return entry


def _looks_like_absolute_path(entry: str) -> bool:
    if not entry:
        return False
    e = entry.strip()
    return e.startswith("/") or (len(e) > 1 and e[1] == ":")


def load_and_resolve_sample_paths(csv_path: str | Path, base_path: str | Path) -> List[str]:
    """Load sample paths from CSV and resolve relative names against base_path."""
    base = Path(base_path).resolve()
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Sample list file not found: {path}")
    out: List[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        if first_row is None:
            return out
        if first_row and first_row[0].strip().lower() in (
            "sample",
            "path",
            "sample_path",
            "name",
            "id",
        ):
            for row in reader:
                if row and row[0].strip():
                    r = _resolve_entry(row[0], base)
                    if r:
                        out.append(r)
        else:
            if first_row and first_row[0].strip():
                r = _resolve_entry(first_row[0], base)
                if r:
                    out.append(r)
            for row in reader:
                if row and row[0].strip():
                    r = _resolve_entry(row[0], base)
                    if r:
                        out.append(r)
    return out
