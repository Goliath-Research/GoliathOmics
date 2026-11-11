"""Portal host package: GoliathApp baseline + Omics SQL on fresh Postgres."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE_REF_FILE = REPO / "database" / "portal_host_package" / "goliathapp_baseline.ref"
PACKAGE_APPLY = REPO / "database" / "portal_host_package" / "apply_package.sh"
TEST_DB = "goliathomics_portal_host_test"


def _app_sql_dir() -> Path:
    override = os.environ.get("GOLIATH_APP_SQL_DIR")
    if override:
        return Path(override)
    pin = BASELINE_REF_FILE.read_text(encoding="utf-8").strip()
    checkout = REPO / "_goliathapp_portal" / "workflow_engine" / "sql_pg"
    if checkout.is_dir():
        return checkout
    raise FileNotFoundError(
        f"GoliathApp sql_pg not found; set GOLIATH_APP_SQL_DIR or check out ref {pin} to _goliathapp_portal"
    )


def _pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", "methyl"))
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


def _psql_file(path: Path, env: dict[str, str]) -> None:
    subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)], env=env, check=True)


@pytest.fixture(scope="module")
def portal_host_env() -> dict[str, str]:
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = _app_sql_dir()
    apply_sh = app_sql / "apply_schema.sh"
    nav_seed = app_sql / "seed_platform_nav_tree.sql"
    if not apply_sh.is_file():
        pytest.skip(f"GoliathApp apply_schema.sh missing at {apply_sh}")
    if not nav_seed.is_file():
        pytest.skip(f"seed_platform_nav_tree.sql missing at {nav_seed}")
    if not PACKAGE_APPLY.is_file():
        pytest.skip("portal host apply_package.sh missing")

    admin = _pg_env()
    admin["PGDATABASE"] = "postgres"
    try:
        _psql("SELECT 1", admin)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable")
    _psql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)', admin)
    _psql(f'CREATE DATABASE "{TEST_DB}"', admin)
    env = _pg_env()
    env["PGDATABASE"] = TEST_DB
    subprocess.run(["bash", str(apply_sh)], env=env, check=True, cwd=str(app_sql))
    email = f"portal-pkg-{uuid.uuid4().hex[:8]}@test"
    user_id = int(
        _psql(
            f"""
            INSERT INTO "RBAC"."Users" ("FullName", email, "Active")
            VALUES ('Portal Pkg', '{email}', 1) RETURNING "ID"
            """,
            env,
        )
    )
    org_row = _psql(f"SELECT * FROM portal.sp_organization_create('PortalPkg-{user_id}')", env)
    org_id = int(org_row.split("|")[0])
    _psql(f"SELECT grant_id FROM portal.sp_organization_set_admin({org_id}, {user_id})", env)
    _psql_file(nav_seed, env)
    subprocess.run(["bash", str(PACKAGE_APPLY)], env=env, check=True)
    return env


def test_omics_sample_subclasses_platform_material(portal_host_env: dict[str, str]) -> None:
    parent = _psql(
        """
        SELECT parent."Name"
        FROM "Meta"."Classes" child
        INNER JOIN "Meta"."Classes" parent ON parent."ID" = child."ParentID"
        WHERE child."Name" = 'omics/Sample'
        """,
        portal_host_env,
    )
    assert parent == "platform/Material"


def test_omics_nav_registered(portal_host_env: dict[str, str]) -> None:
    count = int(
        _psql(
            """SELECT COUNT(*) FROM portal."NavTree" WHERE "NodePath" LIKE '/app/omics%'""",
            portal_host_env,
        )
    )
    assert count >= 5


def test_god_architect_nav_and_permissions_superset(portal_host_env: dict[str, str]) -> None:
    god_id = int(_psql("SELECT portal.fn_god_architect_role_id()", portal_host_env))
    admin_id = int(
        _psql(
            """SELECT "ID" FROM "RBAC"."Roles" WHERE "Name" = 'organization_admin' LIMIT 1""",
            portal_host_env,
        )
    )
    admin_perms = _psql(
        f"""
        SELECT string_agg(p."Name", ',' ORDER BY p."Name")
        FROM "RBAC"."Role_Permissions" rp
        INNER JOIN "RBAC"."Permissions" p ON p."ID" = rp."PermissionID"
        WHERE rp."RoleID" = {admin_id}
        """,
        portal_host_env,
    )
    god_perms = _psql(
        f"""
        SELECT string_agg(p."Name", ',' ORDER BY p."Name")
        FROM "RBAC"."Role_Permissions" rp
        INNER JOIN "RBAC"."Permissions" p ON p."ID" = rp."PermissionID"
        WHERE rp."RoleID" = {god_id}
        """,
        portal_host_env,
    )
    assert admin_perms
    assert god_perms
    assert set(admin_perms.split(",")) <= set(god_perms.split(","))
    assert "platform.model.architect" in god_perms.split(",")

    admin_nav = int(
        _psql(f'SELECT COUNT(*) FROM portal."Role2Node" WHERE "RoleId" = {admin_id}', portal_host_env)
    )
    god_nav = int(
        _psql(f'SELECT COUNT(*) FROM portal."Role2Node" WHERE "RoleId" = {god_id}', portal_host_env)
    )
    assert god_nav >= admin_nav > 0
