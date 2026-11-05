"""R4 PR3: Meta backfill and rollback API."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg_backfill_env():
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
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_backfill_test"))
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


def test_backfill_and_rollback_disease(pg_backfill_env: dict[str, str]) -> None:
    env = pg_backfill_env
    pending_before = int(_psql("SELECT omics_clinical.fn_meta_backfill_count('disease')", env))
    suffix = uuid.uuid4().hex[:8]
    disease_id = _psql(
        f"""
        INSERT INTO portal."Diseases" ("Name")
        VALUES ('backfill-{suffix}')
        RETURNING "ID"
        """,
        env,
    )
    before_sum = _psql("SELECT omics_clinical.fn_meta_legacy_checksum('disease')", env)
    assert int(_psql("SELECT omics_clinical.fn_meta_backfill_count('disease')", env)) == pending_before + 1
    mirrored = _psql("SELECT omics_clinical.sp_meta_backfill_entity('disease')", env)
    assert int(mirrored) >= 1
    assert _psql("SELECT omics_clinical.fn_meta_backfill_count('disease')", env) == "0"
    after_sum = _psql("SELECT omics_clinical.fn_meta_legacy_checksum('disease')", env)
    assert before_sum == after_sum
    meta_id = _psql(f'SELECT "MetaObjectID" FROM portal."Diseases" WHERE "ID" = {disease_id}', env)
    assert meta_id.isdigit()
    cleared = _psql("SELECT omics_clinical.sp_meta_rollback_entity('disease')", env)
    assert int(cleared) >= 1
    assert _psql(f'SELECT "MetaObjectID" FROM portal."Diseases" WHERE "ID" = {disease_id}', env) == ""
