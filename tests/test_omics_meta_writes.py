"""R4 PR2: semantic write paths and Meta dual-write."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg_meta_writes_env():
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = Path(
        os.environ.get(
            "GOLIATH_APP_SQL_DIR",
            REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg",
        )
    )
    omics_apply = REPO_ROOT / "workflow_engine/sql_pg/apply_schema.sh"
    if not (app_sql / "apply_schema.sh").is_file() or not omics_apply.is_file():
        pytest.skip("schema apply scripts missing")
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_meta_writes_test"))
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    env.setdefault("PGPASSWORD", os.environ.get("PGPASSWORD", "methyl"))
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", "SELECT 1"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("PostgreSQL not reachable")
    subprocess.run(["bash", str(app_sql / "apply_schema.sh")], env=env, check=True, cwd=str(app_sql))
    subprocess.run(["bash", str(omics_apply)], env=env, check=True, cwd=str(omics_apply.parent))
    subprocess.run(
        [sys.executable, "-m", "goliath_omics.seed.meta_classes"],
        env=env,
        check=True,
        cwd=str(REPO_ROOT),
    )
    return env


def _psql(sql: str, env: dict[str, str]) -> str:
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def test_disease_create_dual_writes_meta_when_flag_on(pg_meta_writes_env: dict[str, str]) -> None:
    env = pg_meta_writes_env
    suffix = uuid.uuid4().hex[:8]
    _psql("CALL omics_clinical.sp_meta_set_cutover_flag('disease', true, false)", env)
    payload = json.dumps({"name": f"dual-disease-{suffix}"})
    row = _psql(
        f"""
        SELECT (r).disease_id, (r).name
        FROM (
          SELECT omics_clinical.fn_semantic_disease_create(1, '{payload}'::jsonb) AS r
        ) q
        """,
        env,
    )
    disease_id, name = row.split("|")
    assert int(disease_id) > 0
    meta_id = _psql(
        f'SELECT "MetaObjectID" FROM portal."Diseases" WHERE "ID" = {disease_id}',
        env,
    )
    assert meta_id.isdigit() and int(meta_id) > 0
    _psql("CALL omics_clinical.sp_meta_set_cutover_flag('disease', false, false)", env)


def test_disease_list_read_from_meta_view(pg_meta_writes_env: dict[str, str]) -> None:
    env = pg_meta_writes_env
    suffix = uuid.uuid4().hex[:8]
    disease_id = _psql(
        f"""
        INSERT INTO portal."Diseases" ("Name")
        VALUES ('read-meta-{suffix}')
        RETURNING "ID"
        """,
        env,
    )
    _psql(f"SELECT omics_clinical.sp_meta_mirror_disease({disease_id})", env)
    _psql("CALL omics_clinical.sp_meta_set_cutover_flag('disease', false, true)", env)
    count = _psql(
        f"""
        SELECT count(*) FROM omics_clinical.fn_semantic_disease_list(
          1,
          '{{}}'::jsonb,
          500
        ) WHERE name = 'read-meta-{suffix}'
        """,
        env,
    )
    assert int(count) == 1
    _psql("CALL omics_clinical.sp_meta_set_cutover_flag('disease', false, false)", env)
