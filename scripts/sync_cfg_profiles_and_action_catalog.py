#!/usr/bin/env python3
"""Push git analytes / pipeline profiles / assay procedures into cfg.* (DB).

Intended behavior (docs/usage/19-config-registry.md):

- Upsert ``cfg.analyte`` from ``workflow_engine/domain/analytes/*.analyte.json``
- Upsert ``cfg.pipeline_profile`` from ``workflow_engine/domain/profiles/*.profile.json``
  (skip ``modes/`` overlays and ``*.example.json`` / ``site_*.example.json``)
- Upsert ``cfg.assay_procedure`` from ``workflow_engine/domain/profiles/procedures/*.procedure.json``
- Map each document's ``catalog`` block to cfg ``status`` (published vs retired)
- Bind assay ``analyte_id`` via ``cfg.cfg_repo_bind_assay_procedure`` when
  ``analyteExpectation`` is set
- Backfill study ``default_analyte_id`` via ``cfg.cfg_repo_backfill_study_analyte_defaults``
- Optionally re-seed ``wf.workflow_action`` (omit with ``--skip-seed``)

Uses ``cfg.cfg_repo_upsert`` / ``cfg.cfg_repo_publish`` (GoliathApp sql_pg) over
psycopg or the gateway DB layer when available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg_status(catalog: Any) -> str:
    try:
        sys.path.insert(0, str(REPO_ROOT / "workflow_engine"))
        # Prefer GoliathApp helper when present on PYTHONPATH
        from cfg.process_pack_catalog import cfg_status_for_catalog  # type: ignore

        status = cfg_status_for_catalog(catalog if isinstance(catalog, dict) else None)
        return status or "retired"
    except Exception:
        pass
    if not isinstance(catalog, dict):
        return "published"
    lifecycle = str(catalog.get("lifecycle") or "active").strip().lower()
    visibility = str(catalog.get("visibility") or "operator").strip().lower()
    if lifecycle == "deprecated" or visibility == "hidden":
        return "retired"
    if lifecycle in ("active", "internal") and visibility in ("operator", "advanced"):
        return "published"
    return "retired"


def _content_hash(doc: Dict[str, Any]) -> str:
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"expected JSON object in {path}")
    return data


def _pg_connect():
    import psycopg

    host = os.environ.get("PGHOST") or os.environ.get("POSTGRES_HOST")
    user = os.environ.get("PGUSER") or os.environ.get("POSTGRES_USER")
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD")
    dbname = os.environ.get("PGDATABASE") or os.environ.get("POSTGRES_DB") or "postgres"
    port = os.environ.get("PGPORT") or os.environ.get("POSTGRES_PORT") or "5432"
    sslmode = os.environ.get("PGSSLMODE") or "require"
    if not host or not user or not password:
        raise SystemExit("Set PGHOST/PGUSER/PGPASSWORD (or POSTGRES_*) for --backend postgres")
    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode=sslmode,
    )


def _upsert_pg(conn, kind: str, name: str, version: str, status: str, document: Dict[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM cfg.cfg_repo_upsert(%s, %s, %s, %s, %s::jsonb)",
            (kind, name, version, status, json.dumps(document)),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"cfg_repo_upsert returned no id for {kind}:{name}@{version}")
        row_id = int(row[0])
        if status == "published":
            cur.execute(
                "SELECT id FROM cfg.cfg_repo_publish(%s, %s, %s)",
                (kind, name, version),
            )
            cur.fetchone()
        return row_id


def _bind_assay_pg(conn, proc_name: str, version: str, analyte: Optional[str], profile: Optional[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM cfg.cfg_repo_bind_assay_procedure(%s, %s, %s, %s, %s, %s)",
            (proc_name, version, analyte, profile, None, None),
        )
        cur.fetchall()


def _backfill_pg(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT cfg.cfg_repo_backfill_study_analyte_defaults()")
        cur.fetchone()


def _iter_analytes(repo: Path) -> List[Tuple[str, str, Dict[str, Any], str]]:
    out: List[Tuple[str, str, Dict[str, Any], str]] = []
    root = repo / "workflow_engine" / "domain" / "analytes"
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.analyte.json")):
        doc = _load_json(path)
        name = str(doc.get("name") or path.name.replace(".analyte.json", ""))
        version = str(doc.get("version") or "1")
        status = _cfg_status(doc.get("catalog"))
        out.append((name, version, doc, status))
    return out


def _iter_profiles(repo: Path) -> List[Tuple[str, str, Dict[str, Any], str]]:
    out: List[Tuple[str, str, Dict[str, Any], str]] = []
    root = repo / "workflow_engine" / "domain" / "profiles"
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.profile.json")):
        if path.name.endswith(".example.json") or path.name.startswith("site_"):
            continue
        doc = _load_json(path)
        name = str(
            doc.get("pipelineProfile")
            or doc.get("name")
            or path.name.replace(".profile.json", "")
        )
        version = str(doc.get("version") or "1")
        status = _cfg_status(doc.get("catalog"))
        out.append((name, version, doc, status))
    return out


def _iter_procedures(repo: Path) -> List[Tuple[str, str, Dict[str, Any], str, Optional[str], Optional[str]]]:
    out: List[Tuple[str, str, Dict[str, Any], str, Optional[str], Optional[str]]] = []
    root = repo / "workflow_engine" / "domain" / "profiles" / "procedures"
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.procedure.json")):
        doc = _load_json(path)
        name = str(
            doc.get("pipelineProcedure")
            or doc.get("name")
            or path.name.replace(".procedure.json", "")
        )
        version = str(doc.get("version") or "1")
        status = _cfg_status(doc.get("catalog"))
        analyte = doc.get("analyteExpectation") or doc.get("primaryAnalyte") or doc.get("analyte")
        profile = doc.get("pipelineProfile")
        out.append(
            (
                name,
                version,
                doc,
                status,
                str(analyte) if analyte else None,
                str(profile) if profile else None,
            )
        )
    return out


def sync_postgres(repo: Path, *, skip_seed: bool) -> Dict[str, Any]:
    conn = _pg_connect()
    counts = {"analyte": 0, "pipeline_profile": 0, "assay_procedure": 0, "bound": 0}
    try:
        for name, version, doc, status in _iter_analytes(repo):
            _upsert_pg(conn, "analyte", name, version, status, doc)
            counts["analyte"] += 1
        for name, version, doc, status in _iter_profiles(repo):
            _upsert_pg(conn, "pipeline_profile", name, version, status, doc)
            counts["pipeline_profile"] += 1
        for name, version, doc, status, analyte, profile in _iter_procedures(repo):
            _upsert_pg(conn, "assay_procedure", name, version, status, doc)
            counts["assay_procedure"] += 1
            if analyte or profile:
                _bind_assay_pg(conn, name, version, analyte, profile)
                counts["bound"] += 1
        _backfill_pg(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    seed_info: Dict[str, Any] = {"skipped": True}
    if not skip_seed:
        seed_info = _seed_actions(repo)
    return {"backend": "postgres", "upserts": counts, "seed": seed_info}


def _seed_actions(repo: Path) -> Dict[str, Any]:
    import subprocess

    script = repo / "workflow_engine" / "sql_pg" / "seed_action_catalog.py"
    if not script.is_file():
        return {"skipped": True, "reason": "seed_action_catalog.py missing"}
    proc = subprocess.run(
        [sys.executable, str(script), "--use-db"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    return {
        "skipped": False,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("postgres",),
        default="postgres",
        help="Database backend (PostgreSQL only)",
    )
    parser.add_argument("--skip-seed", action="store_true", help="Skip wf.workflow_action seed")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    result = sync_postgres(repo, skip_seed=args.skip_seed)
    print(json.dumps(result, indent=2))
    if not args.skip_seed and result.get("seed", {}).get("returncode", 0) not in (0, None):
        return int(result["seed"]["returncode"] or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
