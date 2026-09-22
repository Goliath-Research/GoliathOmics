"""
Export JSON Schema artifacts for methyl_domain types.

Usage:
  methyl-export-domain-schemas
  methyl-export-domain-schemas --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from pydantic import BaseModel, TypeAdapter

from methyl_utils.repo_paths import repo_schemas_dir

from .program import DomainProgram
from .fastq_storage import FastqSourceLocation, FastqStorageDefaults
from .types import DOMAIN_MODEL_BY_TYPE, DOMAIN_TYPE_NAMES


def repo_schemas_domain_dir() -> Path:
    return repo_schemas_dir("domain", start=Path(__file__))


def generate_schema_dict(model: type[BaseModel], *, title: str | None = None) -> Dict[str, Any]:
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    if title:
        schema.setdefault("title", title)
    return schema


def schema_to_canonical_json(schema: Dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def _filename_for_type(type_name: str) -> str:
    snake = "".join(
        f"_{c.lower()}" if c.isupper() else c for c in type_name
    ).lstrip("_")
    return f"{snake}.schema.json"


def generate_fastq_storage_schema_dict() -> Dict[str, Any]:
    defaults = TypeAdapter(FastqStorageDefaults).json_schema()
    source = TypeAdapter(FastqSourceLocation).json_schema()
    defs = {}
    defs.update(defaults.get("$defs") or {})
    defs.update(source.get("$defs") or {})
    schema: Dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FastqStorage",
        "$defs": defs,
        "FastqStorageDefaults": {
            k: v for k, v in defaults.items() if k not in ("$defs",)
        },
        "FastqSourceLocation": {
            k: v for k, v in source.items() if k not in ("$defs",)
        },
    }
    return schema


def export_all_domain_schemas(
    *,
    schemas_root: Path | None = None,
    write: bool = True,
) -> List[Path]:
    root = schemas_root if schemas_root is not None else repo_schemas_domain_dir()
    written: List[Path] = []

    for type_name, model in sorted(DOMAIN_MODEL_BY_TYPE.items()):
        schema = generate_schema_dict(model, title=type_name)
        path = root / _filename_for_type(type_name)
        text = schema_to_canonical_json(schema)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        written.append(path)

    program_schema = generate_schema_dict(DomainProgram, title="DomainProgram")
    program_path = root / "domain_program.schema.json"
    program_text = schema_to_canonical_json(program_schema)
    if write:
        program_path.write_text(program_text, encoding="utf-8")
    written.append(program_path)

    registry = {
        "version": 1,
        "types": list(DOMAIN_TYPE_NAMES),
        "schemas": {
            name: _filename_for_type(name) for name in sorted(DOMAIN_MODEL_BY_TYPE)
        },
        "domain_program": "domain_program.schema.json",
    }
    registry_path = root / "registry.json"
    registry_text = schema_to_canonical_json(registry)
    if write:
        registry_path.write_text(registry_text, encoding="utf-8")
    written.append(registry_path)

    fastq_path = root / "fastq_storage.schema.json"
    fastq_text = schema_to_canonical_json(generate_fastq_storage_schema_dict())
    if write:
        fastq_path.write_text(fastq_text, encoding="utf-8")
    written.append(fastq_path)

    return written


def check_domain_schema_drift(*, schemas_root: Path | None = None) -> List[str]:
    errors: List[str] = []
    root = schemas_root if schemas_root is not None else repo_schemas_domain_dir()

    for type_name, model in sorted(DOMAIN_MODEL_BY_TYPE.items()):
        path = root / _filename_for_type(type_name)
        expected = schema_to_canonical_json(generate_schema_dict(model, title=type_name))
        if not path.is_file():
            errors.append(f"missing schema artifact: {path}")
            continue
        if path.read_text(encoding="utf-8") != expected:
            errors.append(f"stale schema artifact: {path}")

    program_path = root / "domain_program.schema.json"
    program_expected = schema_to_canonical_json(
        generate_schema_dict(DomainProgram, title="DomainProgram")
    )
    if not program_path.is_file():
        errors.append(f"missing schema artifact: {program_path}")
    elif program_path.read_text(encoding="utf-8") != program_expected:
        errors.append(f"stale schema artifact: {program_path}")

    fastq_path = root / "fastq_storage.schema.json"
    fastq_expected = schema_to_canonical_json(generate_fastq_storage_schema_dict())
    if not fastq_path.is_file():
        errors.append(f"missing schema artifact: {fastq_path}")
    elif fastq_path.read_text(encoding="utf-8") != fastq_expected:
        errors.append(f"stale schema artifact: {fastq_path}")

    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export methyl_domain JSON Schemas.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve() if args.output_root else repo_schemas_domain_dir()

    if args.check:
        drift = check_domain_schema_drift(schemas_root=root)
        if drift:
            for msg in drift:
                print(msg, file=sys.stderr)
            return 1
        n = len(DOMAIN_MODEL_BY_TYPE) + 1
        print(f"Domain schema drift check passed ({n} artifacts).")
        return 0

    paths = export_all_domain_schemas(schemas_root=root, write=True)
    for p in paths:
        print(f"Wrote schema: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
