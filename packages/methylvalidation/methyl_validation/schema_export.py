"""
Export JSON Schema artifacts for all registered pipeline config Pydantic models.

Usage:
  methyl-export-config-schemas              # write/update schemas/config/*.json
  methyl-export-config-schemas --check      # fail if artifacts are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from pydantic import BaseModel

from methyl_utils.repo_paths import repo_schemas_dir

from .config_schema_registry import ConfigSchemaSpec, list_config_schema_specs


def repo_schemas_config_dir() -> Path:
    """``<repo>/schemas/config``."""
    return repo_schemas_dir("config", start=Path(__file__))


def generate_schema_dict(model: type[BaseModel], *, title: str | None = None) -> Dict[str, Any]:
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    if title:
        schema["title"] = title
    return schema


def schema_to_canonical_json(schema: Dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def output_path_for_spec(spec: ConfigSchemaSpec, schemas_root: Path | None = None) -> Path:
    root = schemas_root if schemas_root is not None else repo_schemas_config_dir()
    return root / spec.filename


def export_spec(
    spec: ConfigSchemaSpec,
    *,
    schemas_root: Path | None = None,
    write: bool = True,
) -> Tuple[Path, str]:
    model = spec.load_model()
    schema = generate_schema_dict(model, title=spec.title or spec.class_name)
    text = schema_to_canonical_json(schema)
    path = output_path_for_spec(spec, schemas_root)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path, text


def export_all_config_schemas(
    *,
    schemas_root: Path | None = None,
    write: bool = True,
    specs: Iterable[ConfigSchemaSpec] | None = None,
) -> List[Path]:
    written: List[Path] = []
    for spec in specs or list_config_schema_specs():
        path, _ = export_spec(spec, schemas_root=schemas_root, write=write)
        written.append(path)
    return written


def check_config_schema_drift(
    *,
    schemas_root: Path | None = None,
    specs: Iterable[ConfigSchemaSpec] | None = None,
) -> List[str]:
    """Return human-readable drift messages; empty list means all artifacts match models."""
    errors: List[str] = []
    for spec in specs or list_config_schema_specs():
        path, expected = export_spec(spec, schemas_root=schemas_root, write=False)
        if not path.is_file():
            errors.append(f"missing schema artifact: {path} (run methyl-export-config-schemas)")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append(
                f"stale schema artifact: {path} "
                f"(regenerate with: methyl-export-config-schemas)"
            )
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export JSON Schema for pipeline config Pydantic models (schemas/config/)."
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
        help="Override output directory (default: <repo>/schemas/config).",
    )
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve() if args.output_root else repo_schemas_config_dir()

    if args.check:
        drift = check_config_schema_drift(schemas_root=root)
        if drift:
            for msg in drift:
                print(msg, file=sys.stderr)
            print(
                f"Config schema drift check failed ({len(drift)} issue(s)).",
                file=sys.stderr,
            )
            return 1
        print(f"Config schema drift check passed ({len(list_config_schema_specs())} artifacts).")
        return 0

    paths = export_all_config_schemas(schemas_root=root, write=True)
    for p in paths:
        print(f"Wrote schema: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
