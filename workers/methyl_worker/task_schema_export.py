"""
Export JSON Schema artifacts for registered workflow action task models.

Usage:
  methyl-export-task-schemas              # write/update schemas/tasks/*.json
  methyl-export-task-schemas --check      # fail if artifacts are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from pydantic import BaseModel

from methyl_utils.repo_paths import repo_schemas_dir

from .task_schema_registry import TaskSchemaSpec, list_task_schema_specs


def repo_schemas_tasks_dir() -> Path:
    return repo_schemas_dir("tasks", start=Path(__file__))


def generate_schema_dict(model: type[BaseModel], *, title: str | None = None) -> Dict[str, Any]:
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    if title:
        schema.setdefault("title", title)
    return schema


def schema_to_canonical_json(schema: Dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def export_spec_direction(
    spec: TaskSchemaSpec,
    *,
    direction: str,
    schemas_root: Path | None = None,
    write: bool = True,
) -> Tuple[Path, str]:
    if direction == "input":
        model = spec.load_input_model()
        filename = spec.input_filename
        title = f"{spec.action_name}Input"
    elif direction == "output":
        model = spec.load_output_model()
        filename = spec.output_filename
        title = f"{spec.action_name}Output"
    else:
        raise ValueError(f"invalid direction: {direction!r}")

    schema = generate_schema_dict(model, title=title)
    text = schema_to_canonical_json(schema)
    root = schemas_root if schemas_root is not None else repo_schemas_tasks_dir()
    path = root / filename
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path, text


def export_all_task_schemas(
    *,
    schemas_root: Path | None = None,
    write: bool = True,
    specs: Iterable[TaskSchemaSpec] | None = None,
) -> List[Path]:
    written: List[Path] = []
    for spec in specs or list_task_schema_specs():
        for direction in ("input", "output"):
            path, _ = export_spec_direction(spec, direction=direction, schemas_root=schemas_root, write=write)
            written.append(path)
    return written


def check_task_schema_drift(
    *,
    schemas_root: Path | None = None,
    specs: Iterable[TaskSchemaSpec] | None = None,
) -> List[str]:
    errors: List[str] = []
    for spec in specs or list_task_schema_specs():
        for direction in ("input", "output"):
            path, expected = export_spec_direction(
                spec, direction=direction, schemas_root=schemas_root, write=False
            )
            if not path.is_file():
                errors.append(f"missing schema artifact: {path} (run methyl-export-task-schemas)")
                continue
            actual = path.read_text(encoding="utf-8")
            if actual != expected:
                errors.append(
                    f"stale schema artifact: {path} (regenerate with: methyl-export-task-schemas)"
                )
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export JSON Schema for workflow action task models (schemas/tasks/)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write files; exit non-zero if committed schemas differ from current models.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Override output directory (default: <repo>/schemas/tasks).",
    )
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve() if args.output_root else repo_schemas_tasks_dir()

    if args.check:
        drift = check_task_schema_drift(schemas_root=root)
        if drift:
            for msg in drift:
                print(msg, file=sys.stderr)
            print(f"Task schema drift check failed ({len(drift)} issue(s)).", file=sys.stderr)
            return 1
        n = len(list_task_schema_specs()) * 2
        print(f"Task schema drift check passed ({n} artifacts).")
        return 0

    paths = export_all_task_schemas(schemas_root=root, write=True)
    for p in paths:
        print(f"Wrote schema: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
