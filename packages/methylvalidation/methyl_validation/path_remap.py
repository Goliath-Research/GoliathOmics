"""
Prefix remapping for pipeline project JSON (e.g. different NFS mount between machines).

User-authored cohort lists are usually CSV/txt files of **sample basenames** (no directory).
The pipeline resolves those to ``samples_base_path / basename`` (see ``_resolve_sample_paths``).
For that layout, remapping **JSON strings** such as ``samples_base_path`` (project or per-group)
is what fixes absolute HDF5 paths; basename rows inside list files do not match path prefixes
and are left unchanged.

**MethylValidation** also writes per-run CSVs under Monte Carlo output: ``training_*.csv`` uses
basenames (same as above), but ``testing_*.csv`` uses a ``path`` column of **absolute**
directories (:func:`methyl_validation.project_gen.write_val_csv`). Those testing files must be
rewritten when moving between mounts; :func:`remap_cohort_list_files_in_project` handles them
when they appear in project JSON and rows match ``path_remap`` prefixes.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def remap_path_string(path: str, path_remap: Dict[str, str]) -> str:
    """
    Replace the longest matching old prefix in ``path_remap`` (same rule as methyl-classifier).
    """
    if not path_remap or not path:
        return path
    best_old: Optional[str] = None
    for old_prefix in path_remap:
        if path.startswith(old_prefix) and (best_old is None or len(old_prefix) > len(best_old)):
            best_old = old_prefix
    if best_old is not None:
        new_prefix = path_remap[best_old]
        rest = path[len(best_old) :].lstrip("/")
        return f"{new_prefix.rstrip('/')}/{rest}" if rest else new_prefix.rstrip("/")
    return path


def apply_path_remap_to_nested(obj: Any, path_remap: Dict[str, str]) -> None:
    """
    In-place: remap every string value in nested dicts/lists (typical project JSON).

    This updates path-like fields including ``samples_base_path`` and paths to cohort list
    files. It does not read or modify the contents of external CSV/txt/json list files.
    """
    if not path_remap:
        return
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                obj[k] = remap_path_string(v, path_remap)
            else:
                apply_path_remap_to_nested(v, path_remap)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = remap_path_string(v, path_remap)
            else:
                apply_path_remap_to_nested(v, path_remap)


_PATH_HEADER_CELLS = frozenset(
    {"sample", "path", "sample_path", "name", "id"}
)


def _remap_csv_sample_list_content(src: Path, path_remap: Dict[str, str]) -> Optional[str]:
    """
    Remap column 0 on each data row (skip first row if it looks like a header), matching
    ``_resolve_sample_paths`` and MV ``training_*.csv`` / ``testing_*.csv`` (``path`` header).
    """
    text = src.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return None
    first_cell = rows[0][0].strip().lower() if rows[0] and rows[0][0].strip() else ""
    start_data = 1 if first_cell in _PATH_HEADER_CELLS else 0
    changed = False
    new_rows: List[List[str]] = []
    for i, row in enumerate(rows):
        row = list(row)
        if i < start_data or not row or not row[0].strip():
            new_rows.append(row)
            continue
        old = row[0].strip()
        new = remap_path_string(old, path_remap)
        if new != old:
            changed = True
            row[0] = new
        new_rows.append(row)
    if not changed:
        return None
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerows(new_rows)
    return buf.getvalue()


def _remap_txt_sample_list_content(src: Path, path_remap: Dict[str, str]) -> Optional[str]:
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines()
    changed = False
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        new_line = remap_path_string(stripped, path_remap)
        if new_line != stripped:
            changed = True
        if line.strip() == line:
            out.append(new_line)
        else:
            lead = line[: len(line) - len(line.lstrip())]
            out.append(lead + new_line)
    if not changed:
        return None
    body = "\n".join(out)
    if text.endswith("\n"):
        body += "\n"
    return body


def _remap_json_array_sample_list_content(src: Path, path_remap: Dict[str, str]) -> Optional[str]:
    raw = src.read_text(encoding="utf-8").strip()
    if not raw.startswith("["):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    changed = False
    new_list: List[Any] = []
    for x in data:
        s = str(x).strip()
        ns = remap_path_string(s, path_remap)
        if ns != s:
            changed = True
        new_list.append(ns)
    if not changed:
        return None
    return json.dumps(new_list, indent=2) + "\n"


def _cohort_list_suffixes() -> Tuple[str, ...]:
    return (".csv", ".txt", ".json")


def _collect_list_file_path_strings(obj: Any, out: List[str]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_list_file_path_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_list_file_path_strings(v, out)
    elif isinstance(obj, str):
        low = obj.lower()
        if any(low.endswith(s) for s in _cohort_list_suffixes()):
            out.append(obj)


def remap_cohort_list_files_in_project(
    project_dict: dict,
    path_remap: Dict[str, str],
    prod_dir: Path,
) -> None:
    """
    Copy sample-list files (.csv / .txt / JSON array) referenced in the project when their
    **content** contains absolute paths that match ``path_remap``. Updates ``project_dict``
    in place to point at copies under ``prod_dir / _path_remap_cohort_lists``.

    Basename-only lists (typical user cohort CSVs and MV ``training_*.csv``) produce no rewrites
    here. MV ``testing_*.csv`` (absolute ``path`` column) does.

    Call after :func:`apply_path_remap_to_nested` (and after any CLI
    ``samples_base_path`` override) so JSON paths point at files on this machine.
    """
    if not path_remap:
        return
    prod_dir = Path(prod_dir)
    candidates: List[str] = []
    _collect_list_file_path_strings(project_dict, candidates)
    seen_resolved: Set[str] = set()
    resolved_to_new: Dict[str, str] = {}

    dest_root = prod_dir / "_path_remap_cohort_lists"
    for p_str in candidates:
        try:
            src = Path(p_str)
            if not src.is_file():
                continue
        except OSError:
            continue
        key = str(src.resolve())
        if key in seen_resolved:
            continue
        seen_resolved.add(key)

        suf = src.suffix.lower()
        new_body: Optional[str] = None
        if suf == ".csv":
            new_body = _remap_csv_sample_list_content(src, path_remap)
        elif suf == ".txt":
            new_body = _remap_txt_sample_list_content(src, path_remap)
        elif suf == ".json":
            new_body = _remap_json_array_sample_list_content(src, path_remap)
        if new_body is None:
            continue

        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / src.name
        if dest.exists():
            dest = dest_root / f"{src.stem}_{abs(hash(key)) % (10**8)}{src.suffix}"
        dest.write_text(new_body, encoding="utf-8")
        resolved_to_new[key] = str(dest.resolve())

    def replace_paths(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, str):
                    try:
                        rk = str(Path(v).resolve())
                        if rk in resolved_to_new:
                            obj[k] = resolved_to_new[rk]
                    except OSError:
                        pass
                else:
                    replace_paths(v)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str):
                    try:
                        rk = str(Path(v).resolve())
                        if rk in resolved_to_new:
                            obj[i] = resolved_to_new[rk]
                    except OSError:
                        pass
                else:
                    replace_paths(v)

    replace_paths(project_dict)
