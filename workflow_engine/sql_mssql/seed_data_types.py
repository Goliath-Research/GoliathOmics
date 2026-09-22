#!/usr/bin/env python3
"""
Seed wf.data_type (+ fields) from schemas/domain, schemas/tasks, and
schemas/config (SamplePrep guardrail editor types).

Stores the JSON Schema document on wf.data_type.schema_json (SchemaPropertyGrid
bind target) and flattens properties into data_type_field as a SQL index.

Usage:
  source .venv/bin/activate
  PYTHONPATH=workflow_engine:workers python workflow_engine/sql_mssql/seed_data_types.py
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DIR = REPO_ROOT / "schemas" / "domain"
TASKS_DIR = REPO_ROOT / "schemas" / "tasks"
CONFIG_DIR = REPO_ROOT / "schemas" / "config"
REGISTRY_PATH = DOMAIN_DIR / "registry.json"
WF_ENGINE = REPO_ROOT / "workflow_engine"

CONFIG_GUARDRAIL_TYPES = (
    ("sample_prep_guardrails", "sample_prep_guardrails.schema.json"),
    ("sample_prep_guardrails_overlay", "sample_prep_guardrails_overlay.schema.json"),
    ("study_action_config_overlay", "study_action_config_overlay.schema.json"),
)

sys.path.insert(0, str(REPO_ROOT / "workers"))
sys.path.insert(0, str(WF_ENGINE))

PRIMITIVE_KINDS = (
    "string",
    "int",
    "bool",
    "number",
    "datetime",
    "bytes",
    "any",
)

JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _hash(doc: Any) -> str:
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def primitive_json_schema(kind: str, *, title: Optional[str] = None) -> Dict[str, Any]:
    """Tiny JSON Schema so a primitive type can still bind SchemaPropertyGrid."""
    name = title or kind
    type_map = {
        "string": {"type": "string"},
        "int": {"type": "integer"},
        "bool": {"type": "boolean"},
        "number": {"type": "number"},
        "datetime": {"type": "string", "format": "date-time"},
        "bytes": {"type": "string", "contentEncoding": "base64"},
    }
    if kind == "any":
        return {"$schema": JSON_SCHEMA_DRAFT, "title": name}
    body = type_map.get(kind, {"type": "string"})
    return {"$schema": JSON_SCHEMA_DRAFT, "title": name, **body}


def editor_schema_document(
    type_name: str,
    body: Dict[str, Any],
    *,
    defs: Optional[Dict[str, Any]] = None,
    root_document: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """JSON Schema document SchemaPropertyGrid can Bind() without per-type UI.

    Root types store the full file (including $defs). Nested $defs types store
    the fragment plus sibling $defs so internal $ref still resolve.
    """
    if root_document is not None and body is root_document:
        doc = copy.deepcopy(root_document)
        doc.setdefault("$schema", JSON_SCHEMA_DRAFT)
        doc.setdefault("title", type_name)
        return doc

    doc: Dict[str, Any] = {"$schema": JSON_SCHEMA_DRAFT, "title": type_name}
    skip = {"$schema", "$id", "title"}
    for key, value in body.items():
        if key not in skip:
            doc[key] = copy.deepcopy(value)
    sibling_defs = defs
    if sibling_defs is None and root_document is not None:
        raw = root_document.get("$defs")
        sibling_defs = raw if isinstance(raw, dict) else None
    if sibling_defs:
        merged = copy.deepcopy(sibling_defs)
        existing = doc.get("$defs")
        if isinstance(existing, dict):
            merged.update(existing)
        doc["$defs"] = merged
    return doc


def array_json_schema(
    type_name: str,
    element_name: str,
    *,
    defs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if element_name in PRIMITIVE_KINDS or element_name == "any":
        items = primitive_json_schema(element_name)
        items.pop("$schema", None)
        items.pop("title", None)
    else:
        items = {"$ref": f"#/$defs/{element_name}"}
    doc: Dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "title": type_name,
        "type": "array",
        "items": items,
    }
    if defs:
        doc["$defs"] = copy.deepcopy(defs)
    return doc


def enum_json_schema(type_name: str, values: List[str], *, description: Optional[str] = None) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "title": type_name,
        "type": "string",
        "enum": list(values),
    }
    if description:
        doc["description"] = description
    return doc


def _schema_payload(schema: Optional[Dict[str, Any]]) -> Optional[str]:
    if schema is None:
        return None
    return json.dumps(schema, separators=(",", ":"), ensure_ascii=True)


def _unwrap_nullable(schema: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Return (inner_schema, nullable) for anyOf/oneOf null unions."""
    for key in ("anyOf", "oneOf"):
        arms = schema.get(key)
        if not isinstance(arms, list):
            continue
        non_null = [a for a in arms if isinstance(a, dict) and a.get("type") != "null"]
        has_null = any(isinstance(a, dict) and a.get("type") == "null" for a in arms)
        if has_null and len(non_null) == 1:
            return non_null[0], True
    return schema, False


def _json_type_to_kind(schema: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Map JSON Schema fragment → (kind, element_or_ref_name)."""
    schema, _ = _unwrap_nullable(schema)
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return "object", ref.rsplit("/", 1)[-1]
    if isinstance(ref, str) and not ref.startswith("#"):
        # External — treat as named type stem
        name = Path(ref).stem
        for suffix in (".schema",):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return "object", name

    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        t = non_null[0] if non_null else "any"
    if t == "string":
        if "enum" in schema:
            return "enum", None
        if schema.get("format") in ("date-time", "date"):
            return "datetime", None
        return "string", None
    if t == "integer":
        return "int", None
    if t == "number":
        return "number", None
    if t == "boolean":
        return "bool", None
    if t == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        ikind, iref = _json_type_to_kind(items or {"type": "any"})
        return "array", iref or ikind
    if t == "object" or "properties" in schema:
        title = schema.get("title")
        return "object", str(title) if title else None
    if "enum" in schema:
        return "enum", None
    return "any", None


def _ensure_type(
    db,
    *,
    name: str,
    kind: str,
    version: str = "1",
    element_type_name: Optional[str] = None,
    content_hash: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    backend = os.environ.get("BACKEND_DB", "mssql").lower()
    payload = _schema_payload(schema)
    if content_hash is None and schema is not None:
        content_hash = _hash(schema)
    if backend == "postgres":
        db._exec_proc(  # noqa: SLF001
            "SELECT * FROM wf.wf_repo_upsert_data_type(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (name, version, "published", kind, element_type_name, "1", content_hash, payload),
        )
    else:
        db._exec_proc(  # noqa: SLF001
            "EXEC wf.wf_repo_upsert_data_type "
            "@name=?, @version=?, @status=?, @kind=?, "
            "@element_type_name=?, @element_type_version=?, @content_hash=?, @schema_json=?",
            (name, version, "published", kind, element_type_name, "1", content_hash, payload),
        )


def _replace_fields(
    db, *, type_name: str, fields: List[Dict[str, Any]], version: str = "1"
) -> None:
    backend = os.environ.get("BACKEND_DB", "mssql").lower()
    payload = json.dumps(fields, separators=(",", ":"))
    if backend == "postgres":
        db._exec_proc(  # noqa: SLF001
            "SELECT * FROM wf.wf_repo_replace_data_type_fields(%s,%s,%s::jsonb)",
            (type_name, version, payload),
        )
    else:
        db._exec_proc(  # noqa: SLF001
            "EXEC wf.wf_repo_replace_data_type_fields "
            "@type_name=?, @type_version=?, @fields_json=?",
            (type_name, version, payload),
        )


def _replace_enum(
    db, *, type_name: str, values: List[str], version: str = "1"
) -> None:
    backend = os.environ.get("BACKEND_DB", "mssql").lower()
    payload = json.dumps(values, separators=(",", ":"))
    if backend == "postgres":
        db._exec_proc(  # noqa: SLF001
            "SELECT * FROM wf.wf_repo_replace_data_type_enum_values(%s,%s,%s::jsonb)",
            (type_name, version, payload),
        )
    else:
        db._exec_proc(  # noqa: SLF001
            "EXEC wf.wf_repo_replace_data_type_enum_values "
            "@type_name=?, @type_version=?, @values_json=?",
            (type_name, version, payload),
        )


def _bind_action(
    db,
    *,
    action_name: str,
    input_type: Optional[str],
    output_type: Optional[str],
    implementation_status: str = "present",
    can_pause: bool = False,
    can_continue: bool = False,
    can_stop: bool = True,
) -> None:
    backend = os.environ.get("BACKEND_DB", "mssql").lower()
    if backend == "postgres":
        db._exec_proc(  # noqa: SLF001
            "SELECT * FROM wf.wf_repo_bind_action_types(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                action_name,
                input_type,
                output_type,
                "1",
                implementation_status,
                can_pause,
                can_continue,
                can_stop,
            ),
        )
    else:
        db._exec_proc(  # noqa: SLF001
            "EXEC wf.wf_repo_bind_action_types "
            "@action_name=?, @input_type_name=?, @output_type_name=?, @type_version=?, "
            "@implementation_status=?, @can_pause=?, @can_continue=?, @can_stop=?",
            (
                action_name,
                input_type,
                output_type,
                "1",
                implementation_status,
                1 if can_pause else 0,
                1 if can_continue else 0,
                1 if can_stop else 0,
            ),
        )


def seed_primitives(db) -> int:
    for kind in PRIMITIVE_KINDS:
        _ensure_type(db, name=kind, kind=kind, schema=primitive_json_schema(kind))
    print(f"upserted {len(PRIMITIVE_KINDS)} primitive data_types")
    return len(PRIMITIVE_KINDS)


def _seed_object_from_schema(
    db,
    *,
    type_name: str,
    schema: Dict[str, Any],
    defs: Optional[Dict[str, Any]] = None,
    root_document: Optional[Dict[str, Any]] = None,
) -> None:
    defs = defs or (schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {})
    body = schema
    if type_name in defs and isinstance(defs[type_name], dict):
        body = defs[type_name]
    editor_doc = editor_schema_document(
        type_name,
        schema if schema is root_document else body,
        defs=defs or None,
        root_document=root_document,
    )
    kind, _ = _json_type_to_kind(
        body if "type" in body or "properties" in body or "enum" in body else {"type": "object"}
    )
    if kind == "enum" or "enum" in body:
        values = [str(v) for v in (body.get("enum") or [])]
        _ensure_type(
            db,
            name=type_name,
            kind="enum",
            schema=enum_json_schema(type_name, values, description=body.get("description")),
        )
        if values:
            _replace_enum(db, type_name=type_name, values=values)
        return

    _ensure_type(db, name=type_name, kind="object", schema=editor_doc)
    props = body.get("properties") if isinstance(body.get("properties"), dict) else {}
    required = set(body.get("required") or [])
    fields: List[Dict[str, Any]] = []
    for ordinal, (fname, fschema) in enumerate(props.items()):
        if not isinstance(fschema, dict):
            fschema = {"type": "any"}
        fkind, fref = _json_type_to_kind(fschema)
        if fkind == "enum":
            enum_type = f"{type_name}.{fname}"
            vals = fschema.get("enum") or _unwrap_nullable(fschema)[0].get("enum") or []
            str_vals = [str(v) for v in vals]
            _ensure_type(
                db,
                name=enum_type,
                kind="enum",
                schema=enum_json_schema(enum_type, str_vals, description=fschema.get("description")),
            )
            if str_vals:
                _replace_enum(db, type_name=enum_type, values=str_vals)
            field_type_name = enum_type
        elif fkind == "array":
            elem_name = fref or "any"
            if elem_name not in PRIMITIVE_KINDS and elem_name != "any":
                _ensure_type(db, name=elem_name, kind="object")
            arr_name = f"{type_name}.{fname}.array"
            _ensure_type(
                db,
                name=arr_name,
                kind="array",
                element_type_name=elem_name,
                schema=array_json_schema(arr_name, elem_name, defs=defs or None),
            )
            field_type_name = arr_name
        elif fkind == "object" and fref:
            field_type_name = fref
            _ensure_type(db, name=field_type_name, kind="object")
        else:
            field_type_name = fkind if fkind in PRIMITIVE_KINDS else "any"
            if field_type_name not in PRIMITIVE_KINDS:
                field_type_name = "any"
        fields.append(
            {
                "field_name": fname,
                "field_type_name": field_type_name,
                "field_type_version": "1",
                "required": fname in required,
                "ordinal": ordinal,
            }
        )
    if fields:
        _replace_fields(db, type_name=type_name, fields=fields)


def seed_domain_types(db) -> int:
    if not REGISTRY_PATH.is_file():
        print(f"skip domain types: missing {REGISTRY_PATH}")
        return 0
    reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    schemas_map = reg.get("schemas") or {}
    types = reg.get("types") or list(schemas_map.keys())
    # Seed $defs-named types first by loading each file
    count = 0
    for type_name in types:
        fname = schemas_map.get(type_name)
        if not fname:
            continue
        path = DOMAIN_DIR / fname
        if not path.is_file():
            print(f"skip missing domain schema {path}", file=sys.stderr)
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
        for def_name, def_schema in defs.items():
            if isinstance(def_schema, dict):
                _seed_object_from_schema(
                    db,
                    type_name=def_name,
                    schema=def_schema,
                    defs=defs,
                    root_document=schema,
                )
        _seed_object_from_schema(
            db, type_name=type_name, schema=schema, defs=defs, root_document=schema
        )
        count += 1
        print(f"upserted domain data_type:{type_name}")
    return count


def seed_config_types(db) -> int:
    """Seed SamplePrep guardrail editor schemas from schemas/config."""
    count = 0
    for type_name, filename in CONFIG_GUARDRAIL_TYPES:
        path = CONFIG_DIR / filename
        if not path.is_file():
            print(f"skip missing config schema {path}", file=sys.stderr)
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
        for def_name, def_schema in defs.items():
            if isinstance(def_schema, dict):
                _seed_object_from_schema(
                    db,
                    type_name=def_name,
                    schema=def_schema,
                    defs=defs,
                    root_document=schema,
                )
        _seed_object_from_schema(
            db, type_name=type_name, schema=schema, defs=defs, root_document=schema
        )
        count += 1
        print(f"upserted config data_type:{type_name}")
    return count


def seed_task_types(db) -> Tuple[int, int]:
    from methyl_worker.task_schema_registry import list_task_schema_specs

    if not TASKS_DIR.is_dir():
        raise SystemExit(f"Missing {TASKS_DIR}; run methyl-export-task-schemas first.")

    type_count = 0
    bind_count = 0
    for spec in list_task_schema_specs():
        in_name = f"{spec.action_name}.input"
        out_name = f"{spec.action_name}.output"
        for direction, filename, type_name in (
            ("input", spec.input_filename, in_name),
            ("output", spec.output_filename, out_name),
        ):
            path = TASKS_DIR / filename
            if not path.is_file():
                print(f"skip missing {path}", file=sys.stderr)
                continue
            schema = json.loads(path.read_text(encoding="utf-8"))
            defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
            for def_name, def_schema in defs.items():
                if isinstance(def_schema, dict):
                    _seed_object_from_schema(
                        db,
                        type_name=def_name,
                        schema=def_schema,
                        defs=defs,
                        root_document=schema,
                    )
            _seed_object_from_schema(
                db, type_name=type_name, schema=schema, defs=defs, root_document=schema
            )
            type_count += 1
            print(f"upserted task data_type:{type_name}")

        # Control metadata from catalog if available
        can_pause = can_continue = False
        can_stop = True
        try:
            from methyl_worker.action_catalog import ACTION_CATALOG

            entry = ACTION_CATALOG.get(spec.action_name)
            if entry is not None:
                control = getattr(entry, "control", None) or {}
                if isinstance(control, dict):
                    can_pause = bool(control.get("can_pause", False))
                    can_continue = bool(control.get("can_continue", False))
                    can_stop = bool(control.get("can_stop", True))
        except Exception:  # noqa: BLE001
            pass

        try:
            _bind_action(
                db,
                action_name=spec.action_name,
                input_type=in_name,
                output_type=out_name,
                can_pause=can_pause,
                can_continue=can_continue,
                can_stop=can_stop,
            )
            bind_count += 1
            print(f"bound action types {spec.action_name} -> {in_name} / {out_name}")
        except Exception as exc:  # noqa: BLE001
            print(f"warn: bind {spec.action_name} failed: {exc}", file=sys.stderr)
    return type_count, bind_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("mssql", "postgres"), default=None)
    args = parser.parse_args()
    if args.backend:
        os.environ["BACKEND_DB"] = args.backend

    from rest.connection import resolve_connection_config
    from rest.db import open_gateway_db

    # Load dotenv like sync script
    for env_path in (
        Path.home() / "mssql-mcp-server" / ".env",
        Path.home() / ".secrets" / "azure_sql.env",
    ):
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if os.environ.get("DB_SERVER") and not os.environ.get("AZURE_SQL_SERVER"):
        os.environ["AZURE_SQL_SERVER"] = os.environ["DB_SERVER"]
    if os.environ.get("DB_DATABASE") and not os.environ.get("AZURE_SQL_DB"):
        os.environ["AZURE_SQL_DB"] = os.environ["DB_DATABASE"]
    if os.environ.get("DB_USER") and not os.environ.get("AZURE_SQL_USER"):
        os.environ["AZURE_SQL_USER"] = os.environ["DB_USER"]
    if os.environ.get("DB_PASSWORD") and not os.environ.get("AZURE_SQL_PASSWORD"):
        os.environ["AZURE_SQL_PASSWORD"] = os.environ["DB_PASSWORD"]

    db = open_gateway_db(resolve_connection_config())
    try:
        n_prim = seed_primitives(db)
        n_dom = seed_domain_types(db)
        n_cfg = seed_config_types(db)
        n_task, n_bind = seed_task_types(db)
        print(
            f"data_type seed complete primitives={n_prim} domain={n_dom} "
            f"config={n_cfg} task_types={n_task} action_binds={n_bind}"
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
