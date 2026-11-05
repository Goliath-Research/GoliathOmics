"""R5 PR1: omics_lab_inv bridge to GoliathApp inv."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pg_env(db_name: str) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", db_name))
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
def pg_lab_inv_env():
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
    env = _pg_env("goliath_omics_lab_inv_test")
    try:
        _psql("SELECT 1", env)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable")
    subprocess.run(["bash", str(app_sql / "apply_schema.sh")], env=env, check=True, cwd=str(app_sql))
    subprocess.run(["bash", str(omics_apply)], env=env, check=True, cwd=str(omics_apply.parent))
    return env


def _create_user(env: dict[str, str]) -> int:
    email = f"labinv-{uuid.uuid4().hex[:8]}@test"
    return int(
        _psql(
            f"""
            INSERT INTO "RBAC"."Users" ("FullName", email, "Active")
            VALUES ('Lab Inv', '{email}', 1)
            RETURNING "ID"
            """,
            env,
        )
    )


def _create_org(env: dict[str, str], user_id: int) -> int:
    name = f"LabInv Org {uuid.uuid4().hex[:6]}"
    escaped = name.replace("'", "''")
    org_id = int(_psql(f"SELECT * FROM portal.sp_organization_create('{escaped}')", env).split("|")[0])
    _psql(f"SELECT inserted FROM portal.sp_organization_add_member({org_id}, {user_id})", env)
    return org_id


def _seed_inv(env: dict[str, str], org_id: int) -> dict[str, int]:
    item_id = int(
        _psql(
            f"""
            INSERT INTO inv.item (organization_id, item_number, description, base_unit)
            VALUES ({org_id}, 'KIT-{uuid.uuid4().hex[:4]}', 'Methyl kit', 'ea')
            RETURNING item_id
            """,
            env,
        )
    )
    supplier_id = int(
        _psql(
            f"""
            INSERT INTO inv.supplier (organization_id, supplier_code, name)
            VALUES ({org_id}, 'SUP1', 'Reagent vendor')
            RETURNING supplier_id
            """,
            env,
        )
    )
    _psql(
        f"""
        INSERT INTO inv.approved_supplier (organization_id, supplier_id, item_id)
        VALUES ({org_id}, {supplier_id}, {item_id})
        ON CONFLICT DO NOTHING
        """,
        env,
    )
    _psql(
        f"""
        INSERT INTO inv.supplier_item (
          organization_id, supplier_id, item_id, supplier_part_number
        ) VALUES ({org_id}, {supplier_id}, {item_id}, 'PART-1')
        ON CONFLICT DO NOTHING
        """,
        env,
    )
    location_id = int(
        _psql(
            f"""
            INSERT INTO inv.location (organization_id, location_code, name)
            VALUES ({org_id}, 'LAB-A', 'Lab store')
            RETURNING location_id
            """,
            env,
        )
    )
    bin_id = int(
        _psql(
            f"""
            INSERT INTO inv.bin (location_id, bin_code, name)
            VALUES ({location_id}, 'BIN-1', 'Shelf')
            RETURNING bin_id
            """,
            env,
        )
    )
    po_id = int(
        _psql(
            f"""
            INSERT INTO inv.purchase_order (organization_id, supplier_id, po_number, status)
            VALUES ({org_id}, {supplier_id}, 'PO-{uuid.uuid4().hex[:6]}', 'open')
            RETURNING purchase_order_id
            """,
            env,
        )
    )
    po_line_id = int(
        _psql(
            f"""
            INSERT INTO inv.purchase_order_line (
              purchase_order_id, line_number, item_id, qty_ordered, unit_price
            ) VALUES ({po_id}, 1, {item_id}, 100, 12.50)
            RETURNING po_line_id
            """,
            env,
        )
    )
    return {"item_id": item_id, "bin_id": bin_id, "po_line_id": po_line_id}


def test_receive_and_consume_for_batch(pg_lab_inv_env: dict[str, str]) -> None:
    env = pg_lab_inv_env
    user_id = _create_user(env)
    org_id = _create_org(env, user_id)
    inv = _seed_inv(env, org_id)
    lab_id = _psql(
        """
        INSERT INTO portal."Labs" ("Name")
        VALUES ('Bridge test lab')
        RETURNING "ID"
        """,
        env,
    )
    batch_id = _psql(
        f"""
        INSERT INTO portal."Batches" ("Name", "LabID")
        VALUES ('Batch-1', {lab_id})
        RETURNING "ID"
        """,
        env,
    )
    payload = json.dumps(
        {
            "organization_id": org_id,
            "lab_id": int(lab_id),
            "po_line_id": inv["po_line_id"],
            "bin_id": inv["bin_id"],
            "qty": 5,
            "lot_number": "LOT-A",
        }
    )
    row = _psql(
        f"""
        SELECT receipt_id, stock_lot_id, avg_unit_cost
        FROM omics_lab_inv.sp_receive_for_lab(
          {org_id},
          {user_id},
          {lab_id},
          {inv['po_line_id']},
          {inv['bin_id']},
          5,
          'LOT-A',
          NULL
        )
        """,
        env,
    )
    receipt_id, stock_lot_id, _avg = row.split("|")
    assert int(receipt_id) > 0 and int(stock_lot_id) > 0
    linked = _psql(
        f"""
        SELECT count(*) FROM omics_lab_inv.lab_stock_context
        WHERE stock_lot_id = {stock_lot_id} AND lab_id = {lab_id}
        """,
        env,
    )
    assert int(linked) == 1
    consume = _psql(
        f"""
        SELECT stock_move_id, qty_on_hand
        FROM omics_lab_inv.sp_consume_for_batch(
          {org_id}, {user_id}, {batch_id}, {stock_lot_id}, 2
        )
        """,
        env,
    )
    move_id, qty_on_hand = consume.split("|")
    assert int(move_id) > 0
    assert float(qty_on_hand) == 3.0
