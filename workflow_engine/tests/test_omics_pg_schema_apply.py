"""Apply GoliathApp then GoliathOmics PostgreSQL schema twice (idempotent)."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from shutil import which

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_SQL = Path(os.environ.get("GOLIATH_APP_SQL_DIR", REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg"))
OMICS_SQL = REPO_ROOT / "workflow_engine" / "sql_pg"
APP_APPLY = APP_SQL / "apply_schema.sh"
OMICS_APPLY = OMICS_SQL / "apply_schema.sh"


def _pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_schema_test"))
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    env.setdefault("PGPASSWORD", os.environ.get("PGPASSWORD", "methyl"))
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


@pytest.fixture(scope="module")
def pg_schema_applied() -> dict[str, str]:
    if not which("psql"):
        pytest.skip("psql not installed")
    if not APP_APPLY.is_file():
        pytest.skip(f"GoliathApp apply_schema.sh not found at {APP_APPLY}")
    if not OMICS_APPLY.is_file():
        pytest.skip(f"Omics apply_schema.sh not found at {OMICS_APPLY}")
    env = _pg_env()
    try:
        _psql("SELECT 1", env)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable for schema apply test")
    for _ in range(2):
        subprocess.run(["bash", str(APP_APPLY)], env=env, check=True, cwd=str(APP_SQL))
        subprocess.run(["bash", str(OMICS_APPLY)], env=env, check=True, cwd=str(OMICS_SQL))
    return env


def test_many_customers_per_organization(pg_schema_applied: dict[str, str]) -> None:
    env = pg_schema_applied
    org_row = _psql("SELECT * FROM portal.sp_organization_create('Omics P1b org')", env)
    org_id, _scope_id = org_row.split("|")
    suffix = uuid.uuid4().hex[:8]
    c1 = _psql(
        f"""
        INSERT INTO portal."Customers" ("Name", "OrganizationID")
        VALUES ('cust-a-{suffix}', {org_id})
        RETURNING "ID"
        """,
        env,
    )
    c2 = _psql(
        f"""
        INSERT INTO portal."Customers" ("Name", "OrganizationID")
        VALUES ('cust-b-{suffix}', {org_id})
        RETURNING "ID"
        """,
        env,
    )
    assert int(c1) > 0 and int(c2) > 0 and c1 != c2
    count = _psql(
        f'SELECT count(*) FROM portal."Customers" WHERE "OrganizationID" = {org_id}',
        env,
    )
    assert int(count) >= 2


def test_clinical_nav_and_disease_list(pg_schema_applied: dict[str, str]) -> None:
    env = pg_schema_applied
    diseases = _psql("SELECT count(*) FROM portal.sp_list_diseases()", env)
    assert diseases.isdigit()
    nav = _psql(
        "SELECT count(*) FROM portal.\"NavTree\" n "
        "JOIN portal.\"NodeInfos\" i ON i.\"Id\" = n.\"InfoID\" "
        "WHERE i.\"Name\" = 'omics_clinical'",
        env,
    )
    assert int(nav) >= 1
