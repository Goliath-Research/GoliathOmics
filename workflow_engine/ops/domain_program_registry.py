"""Register DomainProgram source JSON in cfg.domain_program (portal list UI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def register_domain_program_after_deploy(
    db: Any,
    *,
    program_document: dict[str, Any],
    workflow_def_id: int | None,
    workflow_version_id: int | None,
    status: str = "published",
) -> int:
    name = str(program_document.get("name") or "").strip()
    if not name:
        raise ValueError("domain program document missing name")
    version = str(program_document.get("programVersion") or program_document.get("version") or "1")

    with db._connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM cfg.cfg_repo_upsert(%s, %s, %s, %s, %s::jsonb)",
                ("domain_program", name, version, status, json.dumps(program_document)),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"cfg_repo_upsert returned no row for domain_program:{name}@{version}")
            program_id = int(row[0])
            if status == "published":
                cur.execute(
                    "SELECT id FROM cfg.cfg_repo_publish(%s, %s, %s)",
                    ("domain_program", name, version),
                )
                cur.fetchone()
            if workflow_def_id is not None or workflow_version_id is not None:
                cur.execute(
                    """
                    UPDATE cfg.domain_program
                    SET workflow_def_id = COALESCE(%s, workflow_def_id),
                        compiled_workflow_version_id = COALESCE(%s, compiled_workflow_version_id),
                        updated_at_utc = (now() AT TIME ZONE 'utc')
                    WHERE name = %s AND version = %s
                    """,
                    (workflow_def_id, workflow_version_id, name, version),
                )
        conn.commit()
    return program_id


def load_program_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
