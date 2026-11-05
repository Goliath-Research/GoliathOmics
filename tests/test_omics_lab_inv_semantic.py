"""R5 PR2: lab reagent semantic requests."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.test_omics_lab_inv_bridge import (
    _create_org,
    _create_user,
    _pg_env,
    _psql,
    _seed_inv,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pg_lab_semantic_env():
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
    env = _pg_env("goliath_omics_lab_inv_semantic_test")
    try:
        _psql("SELECT 1", env)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable")
    subprocess.run(["bash", str(app_sql / "apply_schema.sh")], env=env, check=True, cwd=str(app_sql))
    subprocess.run(["bash", str(omics_apply)], env=env, check=True, cwd=str(omics_apply.parent))
    return env


def test_lab_reagent_list_after_receive(pg_lab_semantic_env: dict[str, str]) -> None:
    env = pg_lab_semantic_env
    user_id = _create_user(env)
    org_id = _create_org(env, user_id)
    inv = _seed_inv(env, org_id)
    lab_id = int(
        _psql(
            """INSERT INTO portal."Labs" ("Name") VALUES ('Semantic lab') RETURNING "ID" """,
            env,
        )
    )
    _psql(
        f"""
        SELECT stock_lot_id FROM omics_lab_inv.sp_receive_for_lab(
          {org_id}, {user_id}, {lab_id}, {inv['po_line_id']}, {inv['bin_id']}, 4, 'LOT-S', NULL
        )
        """,
        env,
    )
    params = json.dumps({"organization_id": org_id, "lab_id": lab_id})
    count = _psql(
        f"""
        SELECT count(*) FROM omics_lab_inv.fn_semantic_lab_reagent_list(
          {user_id}, '{params}'::jsonb, 100
        )
        """,
        env,
    )
    assert int(count) >= 1
