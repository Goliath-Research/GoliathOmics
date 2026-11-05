"""Meta clinical class registration (R4 PR1)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg_meta_classes_env():
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = Path(
        os.environ.get(
            "GOLIATH_APP_SQL_DIR",
            REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg",
        )
    )
    app_apply = app_sql / "apply_schema.sh"
    omics_apply = REPO_ROOT / "workflow_engine/sql_pg/apply_schema.sh"
    if not app_apply.is_file() or not omics_apply.is_file():
        pytest.skip("schema apply scripts missing")
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_meta_test"))
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
    subprocess.run(["bash", str(app_apply)], env=env, check=True, cwd=str(app_sql))
    subprocess.run(["bash", str(omics_apply)], env=env, check=True, cwd=str(omics_apply.parent))
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


def test_meta_class_registry_seeded(pg_meta_classes_env: dict[str, str]) -> None:
    env = pg_meta_classes_env
    count = _psql("SELECT count(*) FROM omics_clinical.meta_class_registry", env)
    assert int(count) == 5


def test_meta_cutover_flags_default_off(pg_meta_classes_env: dict[str, str]) -> None:
    env = pg_meta_classes_env
    row = _psql(
        """
        SELECT count(*) FROM omics_clinical.meta_cutover_flag
        WHERE dual_write_legacy_meta OR read_from_meta
        """,
        env,
    )
    assert int(row) == 0


def test_semantic_entities_linked_to_meta(pg_meta_classes_env: dict[str, str]) -> None:
    env = pg_meta_classes_env
    count = _psql(
        """
        SELECT count(*)
        FROM omics_clinical.meta_class_registry r
        JOIN portal.semantic_entity e ON e.id = r.semantic_entity_id
        WHERE e.meta_class_id = r.class_id
          AND e.namespace = 'omics'
        """,
        env,
    )
    assert int(count) == 5


def test_legacy_meta_object_id_columns_exist(pg_meta_classes_env: dict[str, str]) -> None:
    env = pg_meta_classes_env
    cols = _psql(
        """
        SELECT count(DISTINCT c.relname)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'portal'
          AND a.attname = 'MetaObjectID'
          AND c.relname IN ('Customers', 'Diseases', 'Patients', 'Samples', 'Batches')
        """,
        env,
    )
    assert int(cols) == 5
