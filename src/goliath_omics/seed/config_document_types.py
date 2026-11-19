"""Register the typed document schema for Omics pipeline profiles (generic cfg document editor).

GoliathApp's config editors only edit a cfg kind's documents once the owning app registers a
catalog data type for them (``cfg.sp_register_kind_document_type``). This seeds
``omics/PipelineProfile`` from ``schemas/config/profile.schema.json``, with external ``$ref``s
bundled in so the stored schema is self-contained. Idempotent.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PIPELINE_PROFILE_TYPE = "omics/PipelineProfile"


def _schemas_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "schemas" / "config"


def _pointer(doc: Any, pointer: str) -> Any:
    node = doc
    for part in [p for p in pointer.lstrip("/").split("/") if p]:
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _rewrite_local_refs(node: Any, prefix: str) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                out[key] = "#/$defs/" + prefix + value[len("#/$defs/") :]
            else:
                out[key] = _rewrite_local_refs(value, prefix)
        return out
    if isinstance(node, list):
        return [_rewrite_local_refs(v, prefix) for v in node]
    return node


def bundle_schema(path: Path) -> dict[str, Any]:
    """Inline ``other.schema.json#/pointer`` refs; their ``$defs`` move into the root, prefixed."""
    root = json.loads(path.read_text(encoding="utf-8"))
    defs: dict[str, Any] = dict(root.get("$defs") or {})
    loaded: set[str] = set()

    def resolve(node: Any, base: Path) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                file_part, _, pointer = ref.partition("#")
                target_path = (base / file_part).resolve()
                other = json.loads(target_path.read_text(encoding="utf-8"))
                prefix = target_path.name.split(".")[0] + "__"
                if str(target_path) not in loaded:
                    loaded.add(str(target_path))
                    for name, sub in (other.get("$defs") or {}).items():
                        defs[prefix + name] = resolve(_rewrite_local_refs(sub, prefix), target_path.parent)
                target = _rewrite_local_refs(_pointer(other, pointer), prefix)
                merged = {**resolve(target, target_path.parent), **{k: v for k, v in node.items() if k != "$ref"}}
                return merged
            return {k: resolve(v, base) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v, base) for v in node]
        return node

    body = resolve({k: v for k, v in root.items() if k != "$defs"}, path.parent)
    defs = {name: resolve(sub, path.parent) for name, sub in defs.items()}
    if defs:
        body["$defs"] = defs
    body.pop("$schema", None)
    return body


def pipeline_profile_schema() -> dict[str, Any]:
    return bundle_schema(_schemas_dir() / "profile.schema.json")


def _connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
    )


def seed(conn) -> bool:
    """Upsert the data type and register it for the pipeline_profile kind. False if unsupported."""
    schema = pipeline_profile_schema()
    text = json.dumps(schema, sort_keys=True)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('cfg.sp_register_kind_document_type(text,text)') IS NOT NULL")
        row = cur.fetchone()
        if not (row and row[0]):
            return False
        cur.execute(
            "SELECT 1 FROM wf.wf_repo_upsert_data_type(%s, '1', 'published', 'object', NULL, '1', %s, %s::jsonb)",
            (PIPELINE_PROFILE_TYPE, content_hash, text),
        )
        cur.execute("SELECT cfg.sp_register_kind_document_type('pipeline_profile', %s)", (PIPELINE_PROFILE_TYPE,))
    conn.commit()
    return True


def main() -> int:
    if not os.environ.get("PGHOST"):
        print("Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-config-document-types", file=sys.stderr)
        return 2
    with _connect() as conn:
        ok = seed(conn)
    if ok:
        print(f"Registered {PIPELINE_PROFILE_TYPE} for cfg kind pipeline_profile")
    else:
        print("cfg.sp_register_kind_document_type is not installed; skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
