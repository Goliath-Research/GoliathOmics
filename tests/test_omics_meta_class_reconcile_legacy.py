"""Legacy Omics* root classes must reconcile under omics/* without breaking VOmics* views."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "database" / "schema"


@pytest.fixture(scope="module")
def pg_legacy_reconcile_env():
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = Path(
        os.environ.get(
            "GOLIATH_APP_SQL_DIR",
            REPO_ROOT / "_goliathapp_portal" / "workflow_engine" / "sql_pg",
        )
    )
    if not (app_sql / "apply_schema.sh").is_file():
        pytest.skip("GoliathApp apply_schema.sh missing")
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_legacy_reconcile_test"))
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
    pre_fifteen = [
        "01_portal_clinical_schema.sql",
        "02_portal_sample_extras_schema.sql",
        "03_legacy_cross_schema_fks.sql",
        "04_portal_clinical_api.sql",
        "05_portal_clinical_api_parity.sql",
        "06_portal_clinical_nav.sql",
        "07_cfg_analyte_catalog.sql",
        "08_omics_semantic_mvp.sql",
        "09_portal_semantic_row_types.sql",
        "10_omics_meta_clinical_classes.sql",
        "11_omics_meta_semantic_writes.sql",
        "12_omics_meta_backfill_api.sql",
        "13_omics_lab_inv_bridge.sql",
        "14_omics_inv_semantic.sql",
    ]
    for name in pre_fifteen:
        subprocess.run(
            ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA_DIR / name)],
            env=env,
            check=True,
        )
    legacy_pairs = (
        ("disease", "OmicsDisease"),
        ("customer", "OmicsCustomer"),
        ("patient", "OmicsPatient"),
        ("sample", "OmicsSample"),
        ("lab_batch", "OmicsLabBatch"),
    )
    for entity_code, class_name in legacy_pairs:
        subprocess.run(
            [
                "psql",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"""
                WITH ins AS (
                  INSERT INTO "Meta"."Classes" ("Name", "TableName")
                  SELECT '{class_name}', '{class_name}'
                  WHERE NOT EXISTS (
                    SELECT 1 FROM "Meta"."Classes" c WHERE c."Name" = '{class_name}'
                  )
                  RETURNING "ID"
                ),
                cls AS (
                  SELECT "ID" FROM ins
                  UNION ALL
                  SELECT "ID" FROM "Meta"."Classes" WHERE "Name" = '{class_name}' LIMIT 1
                )
                INSERT INTO omics_clinical.meta_class_registry (entity_code, class_id, class_name)
                SELECT '{entity_code}', cls."ID", '{class_name}'
                FROM cls
                ON CONFLICT (entity_code) DO UPDATE SET
                  class_id = EXCLUDED.class_id,
                  class_name = EXCLUDED.class_name;
                """,
            ],
            env=env,
            check=True,
        )
    for name in (
        "15_omics_meta_domain_classes.sql",
        "16_omics_portal_host_nav.sql",
        "17_omics_meta_clinical_class_reconcile.sql",
    ):
        subprocess.run(
            ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA_DIR / name)],
            env=env,
            check=True,
        )
    subprocess.run(
        [sys.executable, "-m", "goliath_omics.seed.meta_classes"],
        env={**env, "PYTHONPATH": str(REPO_ROOT / "src")},
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


def test_legacy_omics_classes_reconcile_and_vomics_views_exist(pg_legacy_reconcile_env: dict[str, str]) -> None:
    env = pg_legacy_reconcile_env
    for view in (
        "VOmicsDisease",
        "VOmicsCustomer",
        "VOmicsPatient",
        "VOmicsSample",
        "VOmicsLabBatch",
    ):
        exists = _psql(
            f"""
            SELECT to_regclass('"Meta"."{view}"') IS NOT NULL
            """,
            env,
        )
        assert exists == "t", view
    legacy_parent = _psql(
        """
        SELECT parent."Name"
        FROM "Meta"."Classes" legacy
        JOIN "Meta"."Classes" parent ON parent."ID" = legacy."ParentID"
        WHERE legacy."Name" = 'OmicsDisease'
        """,
        env,
    )
    assert legacy_parent == "omics/Disease"
    legacy_table = _psql(
        """SELECT "TableName" FROM "Meta"."Classes" WHERE "Name" = 'OmicsDisease'""",
        env,
    )
    assert legacy_table == "OmicsDiseaseLegacy"
